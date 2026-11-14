"""A content edit keeps the design of the file (chat tool loop and the task edit path).

Each test builds a file with a NON-default design, applies a content-only edit through the real
edit path (``build_document`` with the working state's last document, ``apply_edit`` for the task
path), and compares the design properties before and after with python-docx, python-pptx and
openpyxl. No model, no network; the PDF is compared at the spec level (re-rendering a PDF needs a
browser).
"""

from __future__ import annotations

import copy
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from docx import Document as DocxDocument
from pptx import Presentation

from app.automation.design_keep import (
    carry_design,
    merge_design,
    requests_restyle,
)
from app.automation.file_edit import apply_edit
from app.services.document_tools import BuiltDocument, build_document

DESIGN: dict[str, Any] = {
    "base": "minimal",
    "mood": "plum print",
    "palette": {
        "background": "#FFFFFF",
        "text": "#1A1A2E",
        "muted": "#4A4A68",
        "accent": "#B4236E",
        "primary": "#7A1F5C",
        "link": "#7A1F5C",
    },
    "heading_font": "Georgia",
    "body_font": "Verdana",
    "cover": "bold",
    "heading_style": "bar",
    "table_style": "striped",
}

DOC_BLOCKS: list[dict[str, Any]] = [
    {"type": "heading", "level": 1, "text": "Quarterly review"},
    {"type": "paragraph", "text": "Revenue grew steadily across all regions this quarter."},
    {"type": "heading", "level": 2, "text": "Regions"},
    {
        "type": "table",
        "headers": ["Region", "Revenue"],
        "rows": [["North", "120"], ["South", "98"]],
    },
    {"type": "bullets", "items": ["Hire two analysts", "Close the audit"]},
]

DECK_SLIDES: list[dict[str, Any]] = [
    {"layout": "cover", "title": "Plum deck", "subtitle": "Overview"},
    {"layout": "bullets", "title": "Goals", "bullets": ["Grow revenue", "Cut costs"]},
    {
        "layout": "cards",
        "title": "Pillars",
        "cards": [{"title": "Speed", "body": "Ship weekly"}, {"title": "Care", "body": "Listen"}],
    },
    {"layout": "closing", "title": "Thank you", "bullets": ["Questions welcome"]},
]


def state_of(built: BuiltDocument, title: str, file_id: str = "f1") -> dict[str, Any]:
    """The working state's ``document`` entry, as ``chat_service._create_document`` stores it."""
    return {
        "format": built.file_type,
        "title": title,
        "file_id": file_id,
        "spec": built.effective_spec,
        "design_state": built.design_state,
    }


def build(tmp_path: Path, fmt: str, title: str, spec: dict[str, Any], **kw: Any) -> BuiltDocument:
    work = tmp_path / f"{fmt}-{len(list(tmp_path.iterdir()))}"
    work.mkdir()
    return build_document(
        file_format=fmt,
        title=title,
        spec=spec,
        request=kw.pop("request", "make the file"),
        workdir=work,
        **kw,
    )


def save(built: BuiltDocument, path: Path) -> Path:
    path.write_bytes(built.data)
    return path


# ------------------------------------------------------------------------------------- docx


def docx_design(path: Path) -> dict[str, Any]:
    doc = DocxDocument(str(path))
    styles = doc.styles
    out: dict[str, Any] = {}
    for name in ("Normal", "Heading 1", "Heading 2", "Title"):
        font = styles[name].font
        color = font.color.rgb if font.color is not None and font.color.type else None
        out[name] = (font.name, font.size, font.bold, str(color))
    section = doc.sections[0]
    out["page"] = (section.page_width, section.page_height, section.left_margin, section.top_margin)
    out["tables"] = [(t.style.name if t.style else None) for t in doc.tables]
    out["shading"] = [
        cell._tc.xml.count("w:shd") for t in doc.tables for row in t.rows for cell in row.cells
    ]
    return out


