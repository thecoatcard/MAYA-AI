"""Themed DOCX rendering from the document model, plus the legacy API."""

from __future__ import annotations

import base64
import zipfile
from pathlib import Path
from typing import Any

import pytest
from docx import Document as WordDocument

from app.automation.docx_engine import (
    RENDERER,
    create_document,
    create_document_from_model,
)
from app.automation.model import Document
from app.automation.themes import THEME_NAMES, get_theme
from app.automation.verifiers.docx_verifier import verify_docx
from tests.support.documents import HINGLISH, TINY_PNG, sample_document


def _document_xml(path: Path) -> str:
    return zipfile.ZipFile(path).read("word/document.xml").decode("utf-8")


@pytest.mark.parametrize("theme", THEME_NAMES)
def test_every_theme_renders_a_verified_docx(tmp_path: Path, theme: str) -> None:
    result = create_document_from_model(sample_document(theme), tmp_path / f"{theme}.docx")
    assert result.renderer == RENDERER == "python-docx-native"
    assert result.theme == theme and result.warnings == []
    report = verify_docx(result.path)
    assert report.status == "verified", report.errors
    assert "heading_hierarchy_consistent" in report.checks_passed
    assert report.metadata["total_tables"] == 2  # metrics grid + data table


def test_structure_headings_lists_tables_and_callouts(tmp_path: Path) -> None:
    path = create_document_from_model(sample_document(), tmp_path / "s.docx").path
    word = WordDocument(str(path))
    styles = {p.style.name for p in word.paragraphs if p.style is not None}
    assert {"Title", "Heading 1", "Heading 2", "List Bullet"} <= styles
    texts = [p.text for p in word.paragraphs]
    assert "Executive summary" in texts and "Appendix" in texts
    assert any(t.startswith("1.  first") for t in texts)  # numbered list
    assert any(t.startswith("WARNING") and "Check this." in t for t in texts)  # callout
    data_table = word.tables[1]
    assert [c.text for c in data_table.rows[0].cells] == ["Segment", "Revenue"]
    assert data_table.rows[1].cells[0].text == "Enterprise"
    xml = _document_xml(path)
    assert "<w:tblHeader/>" in xml  # header row repeats across pages
    assert 'w:type="page"' in xml  # explicit page break


def test_inline_markup_becomes_run_formatting_and_links(tmp_path: Path) -> None:
    path = create_document_from_model(sample_document(), tmp_path / "i.docx").path
    word = WordDocument(str(path))
    paragraph = next(p for p in word.paragraphs if "Plain" in p.text)
    assert "**" not in paragraph.text and "`" not in paragraph.text
    assert any(r.bold and r.text == "bold" for r in paragraph.runs)
    assert any(r.italic and r.text == "italic" for r in paragraph.runs)
    assert "<w:hyperlink" in _document_xml(path)
    relationships = zipfile.ZipFile(path).read("word/_rels/document.xml.rels").decode()
    assert 'Target="https://example.com"' in relationships


def test_javascript_link_is_not_turned_into_a_hyperlink(tmp_path: Path) -> None:
    document = Document.model_validate(
        {"title": "T", "blocks": [{"type": "paragraph", "text": "[x](javascript:alert) ok"}]}
    )
    path = create_document_from_model(document, tmp_path / "j.docx").path
    assert "<w:hyperlink" not in _document_xml(path)
    assert "javascript" not in zipfile.ZipFile(path).read("word/_rels/document.xml.rels").decode()


@pytest.mark.parametrize("theme", THEME_NAMES)
def test_theme_fonts_and_colors_are_applied(tmp_path: Path, theme: str) -> None:
    t = get_theme(theme)
    path = create_document_from_model(sample_document(theme), tmp_path / "t.docx").path
    styles = zipfile.ZipFile(path).read("word/styles.xml").decode()
    xml = _document_xml(path)
    assert f'w:ascii="{t.fonts.body_font}"' in styles
    assert f'w:ascii="{t.fonts.heading_font}"' in styles
    assert f'w:cs="{t.fonts.complex_script_font}"' in styles
    assert t.colors.table_header_bg.lstrip("#").upper() in xml
    assert t.colors.heading.lstrip("#").upper() in styles
    assert ("<w:background" in xml) == t.dark  # dark theme paints the page


