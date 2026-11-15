"""Markdown of a learning book (ADR-0031): assemble it from the chapters and describe it.

Pure functions, no I/O. The chapters come back from the model as Markdown; this module cleans each
one (a wrapping code fence, its own title, headings deeper than ``###``), joins them into ONE
document in the shape the Notion converter expects, and measures the result (words, the heading
outline, code blocks, tables, equations). The same measurements are shown on the approval card.

Heading depths: ``#`` is a chapter title, ``##`` a major part, ``###`` a section. Notion has no
``heading_4``, so nothing deeper is ever kept.
"""

from __future__ import annotations

import re
from typing import Any, Final

TOC_MARKER: Final = "[toc]"
_FENCE: Final = re.compile(r"^\s*(`{3,}|~{3,})")
_HEADING: Final = re.compile(r"^(#{1,6})\s+(.*?)\s*#*\s*$")
_ESCAPED_DOLLAR: Final = re.compile(r"\\\$")
_TABLE_SEP: Final = re.compile(r"^\s*\|?\s*:?-{3,}:?\s*(?:\|\s*:?-{3,}:?\s*)*\|?\s*$")
MAX_OUTLINE_ITEMS: Final = 200


def unwrap_fence(text: str) -> str:
    """The text without a ```markdown fence wrapped around the whole answer."""
    body = text.strip()
    match = re.match(r"^```(?:markdown|md)?\s*\n(.*)\n```\s*$", body, re.DOTALL | re.IGNORECASE)
    return match.group(1) if match else body


def _lines_outside_fences(lines: list[str]) -> list[tuple[str, bool]]:
    """Each line with ``True`` when it is inside a fenced code block."""
    out: list[tuple[str, bool]] = []
    marker = ""
    for line in lines:
        fence = _FENCE.match(line)
        if marker:
            out.append((line, True))
            if fence and fence.group(1)[0] == marker[0] and len(fence.group(1)) >= len(marker):
                marker = ""
        elif fence:
            marker = fence.group(1)
            out.append((line, True))
        else:
            out.append((line, False))
    return out


def clean_chapter_markdown(text: str, chapter_title: str) -> str:
    """One chapter's Markdown ready to join: its own level-1 heading or title heading removed,
    headings deeper than ``###`` made ``###``, ``\\$`` made ``$`` (outside code)."""
    lines = unwrap_fence(text).replace("\r\n", "\n").split("\n")
    marked = _lines_outside_fences(lines)
    title = " ".join(chapter_title.lower().split())
    out: list[str] = []
    seen_content = False
    for line, in_code in marked:
        if in_code:
            out.append(line)
            seen_content = True
            continue
        heading = _HEADING.match(line)
        if heading and not seen_content:
            level, name = len(heading.group(1)), heading.group(2)
            plain = " ".join(re.sub(r"^chapter\s+\d+\s*[:.—-]\s*", "", name.lower()).split())
            if level == 1 or plain == title:
                continue  # the assembler writes "Chapter N: title"
        if line.strip():
            seen_content = True
        if heading:
            level = min(len(heading.group(1)), 3)
            out.append(f"{'#' * level} {heading.group(2)}")
            continue
        out.append(_ESCAPED_DOLLAR.sub("$", line))
    return "\n".join(out).strip()


def build_book_markdown(
    *,
    subtitle: str,
    chapters: list[tuple[int, str, str]],
    missing: list[tuple[int, str]] | None = None,
    toc: bool = True,
) -> str:
    """The book as one Markdown document. ``chapters`` is ``[(index, title, markdown)]``.

    A short intro line (the subtitle), the table-of-contents marker, then each chapter as
    ``# Chapter N: title`` followed by its parts. Missing chapters end in a visible warning."""
    parts: list[str] = []
    if subtitle.strip():
        parts.append(f"*{subtitle.strip()}*")
    if toc:
        parts.append(TOC_MARKER)
    for index, title, markdown in sorted(chapters):
        parts.append(f"# Chapter {index}: {title}\n\n{clean_chapter_markdown(markdown, title)}")
    if missing:
        names = "; ".join(f"Chapter {i}: {t}" for i, t in missing)
        parts.append(
            f"> [!WARNING] Not included: {names}. This part could not be written, so the book is "
            "incomplete. Ask MAYA to write it again."
        )
    return "\n\n".join(parts).strip() + "\n"


def describe_markdown(markdown: str) -> dict[str, Any]:
    """Words, outline and counts of ``markdown`` (code is not counted as words)."""
    lines = markdown.replace("\r\n", "\n").split("\n")
    words = 0
    code_blocks = tables = equations = 0
    outline: list[dict[str, Any]] = []
    in_equation = False
    previous = ""
    was_code = False
    for line, in_code in _lines_outside_fences(lines):
        if in_code:
            if not was_code:
                code_blocks += 1  # the first line of a fenced block
            was_code = True
            continue
        was_code = False
        stripped = line.strip()
        if in_equation:
            in_equation = not stripped.endswith("$$")
            continue
        if stripped.startswith("$$"):
            equations += 1
            in_equation = not (len(stripped) > 2 and stripped.endswith("$$"))
            continue
        heading = _HEADING.match(line)
        if heading:
            if len(outline) < MAX_OUTLINE_ITEMS:
                outline.append({"level": min(len(heading.group(1)), 3), "text": heading.group(2)})
            words += len(heading.group(2).split())
            previous = line
            continue
        if _TABLE_SEP.match(line) and "|" in previous and "-" in line:
            tables += 1
        words += len(re.sub(r"[`*_>#|\-]+", " ", line).split())
        previous = line
    return {
        "words": words,
        "outline": outline,
        "headings": len(outline),
        "code_blocks": code_blocks,
        "tables": tables,
        "equations": equations,
    }


__all__ = [
    "TOC_MARKER",
    "build_book_markdown",
    "clean_chapter_markdown",
    "describe_markdown",
    "unwrap_fence",
]