def test_docx_content_edit_without_design_keeps_the_look(tmp_path: Path) -> None:
    first = build(tmp_path, "docx", "Quarterly review", {"blocks": DOC_BLOCKS, "design": DESIGN})
    default = build(tmp_path, "docx", "Other", {"blocks": DOC_BLOCKS, "theme": "midnight"})
    assert docx_design(save(first, tmp_path / "a.docx")) != docx_design(
        save(default, tmp_path / "d.docx")
    )
    edited_blocks = copy.deepcopy(DOC_BLOCKS)
    edited_blocks[1]["text"] = "Revenue fell slightly in the south this quarter."
    # The model re-sends the content and forgets (or re-plans) the design: it must not matter.
    for resent in ({}, {"theme": "midnight", "design": {"base": "midnight", "mood": "dark"}}):
        second = build(
            tmp_path,
            "docx",
            "Quarterly review",
            {"blocks": edited_blocks, **resent},
            previous=state_of(first, "Quarterly review"),
            request="change the first paragraph: revenue fell in the south",
        )
        assert docx_design(save(second, tmp_path / "b.docx")) == docx_design(tmp_path / "a.docx")
        assert first.effective_spec is not None and second.effective_spec is not None
        assert second.effective_spec["design"] == first.effective_spec["design"]


def test_docx_file_id_keeps_the_design_even_when_the_title_changes(tmp_path: Path) -> None:
    first = build(tmp_path, "docx", "Quarterly review", {"blocks": DOC_BLOCKS, "design": DESIGN})
    second = build(
        tmp_path,
        "docx",
        "Quarterly review v2",
        {"blocks": DOC_BLOCKS},
        previous=state_of(first, "Quarterly review", file_id="abc"),
        edits_file_id="abc",
    )
    assert docx_design(save(second, tmp_path / "b.docx")) == docx_design(
        save(first, tmp_path / "a.docx")
    )


def test_a_different_document_does_not_inherit_the_design(tmp_path: Path) -> None:
    first = build(tmp_path, "docx", "Quarterly review", {"blocks": DOC_BLOCKS, "design": DESIGN})
    other = build(
        tmp_path,
        "docx",
        "Trip plan",
        {"blocks": DOC_BLOCKS},
        previous=state_of(first, "Quarterly review"),
    )
    assert other.effective_spec is not None
    assert other.effective_spec["design"]["palette"]["primary"] != "#7A1F5C"


def test_restyle_changes_only_the_requested_fields(tmp_path: Path) -> None:
    first = build(tmp_path, "docx", "Quarterly review", {"blocks": DOC_BLOCKS, "design": DESIGN})
    second = build(
        tmp_path,
        "docx",
        "Quarterly review",
        {"blocks": DOC_BLOCKS, "design": {"heading_font": "Cambria"}},
        previous=state_of(first, "Quarterly review"),
        request="change the font of the headings to Cambria",
    )
    assert second.effective_spec is not None and first.effective_spec is not None
    new, old = second.effective_spec["design"], first.effective_spec["design"]
    assert new["heading_font"] == "Cambria"
    assert new["body_font"] == old["body_font"] == "Verdana"
    assert new["palette"] == old["palette"]
    assert new["heading_style"] == old["heading_style"]
    assert docx_design(save(second, tmp_path / "b.docx"))["Heading 1"][0] == "Cambria"


def test_restyle_to_a_preset_drops_the_old_palette_but_keeps_other_fields() -> None:
    old = {"base": "minimal", "palette": {"primary": "#7A1F5C"}, "body_font": "Verdana"}
    merged = merge_design(old, {"base": "midnight"})
    assert merged["base"] == "midnight" and "palette" not in merged
    assert merged["body_font"] == "Verdana"


@pytest.mark.parametrize(
    "text",
    [
        "make it dark",
        "change the font to Georgia",
        "use a different colour scheme",
        "theme badlo please",
        "restyle the whole file",
        "make it more modern",
    ],
)
def test_restyle_requests_are_detected(text: str) -> None:
    assert requests_restyle(text)


@pytest.mark.parametrize(
    "text",
    [
        "add a slide about costs",
        "fix the typo in section 2",
        "change the first paragraph to say revenue fell",
        "update the numbers for Q3",
        "ek row add karo",
    ],
)
def test_content_edits_are_not_restyles(text: str) -> None:
    assert not requests_restyle(text)


