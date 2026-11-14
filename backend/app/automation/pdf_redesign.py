"""PDF redesign mode: rebuild a NEW PDF from the text of an uploaded PDF.

Only the text is kept. Pages, images, fonts and the original layout are not, and the result says
so. The text comes from the pure-Python extractor already used for reading files (pypdf); no new
dependency. The rebuilt document goes through the normal PDF builder.

Preserve-layout PDF editing is NOT supported: this module never edits the original file.
"""

from __future__ import annotations

import re
import textwrap

from app.automation.model import (
    MAX_PARAGRAPH_CHARS,
    Block,
    Document,
    HeadingBlock,
    ParagraphBlock,
)

MAX_REDESIGN_CHARS = 300_000
"""Largest extracted text a redesign accepts (the document model has its own limit as well)."""
_PAGE_MARKER = re.compile(r"(?m)^== Page \d+\s*$")
_HEADING_MAX_CHARS = 80
_HEADING_MAX_WORDS = 12
_CONTINUED = (".", "!", "?", ":", ";", ",")
_CHUNK = MAX_PARAGRAPH_CHARS - 200


def _paragraphs(chunk: str) -> list[tuple[str, bool]]:
    """``(text, looks_like_heading)`` for each blank-line separated paragraph of one page."""
    found: list[tuple[str, bool]] = []
    for raw in re.split(r"\n\s*\n", chunk):
        text = " ".join(raw.split())
        if not text:
            continue
        one_line = "\n" not in raw.strip()
        heading = (
            one_line
            and len(text) <= _HEADING_MAX_CHARS
            and len(text.split()) <= _HEADING_MAX_WORDS
            and not text.endswith(_CONTINUED)
        )
        found.append((text, heading))
    return found


def document_from_pdf_text(text: str, *, title: str, theme: str = "executive") -> Document:
    """A :class:`Document` holding the text of an extracted PDF, in reading order.

    Blank lines separate paragraphs. A short one-line paragraph that does not end in
    punctuation becomes a level 2 heading (a heuristic: a wrong guess only changes the style).
    Raises ``ValueError`` when there is no text, or when it is too large for one document.
    """
    if len(text) > MAX_REDESIGN_CHARS:
        raise ValueError(
            f"The PDF has more than {MAX_REDESIGN_CHARS} characters of text; "
            "it is too large to redesign in one document."
        )
    blocks: list[Block] = []
    for chunk in _PAGE_MARKER.split(text):
        for body, heading in _paragraphs(chunk):
            if heading:
                blocks.append(HeadingBlock(level=2, text=body[:_HEADING_MAX_CHARS]))
                continue
            for piece in textwrap.wrap(body, _CHUNK, break_long_words=True) or []:
                blocks.append(ParagraphBlock(text=piece))
    if not blocks:
        raise ValueError("This PDF has no text to redesign.")
    return Document(title=title.strip()[:200] or "Redesigned document", theme=theme, blocks=blocks)


def document_plain_text(document: Document) -> str:
    """The text of a document built by :func:`document_from_pdf_text` (one line per block)."""
    return "\n".join(
        b.text for b in document.blocks if isinstance(b, HeadingBlock | ParagraphBlock)
    )


__all__ = [
    "MAX_REDESIGN_CHARS",
    "document_from_pdf_text",
    "document_plain_text",
]
