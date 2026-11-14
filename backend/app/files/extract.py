"""Bounded text extraction for uploaded and folder files (untrusted DATA, never instructions).

Every function returns at most ``max_chars`` characters and says honestly what it could not do:
an image is metadata only (no pixel reading), a scanned PDF has no text layer, a corrupt Office
file is reported as unreadable. Nothing here runs macros or follows links: the Office formats are
read with python-docx / openpyxl / python-pptx and the PDF with pypdf, all in-process parsers.
"""

from __future__ import annotations

import posixpath
from dataclasses import dataclass
from pathlib import Path
from typing import Any

TEXT_EXTENSIONS = {"txt": "text", "log": "text", "ini": "text", "md": "markdown", "csv": "csv"}
CODE_EXTENSIONS = "html css js ts tsx jsx py java c cpp cs go rs sql yaml yml xml toml".split()
IMAGE_EXTENSIONS = ("png", "jpg", "jpeg", "gif", "webp", "svg")
OFFICE_EXTENSIONS = ("docx", "xlsx", "pptx", "pdf")
EDITABLE_TEXT_KINDS = frozenset({"text", "code", "markdown", "csv", "json"})
MAX_SHEETS = 10
MAX_SHEET_ROWS = 60
MAX_SHEET_COLS = 30
MAX_PDF_PAGES = 60
DEFAULT_MAX_CHARS = 8_000


def kind_for(file_name: str) -> str:
    """The contract ``kind`` for a file name (``other`` when the extension is not allowed)."""
    ext = posixpath.splitext(file_name.lower())[1].lstrip(".")
    if ext in TEXT_EXTENSIONS:
        return TEXT_EXTENSIONS[ext]
    if ext == "json":
        return "json"
    if ext in CODE_EXTENSIONS:
        return "code"
    if ext in OFFICE_EXTENSIONS:
        return ext
    if ext in IMAGE_EXTENSIONS:
        return "image"
    return "other"


@dataclass(frozen=True, slots=True)
class Extracted:
    """The bounded text view of one file."""

    kind: str
    text: str
    readable: bool
    truncated: bool = False
    note: str = ""

    def view(self) -> str:
        """The text plus any honest note, for a prompt."""
        if self.note and self.text:
            return f"{self.text}\n[{self.note}]"
        return self.note and f"[{self.note}]" or self.text


def decode_text(data: bytes) -> str:
    """UTF-8 (with or without BOM), else Windows-1252, else UTF-8 with replacement."""
    try:
        return data.decode("utf-8-sig")
    except UnicodeDecodeError:
        pass
    try:
        return data.decode("cp1252")
    except UnicodeDecodeError:
        return data.decode("utf-8", errors="replace")


def _bound(text: str, max_chars: int) -> tuple[str, bool]:
    if len(text) <= max_chars:
        return text, False
    return text[:max_chars], True


def _cell(value: Any) -> str:
    return "" if value is None else str(value).replace("\n", " ")


# ------------------------------------------------------------------------------- docx


def docx_paragraphs(doc: Any) -> list[tuple[int, str, str]]:
    """``(index, style name, text)`` for every body paragraph (the index the edit ops use)."""
    return [
        (i, str(getattr(p.style, "name", "") or ""), p.text) for i, p in enumerate(doc.paragraphs)
    ]


def _extract_docx(path: Path, max_chars: int, *, indexed: bool) -> Extracted:
    from docx import Document

    doc = Document(str(path))
    lines: list[str] = []
    for index, style, text in docx_paragraphs(doc):
        if not text.strip() and not indexed:
            continue
        lines.append(f"[{index}] ({style}) {text}" if indexed else text)
    for number, table in enumerate(doc.tables, start=1):
        lines.append(f"[table {number}]")
        for row in table.rows:
            lines.append(" | ".join(_cell(c.text) for c in row.cells))
    text, cut = _bound("\n".join(lines), max_chars)
    return Extracted("docx", text, readable=bool(text.strip()), truncated=cut)


# ------------------------------------------------------------------------------- xlsx


def _extract_xlsx(path: Path, max_chars: int, *, indexed: bool) -> Extracted:
    from openpyxl import load_workbook

    workbook = load_workbook(str(path), read_only=False, data_only=False)
    lines: list[str] = []
    try:
        for sheet in workbook.worksheets[:MAX_SHEETS]:
            lines.append(
                f"== Sheet '{sheet.title}' ({sheet.max_row} rows x {sheet.max_column} cols)"
            )
            for row in sheet.iter_rows(
                min_row=1, max_row=min(sheet.max_row, MAX_SHEET_ROWS), max_col=MAX_SHEET_COLS
            ):
                cells = [
                    (f"{c.coordinate}={_cell(c.value)}" if indexed else _cell(c.value))
                    for c in row
                    if c.value is not None or not indexed
                ]
                if any(cells):
                    lines.append(" | ".join(cells))
            if sheet.max_row > MAX_SHEET_ROWS:
                lines.append(f"... {sheet.max_row - MAX_SHEET_ROWS} more rows not shown")
        if len(workbook.worksheets) > MAX_SHEETS:
            lines.append(f"... {len(workbook.worksheets) - MAX_SHEETS} more sheets not shown")
    finally:
        workbook.close()
    text, cut = _bound("\n".join(lines), max_chars)
    return Extracted("xlsx", text, readable=bool(text.strip()), truncated=cut)


