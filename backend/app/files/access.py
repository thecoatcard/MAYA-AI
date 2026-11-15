"""What an agent or tool may do with the caller's files (shared by the tools and the agent steps).

Everything starts from a :class:`FileContext`: the workspace and user (from the task or tool
scope, NEVER from model arguments) and the installed ``FileServices``. The connected folder is
read from that user's settings; a path argument is only ever a name RELATIVE to it, passed
through the SafePath rules. Uploads are addressed by id inside the caller's workspace.
"""

from __future__ import annotations

import asyncio
import difflib
import os
import re
from dataclasses import dataclass
from pathlib import Path
from uuid import UUID, uuid4

from app.automation.file_edit import (
    EDITABLE_KINDS,
    EditError,
    NotEditableError,
    apply_edit,
    create_backup,
    not_editable_message,
    requests_copy,
    reserve_version,
    sha256_of,
    verify_new_version,
)
from app.files.extract import Extracted, extract, kind_for
from app.files.safepath import SafePathError, relative_to_root
from app.files.services import FileServices, OutputTarget
from app.files.uploads import UploadRecord

MAX_READ_BYTES = 2 * 1024 * 1024
"""Largest text file the read tool will open (2 MB)."""
MAX_WRITE_BYTES = 5 * 1024 * 1024
MAX_PROJECT_FILES = 300


class FileAccessError(Exception):
    """A file operation was refused. The message is plain words (safe to show to the user)."""


@dataclass(frozen=True, slots=True)
class Source:
    """One file an operation reads: a folder file (``relative`` set) or an upload."""

    path: Path
    name: str
    kind: str
    relative: str | None = None
    upload: UploadRecord | None = None


@dataclass(frozen=True, slots=True)
class EditResult:
    path: Path
    name: str
    kind: str
    relative: str | None  # path inside the connected folder, when saved there
    diff: str
    changes: int
    warnings: list[str]
    sha256: str
    size: int
    saved_in_folder: bool
    destination_warning: str | None = None
    backup: Path | None = None