def test_carry_design_is_a_noop_for_a_first_document() -> None:
    body = {"blocks": [], "design": {"base": "midnight"}}
    out, note = carry_design(
        body, None, fmt="pdf", title="x", edits_file_id=None, request="make a pdf"
    )
    assert out == body and note is None


def test_pdf_spec_keeps_its_design_when_content_changes(tmp_path: Path) -> None:
    """A PDF is re-rendered from the stored spec: the spec, not the file, carries the design."""
    first = build(tmp_path, "pdf", "Java notes", {"blocks": DOC_BLOCKS, "design": DESIGN})
    edited = copy.deepcopy(DOC_BLOCKS)
    edited.append({"type": "paragraph", "text": "A new closing paragraph."})
    second = build(
        tmp_path,
        "pdf",
        "Java notes",
        {"blocks": edited},
        previous=state_of(first, "Java notes"),
        request="add a closing paragraph",
    )
    assert first.effective_spec is not None and second.effective_spec is not None
    assert second.effective_spec["design"] == first.effective_spec["design"]
    assert second.design_state == first.design_state
    assert len(second.effective_spec["blocks"]) == len(DOC_BLOCKS) + 1


def test_the_design_survives_a_spec_too_large_to_store(tmp_path: Path) -> None:
    """``design_state`` is stored beside the spec, so a spec dropped for size loses no look."""
    first = build(tmp_path, "docx", "Quarterly review", {"blocks": DOC_BLOCKS, "design": DESIGN})
    state = state_of(first, "Quarterly review")
    state["spec"] = None
    second = build(
        tmp_path,
        "docx",
        "Quarterly review",
        {"blocks": DOC_BLOCKS},
        previous=state,
    )
    assert docx_design(save(second, tmp_path / "b.docx")) == docx_design(
        save(first, tmp_path / "a.docx")
    )


# ------------------------------------------------------------------------------------- pptx


def deck_design(path: Path) -> list[Any]:
    deck = Presentation(str(path))
    out: list[Any] = []
    for slide in deck.slides:
        shapes: list[Any] = []
        for shape in slide.shapes:
            fill = None
            try:
                if shape.fill.type == 1:
                    fill = str(shape.fill.fore_color.rgb)
            except (AttributeError, TypeError, NotImplementedError):
                fill = None
            fonts: list[Any] = []
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    for run in para.runs:
                        colour = None
                        try:
                            colour = str(run.font.color.rgb)
                        except (AttributeError, TypeError):
                            colour = None
                        fonts.append((run.font.name, run.font.size, colour))
            shapes.append((shape.shape_type, shape.left, shape.top, fill, tuple(fonts[:1])))
        out.append((slide.slide_layout.name, tuple(shapes)))
    return out


def test_pptx_content_edit_keeps_theme_layouts_and_fonts(tmp_path: Path) -> None:
    deck_design_spec = {**DESIGN, "footer_text": "Plum Co", "decorations": ["top_bar"]}
    first = build(
        tmp_path, "pptx", "Plum deck", {"slides": DECK_SLIDES, "design": deck_design_spec}
    )
    edited = copy.deepcopy(DECK_SLIDES)
    edited[1]["bullets"] = ["Grow revenue by ten percent", "Cut costs"]
    second = build(
        tmp_path,
        "pptx",
        "Plum deck",
        {"slides": edited},  # the model forgot the design
        previous=state_of(first, "Plum deck"),
        request="change the first goal to say ten percent",
    )
    before = deck_design(save(first, tmp_path / "a.pptx"))
    after = deck_design(save(second, tmp_path / "b.pptx"))
    assert [layout for layout, _ in before] == [layout for layout, _ in after]
    # Everything but the edited slide is identical, shape by shape (fills, fonts, colours).
    assert before[0] == after[0]
    assert before[2:] == after[2:]
    # The edited slide keeps its fills and fonts too (only text differs).
    assert [s[3] for s in before[1][1]] == [s[3] for s in after[1][1]]
    plain = build(tmp_path, "pptx", "Plain", {"slides": DECK_SLIDES, "theme": "midnight"})
    assert deck_design(save(plain, tmp_path / "c.pptx")) != before


