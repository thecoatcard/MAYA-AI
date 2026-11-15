"""The user's connected folder: validate, store, recompute status, resolve paths (ADR-0022).

The path is stored in the user's ``user_settings`` JSON (key ``workspace_folder``): no migration.
The stored value is only a hint; every read recomputes the status from the real filesystem, so a
deleted or locked folder reads as ``missing`` / ``not_writable`` and nothing is written to it.
The folder always comes from the CALLER's stored setting, never from a tool argument.
"""

from __future__ import annotations

import errno
import os
import sys
import uuid
from collections.abc import Callable, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Literal, Protocol
from uuid import UUID

from app.files.safepath import is_within, resolve_inside

FolderStatus = Literal["none", "ok", "missing", "not_writable"]
FOLDER_KEY = "workspace_folder"
_MAX_PATH_TEXT = 1024
_WRITE_PROBE_PREFIX = ".maya-write-test-"


class FolderSettings(Protocol):
    """Where the folder path lives (``UserSettingsStore`` in production, a fake in tests)."""

    async def get_workspace_folder(self, workspace_id: UUID, user_id: UUID) -> str | None: ...

    async def set_workspace_folder(
        self, workspace_id: UUID, user_id: UUID, path: str | None
    ) -> None: ...


class FolderError(ValueError):
    """The folder cannot be connected. ``message`` is plain words for the user."""

    def __init__(self, message: str, code: str = "invalid_folder") -> None:
        super().__init__(message)
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class FolderInfo:
    path: str | None
    status: FolderStatus
    writable: bool
    name: str | None

    def as_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "status": self.status,
            "writable": self.writable,
            "name": self.name,
        }


NONE_INFO = FolderInfo(path=None, status="none", writable=False, name=None)


def default_system_dirs() -> tuple[Path, ...]:
    """Operating-system directories that can never be the connected folder (nor inside one)."""
    found: list[Path] = []
    if sys.platform == "win32":
        for var in (
            "SystemRoot",
            "windir",
            "ProgramFiles",
            "ProgramFiles(x86)",
            "ProgramW6432",
            "ProgramData",
        ):
            value = os.environ.get(var)
            if value:
                found.append(Path(value))
        found.append(Path("C:/Windows"))
        found.append(Path("C:/Program Files"))
        found.append(Path("C:/Program Files (x86)"))
        found.append(Path("C:/ProgramData"))
    else:
        found.extend(
            Path(p)
            for p in (
                "/etc",
                "/usr",
                "/bin",
                "/sbin",
                "/lib",
                "/lib32",
                "/lib64",
                "/boot",
                "/dev",
                "/proc",
                "/sys",
                "/System",
                "/Library",
                "/Applications",
                "/private/etc",
            )
        )
    return tuple(found)


def default_protected_dirs(artifacts_dir: Path | str | None = None) -> tuple[Path, ...]:
    """MAYA's own directories: the repository, backend storage, artifacts and ``~/.maya``."""
    backend_dir = Path(__file__).resolve().parents[2]
    repo_dir = backend_dir.parent
    dirs = [repo_dir, backend_dir, backend_dir / "storage"]
    if artifacts_dir is not None:
        dirs.append(Path(artifacts_dir).resolve())
    dirs.append(Path.home() / ".maya")
    return tuple(dirs)


def _norm(path: Path) -> Path:
    return path.resolve(strict=False)