def test_devanagari_text_is_stored_verbatim(tmp_path: Path) -> None:
    path = create_document_from_model(sample_document(), tmp_path / "h.docx").path
    assert HINGLISH in _document_xml(path)


def test_heading_levels_are_clamped_so_hierarchy_stays_valid(tmp_path: Path) -> None:
    document = Document.model_validate(
        {
            "title": "T",
            "blocks": [
                {"type": "heading", "level": 3, "text": "Starts deep"},
                {"type": "heading", "level": 1, "text": "Top"},
                {"type": "heading", "level": 3, "text": "Skips a level"},
            ],
        }
    )
    result = create_document_from_model(document, tmp_path / "hh.docx")
    assert any("Heading levels were adjusted" in w for w in result.warnings)
    assert verify_docx(result.path).status == "verified"


def test_image_block_is_embedded_with_alt_text(tmp_path: Path) -> None:
    document = Document.model_validate(
        {
            "title": "T",
            "blocks": [{"type": "image", "src": TINY_PNG, "alt": "A pixel", "caption": "Fig"}],
        }
    )
    path = create_document_from_model(document, tmp_path / "img.docx").path
    assert 'descr="A pixel"' in _document_xml(path)
    assert any(n.startswith("word/media/") for n in zipfile.ZipFile(path).namelist())


def test_workspace_image_path_and_missing_image(tmp_path: Path) -> None:
    root = tmp_path / "ws"
    (root / "img").mkdir(parents=True)
    (root / "img" / "p.png").write_bytes(base64.b64decode(TINY_PNG.split(",", 1)[1]))
    document = Document.model_validate(
        {
            "title": "T",
            "blocks": [
                {"type": "image", "src": "img/p.png", "alt": "here"},
                {"type": "image", "src": "img/none.png", "alt": "gone"},
            ],
        }
    )
    result = create_document_from_model(document, tmp_path / "w.docx", assets_root=root)
    assert len(result.warnings) == 1 and "img/none.png" in result.warnings[0]
    assert "[Image unavailable: gone]" in _document_xml(result.path)


def test_markup_in_content_is_text_not_xml(tmp_path: Path) -> None:
    evil = "</w:t></w:r><w:r><w:t>INJECTED & <b>bold</b>"
    document = Document.model_validate(
        {"title": evil, "blocks": [{"type": "paragraph", "text": evil}]}
    )
    path = create_document_from_model(document, tmp_path / "x.docx").path
    assert verify_docx(path).status == "verified"
    word = WordDocument(str(path))
    assert any(p.text == evil for p in word.paragraphs)


# ------------------------------------------------------------------------- legacy API
def test_legacy_create_document_keeps_its_signature(tmp_path: Path) -> None:
    sections: list[dict[str, Any]] = [
        {"type": "heading1", "text": "1. Summary"},
        {"type": "paragraph", "text": "Body."},
        {"type": "heading2", "text": "1.1 Goals"},
        {"type": "bullet", "items": ["a", "b"]},
        {"type": "heading3", "text": "1.1.1 KR"},
        {"type": "callout", "text": "Note."},
        {"type": "table", "headers": ["H1", "H2"], "rows": [["1", "2"]]},
    ]
    out = create_document(sections, tmp_path / "l.docx", title="Legacy", subtitle="v1")
    positional = create_document(sections, tmp_path / "m.docx", "Legacy")
    assert isinstance(out, Path) and out.is_file() and positional.is_file()
    report = verify_docx(out)
    assert report.status == "verified", report.errors
    assert "verified_1_tables_structure" in report.checks_passed
    with pytest.raises(ValueError, match="theme"):
        create_document(sections, tmp_path / "n.docx", theme="neon")
