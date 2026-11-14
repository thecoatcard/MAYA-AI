"""Safe, deterministic project ``.zip`` builder (no folder connected: one download).

Every entry name is validated before it is written: relative, forward slashes, no ``..``, no
drive letter, no empty, control-character or reserved segment, extension from an allowlist, and a
size cap on each file and on the whole archive. Entries are sorted and carry a fixed timestamp
and mode, so the same files always give the same bytes. :func:`verify_zip` re-checks a finished
archive, so a zip-slip name can never reach a download.
"""

from __future__ import annotations

import re
import zipfile
from pathlib import Path, PurePosixPath

MAX_ZIP_FILES = 64
MAX_ENTRY_BYTES = 5 * 1024 * 1024
MAX_ZIP_BYTES = 20 * 1024 * 1024
ALLOWED_EXTENSIONS = frozenset(
    {"html", "css", "js", "json", "svg", "txt", "md", "xml", "csv", "py", "ts"}
)
_FIXED_TIME = (1980, 1, 1, 0, 0, 0)
_BAD_SEGMENT = re.compile(r'[<>:"|?*\x00-\x1f]')
_RESERVED = {"con", "prn", "aux", "nul"} | {f"com{i}" for i in range(1, 10)}
_RESERVED |= {f"lpt{i}" for i in range(1, 10)}


class ZipSafetyError(ValueError):
    """A name or size that must never go into (or come out of) a project archive."""


def safe_entry_name(name: str) -> str:
    """The normalised relative name, or :class:`ZipSafetyError` naming the problem."""
    if not isinstance(name, str) or not name.strip():
        raise ZipSafetyError("an empty file name")
    if "\\" in name:
        raise ZipSafetyError(f"'{name[:60]}': use forward slashes")
    if name.startswith("/") or re.match(r"^[A-Za-z]:", name):
        raise ZipSafetyError(f"'{name[:60]}': absolute paths are not allowed")
    parts = PurePosixPath(name).parts
    if "/".join(parts) != name:
        raise ZipSafetyError(f"'{name[:60]}': not a normalised relative path")
    if not parts or len(name) > 200:
        raise ZipSafetyError(f"'{name[:60]}': not a usable file name")
    for part in parts:
        if part in ("", ".", "..") or _BAD_SEGMENT.search(part) or part.endswith((".", " ")):
            raise ZipSafetyError(f"'{name[:60]}': path traversal or unsafe characters")
        if part.split(".")[0].lower() in _RESERVED:
            raise ZipSafetyError(f"'{name[:60]}': reserved name")
    last = parts[-1]
    extension = last.rpartition(".")[2].lower() if "." in last else ""
    if extension not in ALLOWED_EXTENSIONS:
        raise ZipSafetyError(f"'{name[:60]}': file type '.{extension}' is not allowed")
    return "/".join(parts)


def build_project_zip(files: dict[str, str], destination: Path, *, folder: str = "") -> list[str]:
    """Write ``files`` (relative name -> text) to ``destination``; return the entry names.

    ``folder`` (a slug) becomes the single top-level directory so unzipping never scatters files.
    Raises :class:`ZipSafetyError` before anything is written when a name or size is unsafe.
    """
    if not files:
        raise ZipSafetyError("the project has no files")
    if len(files) > MAX_ZIP_FILES:
        raise ZipSafetyError(f"too many files ({len(files)}; at most {MAX_ZIP_FILES})")
    top = safe_entry_name(f"{folder}/x.txt").rpartition("/")[0] if folder else ""
    entries: dict[str, bytes] = {}
    seen: set[str] = set()
    total = 0
    for name, text in files.items():
        clean = safe_entry_name(name)
        data = text.encode("utf-8")
        if len(data) > MAX_ENTRY_BYTES:
            raise ZipSafetyError(f"'{clean}' is larger than {MAX_ENTRY_BYTES // 1_048_576} MB")
        total += len(data)
        key = f"{top}/{clean}" if top else clean
        if key.lower() in seen:
            raise ZipSafetyError(f"'{clean}' is listed twice")
        seen.add(key.lower())
        entries[key] = data
    if total > MAX_ZIP_BYTES:
        raise ZipSafetyError(f"the project is larger than {MAX_ZIP_BYTES // 1_048_576} MB")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(destination, "w", zipfile.ZIP_DEFLATED, compresslevel=6) as archive:
        for key in sorted(entries):
            info = zipfile.ZipInfo(key, date_time=_FIXED_TIME)
            info.compress_type = zipfile.ZIP_DEFLATED
            info.external_attr = 0o644 << 16
            archive.writestr(info, entries[key])
    return sorted(entries)


def verify_zip(path: Path) -> list[str]:
    """Problems found in an archive (empty list = safe): bad names, oversize, not a zip."""
    problems: list[str] = []
    try:
        with zipfile.ZipFile(path) as archive:
            infos = archive.infolist()
            if len(infos) > MAX_ZIP_FILES:
                problems.append("too many entries")
            if sum(i.file_size for i in infos) > MAX_ZIP_BYTES:
                problems.append("uncompressed size is over the limit")
            for info in infos:
                try:
                    safe_entry_name(info.filename)
                except ZipSafetyError as err:
                    problems.append(str(err))
            if archive.testzip() is not None:
                problems.append("a file in the archive is corrupt")
    except zipfile.BadZipFile:
        problems.append("not a valid zip file")
    return problems
