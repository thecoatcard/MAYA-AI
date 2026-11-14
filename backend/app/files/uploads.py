"""Uploaded files: validation, content sniffing and per-workspace storage (ADR-0022).

Bytes live at ``<artifacts>/<workspace_id>/uploads/<upload_id>/<safe name>`` next to a small
``manifest.json``. Nothing a client sends is trusted: the display name is sanitised, the on-disk
name is rebuilt from ``clean_stem``, the extension must be on the allow-list AND agree with the
content (magic bytes), executables and archives are refused, and an id that is not a canonical
UUID never reaches the filesystem. Another workspace's id is simply "not found".
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import unicodedata
import uuid
import zipfile
from dataclasses import asdict, dataclass
from datetime import UTC, datetime
from io import BytesIO
from pathlib import Path
from typing import Any
from uuid import UUID

from app.automation.paths import clean_stem
from app.files.extract import kind_for

ALLOWED_EXTENSIONS = frozenset(
    "txt md csv json html css js ts tsx jsx py java c cpp cs go rs sql yaml yml xml toml ini log "
    "docx xlsx pptx pdf png jpg jpeg gif webp svg".split()
)
BLOCKED_EXTENSIONS = frozenset(
    "exe dll bat cmd ps1 sh msi zip 7z rar jar com scr vbs wsf lnk reg dmg iso tar gz tgz bz2 xz "
    "apk app".split()
)
MAX_FILES_PER_REQUEST = 10
MAX_DISPLAY_NAME = 120
_MAX_UNCOMPRESSED = 200 * 1024 * 1024
_NAME_BAD = re.compile(r'[<>:"/\\|?*\x00-\x1f\x7f]+')
_MANIFEST = "manifest.json"

_OFFICE_PART = {
    "docx": "word/document.xml",
    "xlsx": "xl/workbook.xml",
    "pptx": "ppt/presentation.xml",
}
_EXECUTABLE_MAGIC = (b"MZ", b"\x7fELF", b"\xfe\xed\xfa", b"\xcf\xfa\xed\xfe", b"\xca\xfe\xba\xbe")
_ARCHIVE_MAGIC = (b"7z\xbc\xaf", b"Rar!", b"\x1f\x8b")
_IMAGE_MAGIC = {
    "png": (b"\x89PNG\r\n\x1a\n",),
    "jpg": (b"\xff\xd8\xff",),
    "jpeg": (b"\xff\xd8\xff",),
    "gif": (b"GIF87a", b"GIF89a"),
}
CONTENT_TYPES = {
    "pdf": "application/pdf",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
    "gif": "image/gif",
    "webp": "image/webp",
    "svg": "image/svg+xml",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "xlsx": "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
    "pptx": "application/vnd.openxmlformats-officedocument.presentationml.presentation",
}


class UploadError(Exception):
    """A refused upload. ``status`` is the HTTP status, ``code`` a stable reason."""

    def __init__(self, status: int, code: str, message: str) -> None:
        super().__init__(message)
        self.status = status
        self.code = code
        self.message = message


@dataclass(frozen=True, slots=True)
class UploadRecord:
    id: str
    name: str
    size: int
    kind: str
    created_at: str
    sha256: str
    disk_name: str
    content_type: str
    workspace_id: str
    user_id: str

    def public(self) -> dict[str, object]:
        return {
            "id": self.id,
            "name": self.name,
            "size": self.size,
            "kind": self.kind,
            "created_at": self.created_at,
        }


def display_name(raw: str) -> str:
    """A readable name: no path, no control or reserved characters, bounded length."""
    base = raw.replace("\\", "/").rsplit("/", 1)[-1]
    base = unicodedata.normalize("NFC", base)
    base = "".join(ch for ch in base if unicodedata.category(ch)[0] != "C")
    base = _NAME_BAD.sub("_", base).strip(" .")
    stem, dot, ext = base.rpartition(".")
    if not dot or not stem:
        stem, ext = base, ""
    if len(stem) > MAX_DISPLAY_NAME:
        stem = stem[:MAX_DISPLAY_NAME].rstrip(" .")
    return f"{stem or 'file'}.{ext}" if ext else (stem or "file")


def disk_name_for(name: str) -> str:
    """The ASCII on-disk name derived from a display name (never the client's bytes)."""
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem:
        stem, ext = name, ""
    safe = clean_stem(stem, fallback="file")[:80]
    return f"{safe}.{ext.lower()}" if ext else safe


def _is_text(head: bytes) -> bool:
    return b"\x00" not in head


def sniff(ext: str, data: bytes) -> None:
    """Check ``data`` against ``ext``. Raises :class:`UploadError` (415) on a mismatch."""
    head = data[:8192]
    if head.startswith(_EXECUTABLE_MAGIC):
        raise UploadError(415, "executable_content", "Executable files are not accepted.")
    if head.startswith(_ARCHIVE_MAGIC):
        raise UploadError(415, "archive_content", "Archives are not accepted.")
    if ext in _OFFICE_PART:
        _check_office(ext, data)
        return
    if head.startswith(b"PK\x03\x04"):
        raise UploadError(415, "archive_content", "Archives are not accepted.")
    if ext == "pdf":
        if b"%PDF-" not in data[:1024]:
            raise UploadError(415, "type_mismatch", "The file is not a valid PDF.")
        return
    if ext in _IMAGE_MAGIC:
        if not head.startswith(_IMAGE_MAGIC[ext]):
            raise UploadError(415, "type_mismatch", f"The file is not a valid {ext.upper()} image.")
        return
    if ext == "webp":
        if not (head[:4] == b"RIFF" and head[8:12] == b"WEBP"):
            raise UploadError(415, "type_mismatch", "The file is not a valid WEBP image.")
        return
    if ext == "svg":
        text = head.lstrip(b"\xef\xbb\xbf \t\r\n")
        if not (_is_text(head) and (text.startswith(b"<?xml") or text.startswith(b"<svg"))):
            raise UploadError(415, "type_mismatch", "The file is not a valid SVG image.")
        if b"<svg" not in head.lower() and b"<svg" not in data[:65536].lower():
            raise UploadError(415, "type_mismatch", "The file is not a valid SVG image.")
        return
    if not _is_text(head):
        raise UploadError(
            415, "type_mismatch", f"The file is not plain text, so it cannot be a .{ext} file."
        )


def _check_office(ext: str, data: bytes) -> None:
    if not data.startswith(b"PK\x03\x04"):
        raise UploadError(415, "type_mismatch", f"The file is not a valid .{ext} document.")
    try:
        with zipfile.ZipFile(BytesIO(data)) as archive:
            infos = archive.infolist()
            if sum(i.file_size for i in infos) > _MAX_UNCOMPRESSED:
                raise UploadError(415, "zip_bomb", "The document expands to an unsafe size.")
            names = {i.filename for i in infos}
    except zipfile.BadZipFile:
        raise UploadError(
            415, "type_mismatch", f"The file is not a valid .{ext} document."
        ) from None
    if _OFFICE_PART[ext] not in names:
        raise UploadError(415, "type_mismatch", f"The file is not a valid .{ext} document.")


def _now() -> str:
    return datetime.now(UTC).isoformat()


class UploadStore:
    """Per-workspace upload storage on local disk."""

    def __init__(self, artifacts_dir: Path | str, *, max_bytes: int, total_max_bytes: int) -> None:
        self._root = Path(artifacts_dir).resolve()
        self.max_bytes = max_bytes
        self.total_max_bytes = total_max_bytes

    # ------------------------------------------------------------------ paths

    def _uploads_dir(self, workspace_id: UUID) -> Path:
        return self._root / str(workspace_id) / "uploads"

    @staticmethod
    def _canonical(upload_id: str) -> str | None:
        try:
            parsed = UUID(upload_id)
        except (ValueError, AttributeError, TypeError):
            return None
        return str(parsed) if str(parsed) == upload_id.lower() else None

    def _dir_of(self, workspace_id: UUID, upload_id: str) -> Path | None:
        canonical = self._canonical(upload_id)
        return None if canonical is None else self._uploads_dir(workspace_id) / canonical

    # ------------------------------------------------------------------ write

    def validate(self, name: str, data: bytes) -> tuple[str, str, str]:
        """``(display name, extension, kind)`` for an acceptable file, else :class:`UploadError`."""
        shown = display_name(name)
        ext = shown.rpartition(".")[2].lower() if "." in shown else ""
        if ext in BLOCKED_EXTENSIONS:
            raise UploadError(415, "blocked_type", f"Files of type .{ext} are not accepted.")
        if ext not in ALLOWED_EXTENSIONS:
            raise UploadError(
                415, "unsupported_type", f"Files of type .{ext or '(none)'} are not supported."
            )
        if not data:
            raise UploadError(400, "empty_file", "The file is empty.")
        if len(data) > self.max_bytes:
            raise UploadError(
                413,
                "file_too_large",
                f"The file is larger than {self.max_bytes // (1024 * 1024)} MB.",
            )
        sniff(ext, data)
        return shown, ext, kind_for(shown)

    def save(self, workspace_id: UUID, user_id: UUID, name: str, data: bytes) -> UploadRecord:
        shown, ext, kind = self.validate(name, data)
        upload_id = str(uuid.uuid4())
        directory = self._uploads_dir(workspace_id) / upload_id
        disk_name = disk_name_for(shown)
        record = UploadRecord(
            id=upload_id,
            name=shown,
            size=len(data),
            kind=kind,
            created_at=_now(),
            sha256=hashlib.sha256(data).hexdigest(),
            disk_name=disk_name,
            content_type=CONTENT_TYPES.get(ext, "application/octet-stream"),
            workspace_id=str(workspace_id),
            user_id=str(user_id),
        )
        directory.mkdir(parents=True, exist_ok=False)
        try:
            (directory / disk_name).write_bytes(data)
            tmp = directory / f"{_MANIFEST}.tmp"
            tmp.write_text(json.dumps(asdict(record)), encoding="utf-8")
            os.replace(tmp, directory / _MANIFEST)
        except Exception:
            shutil.rmtree(directory, ignore_errors=True)
            raise
        return record

    # ------------------------------------------------------------------ read

    @staticmethod
    def _load(directory: Path) -> UploadRecord | None:
        try:
            raw: dict[str, Any] = json.loads((directory / _MANIFEST).read_text(encoding="utf-8"))
            return UploadRecord(**raw)
        except (OSError, ValueError, TypeError):
            return None

    def get(self, workspace_id: UUID, upload_id: str) -> UploadRecord | None:
        directory = self._dir_of(workspace_id, upload_id)
        if directory is None or not directory.is_dir():
            return None
        record = self._load(directory)
        if record is None or record.workspace_id != str(workspace_id):
            return None
        return record

    def path_of(self, record: UploadRecord) -> Path | None:
        """The stored file, or ``None`` when it is gone (checked to be inside its directory)."""
        directory = self._uploads_dir(UUID(record.workspace_id)) / record.id
        target = (directory / record.disk_name).resolve()
        if target.parent != directory.resolve() or not target.is_file():
            return None
        return target

    def list(
        self, workspace_id: UUID, limit: int = 50, cursor: str | None = None
    ) -> tuple[list[UploadRecord], str | None]:
        base = self._uploads_dir(workspace_id)
        records: list[UploadRecord] = []
        if base.is_dir():
            for entry in base.iterdir():
                if entry.is_dir():
                    record = self._load(entry)
                    if record is not None and record.workspace_id == str(workspace_id):
                        records.append(record)
        records.sort(key=lambda r: (r.created_at, r.id), reverse=True)
        if cursor:
            marker = tuple(cursor.split("|", 1))
            if len(marker) != 2:
                raise UploadError(400, "bad_cursor", "The cursor is not valid.")
            records = [r for r in records if (r.created_at, r.id) < (marker[0], marker[1])]
        page = records[: max(1, limit)]
        more = len(records) > len(page)
        next_cursor = f"{page[-1].created_at}|{page[-1].id}" if page and more else None
        return page, next_cursor

    def delete(self, workspace_id: UUID, upload_id: str) -> bool:
        directory = self._dir_of(workspace_id, upload_id)
        if directory is None or self.get(workspace_id, upload_id) is None:
            return False
        shutil.rmtree(directory, ignore_errors=True)
        return True
