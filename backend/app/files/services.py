"""Process-wide wiring of the file features, and where task outputs are saved (ADR-0022).

``FileServices`` bundles the folder service, the upload store and the audit hook. ``create_app``
installs one with :func:`configure`; the API reads it from ``app.state``, while the task workers,
the agents and the tools (which have no request) read the installed one with :func:`current`.
Tests install fakes with :func:`configure` and remove them with :func:`reset`.

``OutputTarget.resolve`` answers "where does this user's result go?": the connected folder when
it is usable, else MAYA's artifacts store (with a warning when the folder is set but unusable).
A result is never lost because the folder failed.
"""

from __future__ import annotations

import logging
import os
import shutil
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncEngine

from app.automation.paths import clean_stem
from app.core.config import Settings
from app.db.repositories.user_settings import UserSettingsStore
from app.db.tenant import tenant_session
from app.files.folder import FolderService, FolderSettings
from app.files.safepath import SafePathError, split_relative
from app.files.uploads import UploadStore
from app.services.audit_service import record_audit

logger = logging.getLogger(__name__)

MAX_COLLISION_TRIES = 500


@dataclass
class FileServices:
    """The folder service, the upload store and the audit hook for one deployment."""

    folders: FolderService
    uploads: UploadStore
    artifacts_dir: Path
    engine: AsyncEngine | None = None

    async def audit(
        self,
        *,
        workspace_id: UUID,
        user_id: UUID,
        tool: str,
        level: str,
        detail: dict[str, Any],
        task_id: UUID | None = None,
        status: str = "success",
    ) -> None:
        """Append one audit row for a file write. Best effort: a failed audit is logged, not
        raised (the write already happened and its result is reported to the user)."""
        if self.engine is None:
            return
        try:
            async with tenant_session(self.engine, workspace_id, user_id) as session:
                await record_audit(
                    session,
                    workspace_id=workspace_id,
                    user_ref=str(user_id),
                    status=status,
                    server_name="maya-tools",
                    tool_name=tool,
                    permission_level=level,
                    task_id=task_id,
                    input=detail,
                )
                await session.commit()
        except Exception as error:
            logger.warning("file_audit_failed tool=%s error=%s", tool, type(error).__name__)


def build_file_services(settings: Settings, engine: AsyncEngine | None) -> FileServices:
    folder_settings: FolderSettings
    if engine is not None:
        folder_settings = UserSettingsStore(engine)
    else:  # no database (some test apps): the folder cannot be stored
        folder_settings = _NoSettings()
    artifacts = Path(settings.artifacts_dir).resolve()
    return FileServices(
        folders=FolderService(folder_settings, artifacts_dir=artifacts),
        uploads=UploadStore(
            artifacts,
            max_bytes=settings.upload_max_bytes,
            total_max_bytes=settings.upload_total_max_bytes,
        ),
        artifacts_dir=artifacts,
        engine=engine,
    )


class _NoSettings:
    async def get_workspace_folder(self, workspace_id: UUID, user_id: UUID) -> str | None:
        return None

    async def set_workspace_folder(
        self, workspace_id: UUID, user_id: UUID, path: str | None
    ) -> None:
        raise RuntimeError("no database is configured")


_current: FileServices | None = None


def configure(services: FileServices | None) -> None:
    global _current
    _current = services


def reset() -> None:
    configure(None)


def current() -> FileServices | None:
    return _current


# ------------------------------------------------------------------------- output routing


@dataclass(frozen=True, slots=True)
class OutputDestination:
    """Where a result is saved. ``folder`` is set only for ``mode == "folder"``."""

    mode: Literal["folder", "artifacts"]
    folder: Path | None = None
    warning: str | None = None


class OutputTarget:
    """``resolve(workspace, user)``: the connected folder when usable, else the artifacts store."""

    def __init__(self, services: FileServices | None = None) -> None:
        self._services = services if services is not None else current()

    async def resolve(self, workspace_id: UUID, user_id: UUID) -> OutputDestination:
        services = self._services
        if services is None:
            return OutputDestination("artifacts")
        try:
            info = await services.folders.get(workspace_id, user_id)
            if info.status == "none":
                return OutputDestination("artifacts")
            root = await services.folders.usable_root(workspace_id, user_id)
        except Exception as error:  # settings unreadable: fall back, never lose the file
            logger.warning("output_target_unreadable error=%s", type(error).__name__)
            return OutputDestination(
                "artifacts",
                warning=(
                    "Your connected folder could not be checked, so the file was saved in "
                    "MAYA's storage instead."
                ),
            )
        if root is None:
            reason = {
                "missing": "is missing",
                "not_writable": "is not writable",
            }.get(info.status, "cannot be used")
            return OutputDestination(
                "artifacts",
                warning=(
                    f"Your connected folder '{info.name}' {reason}, so the file was saved in "
                    "MAYA's storage instead. Reconnect the folder to save there."
                ),
            )
        return OutputDestination("folder", folder=root)