# ------------------------------------------------------------------------------- pptx


def pptx_shapes(slide: Any) -> list[tuple[int, str]]:
    """``(shape_index, text)`` of every shape on a slide that holds text."""
    out: list[tuple[int, str]] = []
    for index, shape in enumerate(slide.shapes):
        if getattr(shape, "has_text_frame", False) and shape.has_text_frame:
            out.append((index, shape.text_frame.text))
    return out


def _extract_pptx(path: Path, max_chars: int, *, indexed: bool) -> Extracted:
    from pptx import Presentation

    deck = Presentation(str(path))
    lines: list[str] = []
    for number, slide in enumerate(deck.slides, start=1):
        lines.append(f"== Slide {number}")
        for index, text in pptx_shapes(slide):
            if text.strip():
                lines.append(f"[shape {index}] {text}" if indexed else text)
        if slide.has_notes_slide:
            notes = slide.notes_slide.notes_text_frame
            if notes is not None and notes.text.strip():
                lines.append(f"(notes) {notes.text}")
    text, cut = _bound("\n".join(lines), max_chars)
    return Extracted("pptx", text, readable=bool(text.strip()), truncated=cut)


# -------------------------------------------------------------------------------- pdf


def _extract_pdf(path: Path, max_chars: int) -> Extracted:
    from pypdf import PdfReader

    reader = PdfReader(str(path))
    if reader.is_encrypted:
        return Extracted("pdf", "", readable=False, note="This PDF is password protected.")
    pages: list[str] = []
    total = 0
    for number, page in enumerate(reader.pages[:MAX_PDF_PAGES], start=1):
        text = (page.extract_text() or "").strip()
        if text:
            pages.append(f"== Page {number}\n{text}")
            total += len(text)
        if total > max_chars:
            break
    joined, cut = _bound("\n".join(pages), max_chars)
    if not joined.strip():
        return Extracted(
            "pdf",
            "",
            readable=False,
            note=(
                "This PDF has no extractable text (it is probably a scan or images). "
                "MAYA cannot read the pixels."
            ),
        )
    note = ""
    if len(reader.pages) > MAX_PDF_PAGES:
        note = f"Only the first {MAX_PDF_PAGES} of {len(reader.pages)} pages were read."
    return Extracted("pdf", joined, readable=True, truncated=cut, note=note)


# ------------------------------------------------------------------------------ public


def extract(
    path: Path | str,
    kind: str,
    *,
    max_chars: int = DEFAULT_MAX_CHARS,
    indexed: bool = False,
) -> Extracted:
    """The bounded view of ``path``. ``indexed`` labels paragraphs, cells and shapes with the
    addresses the edit operations use. Never raises for a bad file: it reports unreadable."""
    target = Path(path)
    try:
        if kind in EDITABLE_TEXT_KINDS:
            with target.open("rb") as handle:
                raw = handle.read(max_chars * 4 + 8)  # UTF-8 uses up to 4 bytes per character
            text, cut = _bound(decode_text(raw), max_chars)
            cut = cut or target.stat().st_size > len(raw)
            return Extracted(kind, text, readable=True, truncated=cut)
        if kind == "docx":
            return _extract_docx(target, max_chars, indexed=indexed)
        if kind == "xlsx":
            return _extract_xlsx(target, max_chars, indexed=indexed)
        if kind == "pptx":
            return _extract_pptx(target, max_chars, indexed=indexed)
        if kind == "pdf":
            return _extract_pdf(target, max_chars)
        if kind == "image":
            size = target.stat().st_size
            return Extracted(
                "image",
                "",
                readable=False,
                note=(
                    f"Image file ({size} bytes). MAYA cannot read the pixels of an image "
                    "here, so its content is unknown."
                ),
            )
    except Exception as err:  # corrupt or unsupported file: say so, never crash the turn
        return Extracted(
            kind,
            "",
            readable=False,
            note=f"This {kind} file could not be read ({type(err).__name__}).",
        )
    return Extracted(kind, "", readable=False, note="This file type cannot be read.")


def extract_bytes(data: bytes, kind: str, *, max_chars: int = DEFAULT_MAX_CHARS) -> Extracted:
    """Extraction for in-memory bytes (used by tests and small inline content)."""
    import tempfile

    with tempfile.TemporaryDirectory() as tmp:
        target = Path(tmp) / f"x.{kind}"
        target.write_bytes(data)
        return extract(target, kind, max_chars=max_chars)


__all__ = [
    "EDITABLE_TEXT_KINDS",
    "Extracted",
    "decode_text",
    "docx_paragraphs",
    "extract",
    "extract_bytes",
    "kind_for",
    "pptx_shapes",
]