# ------------------------------------------------------------------------------------- xlsx

SHEET = {
    "title": "Sales",
    "headers": ["Region", "Revenue", "Growth"],
    "rows": [["North", "$1,200", "12%"], ["South", "$980", "8%"], ["East", "$1,540", "5%"]],
}


def xlsx_design(path: Path) -> dict[str, Any]:
    ws = openpyxl.load_workbook(path).active
    assert ws is not None
    header = ws["A1"]
    return {
        "header_fill": header.fill.start_color.rgb,
        "header_font": (header.font.name, header.font.bold, header.font.color.rgb),
        "freeze": ws.freeze_panes,
        "widths": {k: v.width for k, v in ws.column_dimensions.items()},
        "formats": [ws.cell(row=2, column=c).number_format for c in range(1, 4)],
        "band": ws["A3"].fill.start_color.rgb,
        "filter": ws.auto_filter.ref is not None,
    }


def test_xlsx_content_edit_keeps_header_style_formats_and_palette(tmp_path: Path) -> None:
    style = {"palette": {"primary": "#7A1F5C", "accent": "#B4236E"}}
    first = build(tmp_path, "xlsx", "Sales", {"sheets": [SHEET], "design": style})
    edited = {**SHEET, "rows": [*SHEET["rows"], ["West", "$700", "9%"]]}
    second = build(
        tmp_path,
        "xlsx",
        "Sales",
        {"sheets": [edited]},
        previous=state_of(first, "Sales"),
        request="add a row for West",
    )
    a = xlsx_design(save(first, tmp_path / "a.xlsx"))
    b = xlsx_design(save(second, tmp_path / "b.xlsx"))
    assert a["header_fill"].endswith("7A1F5C")
    assert a == b


def test_xlsx_inplace_edit_keeps_styles_and_the_chart(tmp_path: Path) -> None:
    """The task path edits cells in place: every style survives."""
    first = build(tmp_path, "xlsx", "Sales", {"sheets": [SHEET]})
    src = save(first, tmp_path / "a.xlsx")
    dest = tmp_path / "b.xlsx"
    outcome = apply_edit(
        "xlsx", src, {"ops": [{"sheet": "Sales", "cell": "A2", "value": "Nord"}]}, dest
    )
    assert outcome.changes == 1
    assert xlsx_design(src) == xlsx_design(dest)


# --------------------------------------------------------------------- docx edit in place


def test_docx_inplace_edit_appended_table_copies_the_existing_table_style(tmp_path: Path) -> None:
    first = build(tmp_path, "docx", "Quarterly review", {"blocks": DOC_BLOCKS, "design": DESIGN})
    src = save(first, tmp_path / "a.docx")
    dest = tmp_path / "b.docx"
    apply_edit(
        "docx",
        src,
        {
            "edits": [{"paragraph_index": 1, "new_text": "Revenue fell in the south."}],
            "append_paragraphs": ["A new note."],
            "append_tables": [{"headers": ["Region", "Revenue"], "rows": [["West", "70"]]}],
        },
        dest,
    )
    before = DocxDocument(str(src))
    after = DocxDocument(str(dest))
    assert len(after.tables) == len(before.tables) + 1
    new_style, old_style = after.tables[-1].style, before.tables[-1].style
    assert new_style is not None and old_style is not None and new_style.name == old_style.name
    # The copied table keeps the cell shading of the original (header fill, stripes).
    assert (
        after.tables[-1]._tbl.xml.count("w:shd") >= before.tables[-1]._tbl.xml.count("w:shd") // 2
    )
    appended = after.paragraphs[-1]
    assert appended.text == "A new note."
    assert appended.style is not None and appended.style.name == "Normal"
    # Styles of the document are untouched.
    assert docx_design(src)["Heading 1"] == docx_design(dest)["Heading 1"]
