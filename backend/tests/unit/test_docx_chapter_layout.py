"""DOCX parity of the chapter layout: chapters start a page, headings keep with next, tables repeat
their header row, long code may split, and a short report gets no page break per heading."""

from __future__ import annotations

from pathlib import Path

from docx import Document as WordDocument
from docx.document import Document as DocxDocument
from docx.oxml.ns import qn
from docx.text.paragraph import Paragraph

from app.automation.docx_engine import create_document_from_model
from app.automation.model import Document


def _blocks(sections: int, words: int) -> list[dict[str, object]]:
    blocks: list[dict[str, object]] = []
    for n in range(1, sections + 1):
        blocks += [
            {"type": "heading", "level": 1, "text": f"Chapter {n}"},
            {"type": "paragraph", "text": "word " * words},
            {"type": "code", "language": "java", "text": "\n".join(["int x = 1;"] * 30)},
            {"type": "table", "headers": ["A", "B"], "rows": [["1", "2"], ["3", "4"]]},
        ]
    return blocks


def _build(tmp_path: Path, **data: object) -> DocxDocument:
    document = Document.model_validate({"title": "Notes", **data})
    result = create_document_from_model(document, tmp_path / "out.docx")
    return WordDocument(str(result.path))


def _h1(doc: DocxDocument) -> list[Paragraph]:
    return [p for p in doc.paragraphs if p.style is not None and p.style.name == "Heading 1"]


def test_long_document_chapters_start_on_a_new_page(tmp_path: Path) -> None:
    doc = _build(tmp_path, cover_page=True, toc=True, blocks=_blocks(3, 1000))
    headings = _h1(doc)
    assert len(headings) == 3
    # The first chapter follows the contents page break already; the others get their own break.
    assert [h.paragraph_format.page_break_before for h in headings] == [None, True, True]


def test_short_report_gets_no_chapter_page_breaks(tmp_path: Path) -> None:
    doc = _build(tmp_path, blocks=_blocks(3, 20))
    assert all(h.paragraph_format.page_break_before is None for h in _h1(doc))


def test_headings_keep_with_next_widow_control_and_header_rows_repeat(tmp_path: Path) -> None:
    doc = _build(tmp_path, blocks=_blocks(1, 20))
    for name in ("Heading 1", "Heading 2", "Heading 3"):
        fmt = doc.styles[name].paragraph_format
        assert fmt.keep_with_next is True and fmt.widow_control is True
    assert doc.styles["Normal"].paragraph_format.widow_control is True
    header_row = doc.tables[0].rows[0]._tr.trPr
    assert header_row is not None and header_row.find(qn("w:tblHeader")) is not None
    code = next(p for p in doc.paragraphs if p.text.startswith("int x"))
    assert code.paragraph_format.keep_together is False  # 30 lines: allowed to split