@dataclass(frozen=True, slots=True)
class FileContext:
    services: FileServices
    workspace_id: UUID
    user_id: UUID
    task_id: UUID | None = None

    async def root(self) -> Path:
        root = await self.services.folders.usable_root(self.workspace_id, self.user_id)
        if root is None:
            raise FileAccessError(
                "No usable folder is connected. Connect a folder in MAYA first (Settings)."
            )
        return root

    def resolve(self, root: Path, relative: str, *, allow_root: bool = False) -> Path:
        try:
            return self.services.folders.resolve(root, relative, allow_root=allow_root)
        except SafePathError as err:
            raise FileAccessError(str(err)) from None

    def relative(self, root: Path, path: Path) -> str:
        return relative_to_root(root, path)

    async def upload_source(self, upload_id: str) -> Source:
        record = await asyncio.to_thread(self.services.uploads.get, self.workspace_id, upload_id)
        path = self.services.uploads.path_of(record) if record is not None else None
        if record is None or path is None:
            raise FileAccessError("That attached file was not found.")
        return Source(path=path, name=record.name, kind=record.kind, upload=record)

    async def find_in_folder(self, hint: str = "", text: str = "") -> Source:
        """Resolve a file NAME against the connected folder's own listing (never invents a path).

        ``hint`` is a name or path the planner or user gave ("report.docx", "Reports/sales");
        ``text`` is the request wording, searched for a file name when there is no hint. Matching
        ignores case, an extension is optional, a unique fuzzy match is accepted, and several
        candidates raise a question listing them. Only real files that exist are returned.
        """
        root = await self.root()
        names = await asyncio.to_thread(_scan_files, root)
        matches = match_file_names(names, hint, text)
        if not matches:
            what = hint.strip() or "that file"
            raise FileAccessError(
                f"I could not find '{what}' in your connected folder. Tell me the exact file name."
            )
        if len(matches) > 1:
            shown = ", ".join(f"'{m}'" for m in matches[:5])
            raise FileAccessError(f"Which file do you mean: {shown}? Tell me the exact name.")
        return await self.folder_source(matches[0])

    async def folder_source(self, relative: str) -> Source:
        root = await self.root()
        path = self.resolve(root, relative)
        if not await asyncio.to_thread(path.is_file):
            raise FileAccessError("That file does not exist in the connected folder.")
        return Source(
            path=path, name=path.name, kind=kind_for(path.name), relative=self.relative(root, path)
        )

    async def source(self, *, upload_id: str | None = None, path: str | None = None) -> Source:
        if upload_id:
            return await self.upload_source(upload_id)
        if path:
            return await self.folder_source(path)
        raise FileAccessError("Say which file: an attached file or a path inside the folder.")

    async def read(self, source: Source, *, max_chars: int, indexed: bool = False) -> Extracted:
        if source.path.stat().st_size > MAX_READ_BYTES * 12 and source.kind in (
            "docx",
            "xlsx",
            "pptx",
            "pdf",
        ):
            return Extracted(source.kind, "", readable=False, note="This file is too large.")
        if source.kind in ("text", "code", "markdown", "csv", "json") and (
            source.path.stat().st_size > MAX_READ_BYTES
        ):
            # Only the first part is read; the view says so.
            view = await asyncio.to_thread(extract, source.path, source.kind, max_chars=max_chars)
            return Extracted(
                view.kind,
                view.text,
                view.readable,
                True,
                "Only the start of this large file is shown.",
            )
        return await asyncio.to_thread(
            extract, source.path, source.kind, max_chars=max_chars, indexed=indexed
        )

    # ------------------------------------------------------------------ editing

    async def create_new_version(
        self,
        source: Source,
        plan: dict[str, object],
        *,
        make_copy: bool = False,
        stage: bool = False,
    ) -> EditResult:
        """Write ``source`` with ``plan`` applied in place (or as a copy if requested).

        For existing folder files, edits are applied in place (atomically via a temporary file in
        the same directory, then os.replace, with a backup in .maya-backups).
        If ``stage`` is True, the edit is applied and verified on a temporary file without
        overwriting ``source`` yet (used by multi-attempt preservation checks); ``commit_edit``
        then atomically swaps it in place once all checks pass.
        A PDF is accepted only with a ``document`` plan (redesign mode: a NEW PDF built from its
        text); a PDF is never edited in place.
        """
        redesign = source.kind == "pdf" and "document" in plan
        if source.kind not in EDITABLE_KINDS and not redesign:
            raise NotEditableError(not_editable_message(source.kind, source.name))
        warning: str | None = None
        saved_in_folder = False
        root: Path | None = None
        if source.relative is not None:
            root = await self.root()
            directory = source.path.parent
            saved_in_folder = True
        else:
            destination = await OutputTarget(self.services).resolve(self.workspace_id, self.user_id)
            warning = destination.warning
            if destination.folder is not None:
                root, directory, saved_in_folder = destination.folder, destination.folder, True
            else:
                task_part = str(self.task_id) if self.task_id else "uploads"
                directory = self.services.artifacts_dir / str(self.workspace_id) / task_part
                directory.mkdir(parents=True, exist_ok=True)

        if redesign:
            target = await asyncio.to_thread(reserve_version, directory, source.name, "copy")
            try:
                outcome = await asyncio.to_thread(
                    apply_edit, source.kind, source.path, plan, target
                )
                await asyncio.to_thread(verify_new_version, source.kind, target)
            except BaseException:
                target.unlink(missing_ok=True)
                raise
            sha = await asyncio.to_thread(sha256_of, target)
            size = target.stat().st_size
            relative = (
                self.relative(root, target) if (root is not None and saved_in_folder) else None
            )
            return EditResult(
                path=target,
                name=target.name,
                kind=source.kind,
                relative=relative,
                diff=outcome.diff,
                changes=outcome.changes,
                warnings=outcome.warnings,
                sha256=sha,
                size=size,
                saved_in_folder=saved_in_folder,
                destination_warning=warning,
            )

        is_copy = make_copy or requests_copy(str(plan.get("wording") or ""))
        temp = directory / f".maya-edit-{uuid4().hex[:12]}.tmp"
        try:
            outcome = await asyncio.to_thread(apply_edit, source.kind, source.path, plan, temp)
            await asyncio.to_thread(verify_new_version, source.kind, temp)
        except BaseException:
            temp.unlink(missing_ok=True)
            raise

        if stage:
            sha = await asyncio.to_thread(sha256_of, temp)
            size = temp.stat().st_size
            rel = source.relative if (root is not None and saved_in_folder) else None
            return EditResult(
                path=temp,
                name=source.name,
                kind=source.kind,
                relative=rel,
                diff=outcome.diff,
                changes=outcome.changes,
                warnings=outcome.warnings,
                sha256=sha,
                size=size,
                saved_in_folder=saved_in_folder,
                destination_warning=warning,
            )

        candidate = EditResult(
            path=temp,
            name=source.name,
            kind=source.kind,
            relative=source.relative,
            diff=outcome.diff,
            changes=outcome.changes,
            warnings=outcome.warnings,
            sha256="",
            size=0,
            saved_in_folder=saved_in_folder,
            destination_warning=warning,
        )
        return await self.commit_edit(source, candidate, make_copy=is_copy)

    async def commit_edit(
        self,
        source: Source,
        candidate: EditResult,
        *,
        make_copy: bool = False,
    ) -> EditResult:
        """Atomically commit a staged edit candidate to its destination."""
        temp = candidate.path
        if not temp.is_file():
            raise FileAccessError("The staged edit no longer exists.")
        directory = temp.parent
        root: Path | None = None
        saved_in_folder = False
        if source.relative is not None:
            root = await self.root()
            saved_in_folder = True
        else:
            destination = await OutputTarget(self.services).resolve(self.workspace_id, self.user_id)
            if destination.folder is not None:
                root, saved_in_folder = destination.folder, True

        backup_path: Path | None = None
        if make_copy:
            target = await asyncio.to_thread(reserve_version, directory, source.name, "copy")
            await asyncio.to_thread(os.replace, temp, target)
        elif source.relative is not None:
            target = source.path
            backup_path = await asyncio.to_thread(create_backup, target)
            await asyncio.to_thread(os.replace, temp, target)
        else:
            target = directory / source.name
            if await asyncio.to_thread(target.is_file):
                backup_path = await asyncio.to_thread(create_backup, target)
            await asyncio.to_thread(os.replace, temp, target)

        sha = await asyncio.to_thread(sha256_of, target)
        size = target.stat().st_size
        relative = self.relative(root, target) if (root is not None and saved_in_folder) else None
        await self.services.audit(
            workspace_id=self.workspace_id,
            user_id=self.user_id,
            task_id=self.task_id,
            tool="file_edit_as_new_version",
            level="WRITE",
            detail={
                "source": source.name,
                "new_version": target.name,
                "changes": candidate.changes,
                "in_place": not make_copy,
            },
        )
        return EditResult(
            path=target,
            name=target.name,
            kind=source.kind,
            relative=relative,
            diff=candidate.diff,
            changes=candidate.changes,
            warnings=candidate.warnings,
            sha256=sha,
            size=size,
            saved_in_folder=saved_in_folder,
            destination_warning=candidate.destination_warning,
            backup=backup_path,
        )


