"""Artifact file names that can always be saved, on Windows too (pure, no I/O).

A title-derived name plus a deep artifacts folder can pass the Windows 260-character path limit
(``FileNotFoundError`` while saving). ``fit_artifact_name`` keeps the whole path under
``MAX_PATH_CHARS`` by shortening the file NAME deterministically (a short hash of the original
keeps two long titles apart), removes characters and names Windows rejects, and fails with an
actionable message when even a short name cannot fit because the folder itself is too deep.
"""

from __future__ import annotations

import hashlib
import re
from pathlib import Path

MAX_PATH_CHARS = 240
"""Total path budget: 20 characters below the Windows 260 limit for temporary files and suffixes."""
MAX_NAME_CHARS = 80
"""Longest file name stem a title may produce."""
MIN_STEM_CHARS = 8
_HASH_CHARS = 6
_UNSAFE = re.compile(r"[^A-Za-z0-9._-]+")
_RESERVED = frozenset(
    {
        "con",
        "prn",
        "aux",
        "nul",
        *(f"com{i}" for i in range(1, 10)),
        *(f"lpt{i}" for i in range(1, 10)),
    }
)


class ArtifactPathTooLongError(ValueError):
    """The artifacts folder is so deep that no usable file name fits under the path budget."""


def clean_stem(stem: str, fallback: str = "deliverable") -> str:
    """Letters, digits and ``._-`` only; no edge dots or spaces; no reserved device name."""
    cleaned = _UNSAFE.sub("_", stem).strip("._ -") or fallback
    if cleaned.lower() in _RESERVED:
        cleaned = f"{cleaned}_"
    return cleaned


def fit_artifact_name(
    directory: Path | str,
    stem: str,
    extension: str,
    *,
    max_total: int = MAX_PATH_CHARS,
    max_stem: int = MAX_NAME_CHARS,
) -> str:
    """``"{stem}.{extension}"`` shortened so ``directory / name`` stays within ``max_total``.

    Raises :class:`ArtifactPathTooLongError` (naming ``MAYA_ARTIFACTS_DIR``) when the directory
    leaves no room for even a short name.
    """
    ext = extension.lstrip(".").lower()
    suffix = f".{ext}" if ext else ""
    available = max_total - len(str(directory)) - 1 - len(suffix)
    if available < MIN_STEM_CHARS:
        raise ArtifactPathTooLongError(
            f"The artifacts folder path is too long to save files on this system "
            f"({len(str(directory))} characters; the limit is about {max_total}). Set "
            "MAYA_ARTIFACTS_DIR to a shorter folder (for example D:/maya/artifacts) and try again."
        )
    original = clean_stem(stem)
    cap = min(max_stem, available)
    if len(original) <= cap:
        return f"{original}{suffix}"
    digest = hashlib.sha256(stem.encode("utf-8")).hexdigest()[:_HASH_CHARS]
    head = original[: cap - _HASH_CHARS - 1].rstrip("._ -") or "file"
    return f"{head}-{digest}{suffix}"


def fit_filename(
    workspace_dir: Path | str, filename: str, *, max_total: int = MAX_PATH_CHARS
) -> str:
    """``filename`` (possibly ``"task-id/title.pdf"``) unchanged when it already fits and is safe,
    else with its last component shortened by :func:`fit_artifact_name`."""
    text = filename.replace("\\", "/")
    parent, _, name = text.rpartition("/")
    stem, dot, ext = name.rpartition(".")
    if not dot or not stem or len(ext) > 8:
        stem, ext = name, ""
    directory = Path(workspace_dir) / parent if parent else Path(workspace_dir)
    total = len(str(directory)) + 1 + len(name)
    risky = (
        total > max_total
        or len(stem) > MAX_NAME_CHARS
        or stem.lower() in _RESERVED
        or name != name.rstrip(". ")
    )
    if not risky:
        return filename
    fitted = fit_artifact_name(directory, stem, ext, max_total=max_total)
    return f"{parent}/{fitted}" if parent else fitted
