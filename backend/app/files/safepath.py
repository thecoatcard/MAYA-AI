"""Strict relative-path resolution inside a root folder (the ``SafePath`` of ADR-0022).

Every read or write under the user's connected folder goes through :func:`resolve_inside`. It
accepts only a RELATIVE path made of plain names and returns an absolute path that is guaranteed
(as far as the filesystem can tell at call time) to be inside ``root``. The rules are the strict
Windows ones on every platform, so a folder that is shared between systems behaves the same:

* no absolute path, drive letter, UNC path or leading separator,
* no ``..`` (also after Unicode compatibility folding, so full-width dots and slashes fail),
* no NUL or other control/format characters (bidi overrides, zero-width spaces),
* no ``<>:"|?*`` (so no alternate data streams such as ``name:stream``),
* no segment ending in a dot or a space, and no Windows reserved device names (``CON``,
  ``NUL``, ``COM1`` ... with or without an extension),
* a total path length above :data:`MAX_PATH_CHARS` is refused,
* every EXISTING component is resolved (``Path.resolve``), so a symlink or junction that points
  outside the root is refused even when only a parent directory is the link.

This is a defence-in-depth check, not a sandbox: a concurrent actor who swaps a directory for a
link between this check and the write can still win a race (documented in the threat table).
"""

from __future__ import annotations

import os
import re
import unicodedata
from collections.abc import Callable, Iterable
from pathlib import Path

MAX_PATH_CHARS = 240
"""Total path budget (the same as ``app.automation.paths``): 20 below the Windows 260 limit."""
MAX_SEGMENT_CHARS = 120

_RESERVED = frozenset(
    {"con", "prn", "aux", "nul", "conin$", "conout$"}
    | {f"com{i}" for i in range(1, 10)}
    | {f"lpt{i}" for i in range(1, 10)}
)
_FORBIDDEN_CHARS = frozenset('<>:"|?*')
_DRIVE = re.compile(r"^[A-Za-z]:")
Resolver = Callable[[Path], Path]


class SafePathError(ValueError):
    """The path is not allowed. ``code`` is a stable machine reason; ``str`` is plain words."""

    def __init__(self, message: str, code: str = "unsafe_path") -> None:
        super().__init__(message)
        self.code = code


def _default_resolver(path: Path) -> Path:
    return path.resolve(strict=False)


def is_within(child: Path | str, root: Path | str, *, case_insensitive: bool | None = None) -> bool:
    """Whether ``child`` equals or lies under ``root`` (both already absolute and resolved).

    Compares whole path components, never string prefixes (``/a/bc`` is not inside ``/a/b``), and
    ignores case where the filesystem does (Windows by default).
    """
    fold = (os.name == "nt") if case_insensitive is None else case_insensitive

    def parts(value: Path | str) -> tuple[str, ...]:
        text = os.path.normpath(str(value))
        drive, rest = os.path.splitdrive(text)
        pieces = [p for p in re.split(r"[\\/]+", rest) if p]
        out = ([drive] if drive else []) + (["/"] if rest.startswith(("/", "\\")) else []) + pieces
        return tuple(p.casefold() if fold else p for p in out)

    child_parts, root_parts = parts(child), parts(root)
    return len(child_parts) >= len(root_parts) and child_parts[: len(root_parts)] == root_parts


def _check_segment(segment: str) -> None:
    if segment in ("", "."):
        raise SafePathError("The path has an empty or '.' part.", "empty_segment")
    if segment == ".." or set(segment) == {"."}:
        raise SafePathError("'..' is not allowed in a path.", "traversal")
    if len(segment) > MAX_SEGMENT_CHARS:
        raise SafePathError("A file or folder name in the path is too long.", "too_long")
    for char in segment:
        category = unicodedata.category(char)
        if category[0] == "C":  # control, format (bidi, zero-width), surrogate, private, unassigned
            raise SafePathError("The path contains a hidden or control character.", "bad_char")
        if char in _FORBIDDEN_CHARS:
            raise SafePathError(f"The character '{char}' is not allowed in a name.", "bad_char")
    if segment != segment.rstrip(". "):
        raise SafePathError("A name may not end with a dot or a space.", "trailing_dot_space")
    if segment != segment.lstrip(" "):
        raise SafePathError("A name may not start with a space.", "leading_space")
    stem = segment.split(".", 1)[0].rstrip(" ").casefold()
    if stem in _RESERVED:
        raise SafePathError(f"'{segment}' is a reserved system name.", "reserved_name")


def split_relative(relative: str, *, allow_root: bool = False) -> list[str]:
    """Validate ``relative`` and return its plain name parts. Raises :class:`SafePathError`."""
    if not isinstance(relative, str):
        raise SafePathError("The path must be text.", "not_text")
    if "\x00" in relative:
        raise SafePathError("The path contains a NUL character.", "nul")
    for form in (relative, unicodedata.normalize("NFKC", relative)):
        text = form.replace("\\", "/")
        if text.startswith("/"):
            raise SafePathError("Only paths inside the folder are allowed (no leading '/').")
        if _DRIVE.match(text):
            raise SafePathError("A drive letter is not allowed in a path.", "drive_letter")
        if text in ("", ".") and allow_root:
            continue
        trimmed = text[:-1] if text.endswith("/") else text
        for segment in trimmed.split("/"):
            _check_segment(segment)
    text = relative.replace("\\", "/")
    if text in ("", ".") and allow_root:
        return []
    trimmed = text[:-1] if text.endswith("/") else text
    return trimmed.split("/")


def resolve_inside(
    root: Path | str,
    relative: str,
    *,
    allow_root: bool = False,
    forbidden: Iterable[Path | str] = (),
    resolver: Resolver | None = None,
    max_chars: int = MAX_PATH_CHARS,
) -> Path:
    """The absolute path of ``relative`` under ``root``, or :class:`SafePathError`.

    ``forbidden`` lists absolute directories (for example MAYA's own storage) that the result
    may never be inside, even when they are inside ``root``. ``resolver`` replaces
    ``Path.resolve`` (tests inject a fake filesystem view).
    """
    resolve = resolver or _default_resolver
    parts = split_relative(relative, allow_root=allow_root)
    root_path = Path(root)
    if not root_path.is_absolute():
        raise SafePathError("The folder path must be absolute.", "root_not_absolute")
    real_root = resolve(root_path)
    candidate = real_root.joinpath(*parts)
    if len(str(candidate)) > max_chars:
        raise SafePathError(
            f"The path is too long (over {max_chars} characters). Use shorter names.", "too_long"
        )
    current = real_root
    for part in parts:
        current = current / part
        resolved = resolve(current)  # equals ``current`` unless it (or a parent) is a link
        if not is_within(resolved, real_root):
            raise SafePathError(
                "That path leads outside the connected folder (a link or junction).", "escape"
            )
    final = resolve(candidate)
    if not is_within(final, real_root):
        raise SafePathError("That path leads outside the connected folder.", "escape")
    for blocked in forbidden:
        if is_within(final, resolve(Path(blocked))):
            raise SafePathError(
                "That location belongs to MAYA itself and cannot be used.", "protected"
            )
    return final


def relative_to_root(root: Path | str, path: Path | str) -> str:
    """``path`` as a forward-slash relative string under ``root`` ('' for the root itself)."""
    rel = os.path.relpath(str(path), str(root))
    return "" if rel == "." else rel.replace("\\", "/")