_SCAN_LIMIT = 3000
_SCAN_DEPTH = 5
_SKIP_DIRS = frozenset({".git", "node_modules", "__pycache__", ".venv", ".maya-backups"})
_VERSION_MARK = re.compile(r"\((?:copy|original|edited)[^)]*\)", re.IGNORECASE)


def _scan_files(root: Path) -> list[str]:
    """Folder-relative paths (forward slashes) of the editable files below ``root``."""
    out: list[str] = []
    base = str(root)
    for current, dirs, files in os.walk(root, followlinks=False):
        depth = current[len(base) :].count(os.sep)
        dirs[:] = sorted(d for d in dirs if d not in _SKIP_DIRS and not d.startswith("."))
        if depth >= _SCAN_DEPTH:
            dirs[:] = []
        for name in sorted(files):
            if name.startswith(".") or kind_for(name) not in EDITABLE_KINDS:
                continue
            relative = os.path.relpath(os.path.join(current, name), base).replace(os.sep, "/")
            out.append(relative)
            if len(out) >= _SCAN_LIMIT:
                return out
    return out


def _stem(name: str) -> str:
    return _VERSION_MARK.sub("", name.rpartition("/")[2].rpartition(".")[0]).strip().lower()


def match_file_names(names: list[str], hint: str = "", text: str = "") -> list[str]:
    """The listed files a name hint (or the request wording) refers to; empty = none.

    Order of trust: exact relative path, same file name, same name without extension, a unique
    fuzzy match; then (no hint) a file name or a distinctive stem found in ``text``. Backup and
    separate copies ("x (copy).docx", "x (original, ...).docx") never win over the original
    when both match.
    """
    originals = [n for n in names if not _VERSION_MARK.search(n.rpartition("/")[2])]
    pool = originals or names
    hint = hint.strip().replace("\\", "/").strip("/")
    if hint:
        low = hint.lower()
        base = low.rpartition("/")[2]
        for test in (
            lambda n: n.lower() == low,
            lambda n: n.lower().rpartition("/")[2] == base,
            lambda n: _stem(n) == base.rpartition(".")[0] or _stem(n) == base,
        ):
            found = [n for n in pool if test(n)]
            if found:
                return found
        want = base.rpartition(".")[0] if "." in base else base
        scored = [
            (difflib.SequenceMatcher(None, want, _stem(n)).ratio(), n) for n in pool if _stem(n)
        ]
        close = [n for ratio, n in scored if ratio >= 0.8 or (len(want) >= 4 and want in _stem(n))]
        return close
    lowered = text.lower()
    named = [n for n in pool if n.lower().rpartition("/")[2] in lowered]
    if named:
        longest = max(len(n.rpartition("/")[2]) for n in named)
        return [n for n in named if len(n.rpartition("/")[2]) == longest]
    words = set(re.findall(r"[\w]+", lowered))
    found = [n for n in pool if len(_stem(n)) >= 3 and _stem(n) in words]
    return found


def context_from(
    services: FileServices | None,
    workspace_id: UUID | None,
    user_id: UUID | None,
    task_id: UUID | None = None,
) -> FileContext:
    """A context, or :class:`FileAccessError` when the file features or the caller are missing."""
    if services is None:
        raise FileAccessError("File access is not available in this deployment.")
    if workspace_id is None or user_id is None:
        raise FileAccessError("File access needs a signed-in user.")
    return FileContext(services, workspace_id, user_id, task_id)


__all__ = [
    "EditError",
    "EditResult",
    "FileAccessError",
    "FileContext",
    "Source",
    "context_from",
]