class FolderService:
    """Get, set and clear one user's folder, and resolve paths inside it safely."""

    def __init__(
        self,
        settings: FolderSettings,
        *,
        system_dirs: Sequence[Path | str] | None = None,
        protected_dirs: Sequence[Path | str] | None = None,
        home: Path | str | None = None,
        artifacts_dir: Path | str | None = None,
    ) -> None:
        self._settings = settings
        self._system = tuple(Path(p) for p in (system_dirs or default_system_dirs()))
        self._protected = tuple(
            Path(p) for p in (protected_dirs or default_protected_dirs(artifacts_dir))
        )
        self._home = Path(home) if home is not None else Path.home()

    # ------------------------------------------------------------------ validation

    def validate(self, raw: object) -> Path:
        """The normalised folder path, or :class:`FolderError` saying why not."""
        if not isinstance(raw, str) or not raw.strip():
            raise FolderError("Enter the full path of a folder.", "empty")
        text = raw.strip()
        if "\x00" in text or len(text) > _MAX_PATH_TEXT:
            raise FolderError("That is not a valid folder path.", "bad_path")
        if text.startswith(("\\\\", "//")):
            raise FolderError("Network (UNC) paths are not supported. Pick a local folder.", "unc")
        candidate = Path(text)
        if not candidate.is_absolute():
            raise FolderError(
                "The folder path must be absolute (for example C:\\Users\\you\\Work)."
            )
        try:
            resolved = _norm(candidate)
            exists = resolved.exists()
            is_dir = resolved.is_dir()
        except (OSError, ValueError):
            raise FolderError("That folder cannot be read.", "unreadable") from None
        if resolved.parent == resolved:
            raise FolderError(
                "A drive or filesystem root cannot be connected. Pick a folder inside it.", "root"
            )
        if not exists:
            raise FolderError("That folder does not exist.", "missing")
        if not is_dir:
            raise FolderError("That path is a file, not a folder.", "not_directory")
        if self._same(resolved, _norm(self._home)):
            raise FolderError(
                "Your whole user profile folder cannot be connected. Pick a folder inside it.",
                "user_profile",
            )
        for system in self._system:
            if is_within(resolved, _norm(system)):
                raise FolderError("An operating-system folder cannot be connected.", "system_dir")
        for protected in self._protected:
            if is_within(resolved, _norm(protected)):
                raise FolderError(
                    "MAYA's own folders (the app, its storage and artifacts) cannot be connected.",
                    "maya_dir",
                )
        if not self._writable(resolved):
            raise FolderError(
                "MAYA cannot write to that folder. Pick one you can edit. "
                + self._why_not_writable(resolved),
                "readonly",
            )
        return resolved

    @staticmethod
    def _why_not_writable(folder: Path) -> str:
        """A plain-language reason for the failed write test (the folder path is not repeated)."""
        probe = folder / f"{_WRITE_PROBE_PREFIX}{uuid.uuid4().hex[:12]}"
        try:
            with open(probe, "xb") as handle:
                handle.write(b"x")
        except PermissionError:
            return (
                "Windows refused the write (access denied). The folder's permissions may not "
                "allow this account to create files, or Windows Security's Controlled folder "
                "access may be blocking MAYA's engine (python.exe); allow it there, or pick a "
                "folder outside Documents, Desktop and Pictures."
            )
        except OSError as error:
            if error.errno == errno.ENOSPC:
                return "The disk is full."
            if error.errno == errno.EROFS:
                return "The location is read-only."
            return f"The system reported: {error.strerror or 'an unknown error'}."
        else:
            return "A second try worked, so it may have been a brief lock: press Connect again."
        finally:
            try:
                probe.unlink()
            except OSError:
                pass

    @staticmethod
    def _same(a: Path, b: Path) -> bool:
        return is_within(a, b) and is_within(b, a)

    @staticmethod
    def _writable(folder: Path) -> bool:
        probe = folder / f"{_WRITE_PROBE_PREFIX}{uuid.uuid4().hex[:12]}"
        try:
            with open(probe, "xb") as handle:
                handle.write(b"x")
            return True
        except OSError:
            return False
        finally:
            try:
                probe.unlink()
            except OSError:
                pass

    # ------------------------------------------------------------------ state

    def info_for(self, stored: str | None) -> FolderInfo:
        """Status recomputed from the filesystem for a stored path."""
        if not stored:
            return NONE_INFO
        path = Path(stored)
        name = path.name or str(path)
        try:
            if not path.is_dir():
                return FolderInfo(path=stored, status="missing", writable=False, name=name)
            writable = self._writable(_norm(path))
        except OSError:
            return FolderInfo(path=stored, status="missing", writable=False, name=name)
        if not writable:
            return FolderInfo(path=stored, status="not_writable", writable=False, name=name)
        return FolderInfo(path=stored, status="ok", writable=True, name=name)

    async def get(self, workspace_id: UUID, user_id: UUID) -> FolderInfo:
        return self.info_for(await self._settings.get_workspace_folder(workspace_id, user_id))

    async def set(self, workspace_id: UUID, user_id: UUID, raw: object) -> FolderInfo:
        resolved = self.validate(raw)
        await self._settings.set_workspace_folder(workspace_id, user_id, str(resolved))
        return self.info_for(str(resolved))

    async def clear(self, workspace_id: UUID, user_id: UUID) -> None:
        await self._settings.set_workspace_folder(workspace_id, user_id, None)

    async def usable_root(self, workspace_id: UUID, user_id: UUID) -> Path | None:
        """The folder when it is connected, present and writable now; else ``None``."""
        info = await self.get(workspace_id, user_id)
        if info.status != "ok" or info.path is None:
            return None
        root = _norm(Path(info.path))
        # Re-check the safety rules: the setting may predate a rule or point at a moved folder.
        try:
            self.validate(str(root))
        except FolderError:
            return None
        return root

    # ------------------------------------------------------------------ paths

    def forbidden(self) -> tuple[Path, ...]:
        return self._protected

    def resolve(
        self,
        root: Path,
        relative: str,
        *,
        allow_root: bool = False,
        resolver: Callable[[Path], Path] | None = None,
    ) -> Path:
        """``relative`` under ``root`` through the SafePath rules (MAYA's own dirs excluded)."""
        return resolve_inside(
            root,
            relative,
            allow_root=allow_root,
            forbidden=self._protected,
            resolver=resolver,
        )