def safe_output_name(name: str, fallback: str = "output") -> str:
    """A single plain file name from model-chosen text (never a path, never reserved)."""
    base = name.replace("\\", "/").rsplit("/", 1)[-1].strip()
    try:
        split_relative(base)
        return base
    except SafePathError:
        stem, dot, ext = base.rpartition(".")
        if not dot or not stem or len(ext) > 8:
            stem, ext = base, ""
        cleaned = clean_stem(stem, fallback=fallback)
        return f"{cleaned}.{ext.lower()}" if ext and ext.isalnum() else cleaned


def collision_safe_copy(source: Path, directory: Path, name: str) -> Path:
    """Copy ``source`` into ``directory`` as ``name``, or ``name (2).ext``, ``(3)`` ... Never
    overwrites: the destination is created exclusively."""
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    suffix = f".{ext}" if ext else ""
    for number in range(1, MAX_COLLISION_TRIES + 1):
        candidate = directory / (f"{stem}{suffix}" if number == 1 else f"{stem} ({number}){suffix}")
        try:
            with open(candidate, "xb") as out, open(source, "rb") as src:
                shutil.copyfileobj(src, out)
            return candidate
        except FileExistsError:
            continue
    raise OSError("no free file name in the folder")


def collision_safe_write(directory: Path, name: str, data: bytes) -> Path:
    """Create a new file ``name`` (or ``name (2).ext`` ...) with ``data``. Never overwrites."""
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    suffix = f".{ext}" if ext else ""
    for number in range(1, MAX_COLLISION_TRIES + 1):
        candidate = directory / (f"{stem}{suffix}" if number == 1 else f"{stem} ({number}){suffix}")
        try:
            with open(candidate, "xb") as out:
                out.write(data)
            return candidate
        except FileExistsError:
            continue
    raise OSError("no free file name in the folder")


def unique_subdirectory(parent: Path, slug: str) -> Path:
    """Create ``parent/slug`` (or ``slug-2`` ...) exclusively and return it."""
    for number in range(1, MAX_COLLISION_TRIES + 1):
        candidate = parent / (slug if number == 1 else f"{slug}-{number}")
        try:
            os.mkdir(candidate)
            return candidate
        except FileExistsError:
            continue
    raise OSError("no free folder name")


def project_slug(text: str, fallback: str = "project") -> str:
    """A short lowercase folder name: ``Solar Energy Site`` -> ``solar-energy-site``."""
    lowered = text.lower()
    out = []
    previous_dash = False
    for char in lowered:
        if char.isascii() and char.isalnum():
            out.append(char)
            previous_dash = False
        elif not previous_dash and out:
            out.append("-")
            previous_dash = True
    slug = "".join(out).strip("-")[:48].strip("-")
    return clean_stem(slug, fallback=fallback).lower() if slug else fallback


async def deliver_output(
    workspace_id: UUID, user_id: UUID, source: Path, name: str | None = None
) -> tuple[Path, str | None]:
    """Save a finished file where the user wants results: ``(final path, warning)``.

    With a usable connected folder the file is COPIED into its root under a collision-safe name
    (never overwriting). Without one, or when the copy fails, ``source`` (already in the
    artifacts store) is the result and a warning says why when a folder was set. The file is
    never lost.
    """
    import asyncio

    destination = await OutputTarget().resolve(workspace_id, user_id)
    if destination.mode != "folder" or destination.folder is None:
        return source, destination.warning
    clean = safe_output_name(name or source.name, fallback="document")
    try:
        saved = await asyncio.to_thread(collision_safe_copy, source, destination.folder, clean)
    except OSError as error:
        logger.warning("output_copy_failed error=%s", type(error).__name__)
        return source, (
            "Your connected folder could not be written to, so the file was saved in MAYA's "
            "storage instead."
        )
    services = current()
    if services is not None:
        await services.audit(
            workspace_id=workspace_id,
            user_id=user_id,
            tool="file_write_new",
            level="WRITE",
            detail={"saved_as": saved.name, "via": "output_routing"},
        )
    return saved, None
