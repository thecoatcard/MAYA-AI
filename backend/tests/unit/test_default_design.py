"""A short request with no design still gets a designed file (no extra model call).

The spec here is what a model sends for "a 6 slide deck on AI in healthcare" or "a sales sheet": a
title and plain content, no ``design``, no layout variety. The engine must fill the gaps: a
tone-matched design (contrast-safe), varied slide layouts, a cover page for a longer document and a
formatted workbook.
"""

from __future__ import annotations

import re
from pathlib import Path
from typing import Any

import openpyxl
import pytest
from docx import Document as DocxDocument

from app.automation.auto_design import (
    MAX_SAME_LAYOUT_RUN,
    default_design,
    document_flags,
    layout_of,
    max_run,
    pick_tone,
    vary_layouts,
)
from app.automation.design import AA_RATIO, contrast_report, resolve_deck, resolve_design
from app.automation.verifiers.xlsx_verifier import verify_xlsx
from app.automation.visual_review import check_pages, check_palette, pptx_facts
from app.automation.xlsx_engine import create_spreadsheet
from app.services.document_tools import BuiltDocument, build_document

PLAIN_DECK: list[dict[str, Any]] = [
    {"layout": "bullets", "title": "AI in healthcare", "bullets": ["An overview for clinicians"]},
    {
        "layout": "bullets",
        "title": "Why now",
        "bullets": ["85% of hospitals plan AI pilots", "40% faster image reads"],
    },
    {
        "layout": "bullets",
        "title": "Where it helps",
        "bullets": [
            "Diagnosis: spots patterns in scans",
            "Triage: ranks urgent cases first",
            "Admin: drafts clinical notes",
        ],
    },
    {
        "layout": "bullets",
        "title": "Rollout steps",
        "bullets": ["Pilot one ward", "Measure outcomes", "Train staff", "Scale hospital-wide"],
    },
    {
        "layout": "bullets",
        "title": "Risks",
        "bullets": [
            "Bias in training data",
            "Privacy of patient records",
            "Over-reliance on automation",
            "Unclear accountability",
            "Integration cost",
        ],
    },
    {
        "layout": "bullets",
        "title": "Rules vs Judgement",
        "bullets": [
            "Fixed checklists",
            "Audit trail",
            "Human sign-off",
            "Context",
            "Empathy",
            "Ethics",
        ],
    },
    {
        "layout": "bullets",
        "title": "Benefits",
        "bullets": ["Faster care", "Lower cost", "Fewer errors", "Better access", "Happier staff"],
    },
    {"layout": "bullets", "title": "Thank you", "bullets": ["Questions welcome"]},
]


def build(
    tmp_path: Path, fmt: str, title: str, spec: dict[str, Any], request: str
) -> BuiltDocument:
    work = tmp_path / f"{fmt}-{len(list(tmp_path.iterdir()))}"
    work.mkdir()
    return build_document(file_format=fmt, title=title, spec=spec, request=request, workdir=work)


# ------------------------------------------------------------------------------- tone


@pytest.mark.parametrize(
    ("text", "tone"),
    [
        ("ek 6 slide deck banao AI in healthcare", "health"),
        ("sales ka sheet banao", "finance"),
        ("PDF banao Java notes", "notes"),
        ("a story for kids", "kids"),
        ("investor pitch for our startup", "pitch"),
        ("cloud security architecture", "tech"),
        ("summary of the meeting", "report"),
    ],
)
def test_tone_is_picked_from_the_request(text: str, tone: str) -> None:
    assert pick_tone(text) == tone


def test_default_design_fills_only_gaps_and_keeps_the_models_palette() -> None:
    chosen = {"palette": {"background": "#101010", "text": "#F0F0F0"}, "body_font": "Arial"}
    out = default_design(chosen, "notes")
    assert out["body_font"] == "Arial"
    assert out["palette"] == {"background": "#101010", "text": "#F0F0F0"}
    assert out["heading_style"] == "bar" and out["table_style"] == "striped"
    assert default_design(None, "report")["palette"]["primary"] == "#1F3A5F"


@pytest.mark.parametrize("tone", ["report", "finance", "pitch", "notes", "kids", "tech", "health"])
@pytest.mark.parametrize("deck", [False, True])
def test_every_default_design_is_aa_contrast_safe(tone: str, deck: bool) -> None:
    design = default_design(None, tone, deck=deck)
    theme = (resolve_deck(design, None)[0]) if deck else resolve_design(design, None)[0]
    ratios = contrast_report(theme)
    assert min(ratios.values()) >= AA_RATIO, ratios
    assert check_palette(theme) == []


# ------------------------------------------------------------------------------- decks


def test_plain_bullet_deck_gets_varied_layouts() -> None:
    slides = vary_layouts(PLAIN_DECK)
    layouts = [layout_of(s) for s in slides]
    assert len(slides) == len(PLAIN_DECK)
    assert layouts[0] == "cover"
    assert layouts[-1] == "closing"
    assert len(set(layouts)) >= 5, layouts
    assert max_run(layouts) <= MAX_SAME_LAYOUT_RUN, layouts
    # Nothing is lost: every word of every bullet still appears on its slide.
    for old, new in zip(PLAIN_DECK, slides, strict=True):
        text = set(re.findall(r"\w+", str(new).lower()))
        words = {w for b in old["bullets"] for w in re.findall(r"\w+", b.lower())}
        assert words <= text, (old["title"], words - text)


def test_a_model_designed_deck_is_left_alone() -> None:
    designed: list[dict[str, Any]] = [
        {"layout": "cover", "title": "T", "subtitle": "s"},
        {"layout": "bullets", "title": "A", "bullets": ["x", "y"]},
        {"layout": "bullets", "title": "B", "bullets": ["x", "y"]},
        {"layout": "section", "title": "C"},
    ]
    assert vary_layouts(designed) == designed
    assert vary_layouts(PLAIN_DECK[:3]) == PLAIN_DECK[:3]  # under four slides: untouched


def test_minimal_deck_builds_with_design_variety_and_clean_checks(tmp_path: Path) -> None:
    built = build(
        tmp_path,
        "pptx",
        "AI in healthcare",
        {"slides": PLAIN_DECK},
        "ek 6 slide deck banao AI in healthcare",
    )
    spec = built.effective_spec
    assert spec is not None
    layouts = [layout_of(s) for s in spec["slides"]]
    assert max_run(layouts) <= MAX_SAME_LAYOUT_RUN and len(set(layouts)) >= 5
    design = spec["design"]
    # A health deck now starts from the slide-library theme for its tone (it was "minimal" with a
    # teal palette before the library); the theme brings its own palette.
    assert design["base"] == "editorial-mint" and "palette" not in design
    theme, _style, _notes = resolve_deck(design, None)
    assert min(contrast_report(theme).values()) >= AA_RATIO
    path = tmp_path / "deck.pptx"
    path.write_bytes(built.data)
    findings = check_pages(pptx_facts(path))
    assert [f for f in findings if f.severity == "high"] == [], findings


# ------------------------------------------------------------------------- documents


def long_blocks(sections: int, words: int) -> list[dict[str, Any]]:
    blocks: list[dict[str, Any]] = [{"type": "heading", "level": 1, "text": "Java notes"}]
    for n in range(sections):
        blocks.append({"type": "heading", "level": 2, "text": f"Topic {n + 1}"})
        blocks.append({"type": "paragraph", "text": " ".join(["word"] * words)})
    return blocks


def test_document_flags_follow_length() -> None:
    assert document_flags(long_blocks(1, 50)) == {"cover_page": False, "toc": False}
    assert document_flags(long_blocks(5, 120)) == {"cover_page": True, "toc": False}
    assert document_flags(long_blocks(8, 200)) == {"cover_page": True, "toc": True}


def test_minimal_docx_gets_a_tone_design_and_a_cover_when_long(tmp_path: Path) -> None:
    built = build(
        tmp_path, "docx", "Java notes", {"blocks": long_blocks(6, 120)}, "Java notes banao"
    )
    spec = built.effective_spec
    assert spec is not None
    assert spec["design"]["base"] == "notes" and spec["design"]["heading_style"] == "bar"
    assert spec["cover_page"] is True
    path = tmp_path / "n.docx"
    path.write_bytes(built.data)
    heading = DocxDocument(str(path)).styles["Heading 1"].font
    assert heading.name == "Calibri"


def test_a_stated_page_count_gets_no_automatic_cover_page(tmp_path: Path) -> None:
    built = build(
        tmp_path, "docx", "Java notes", {"blocks": long_blocks(6, 120)}, "Java notes in 3 pages"
    )
    assert built.effective_spec is not None
    assert "cover_page" not in built.effective_spec


def test_a_short_document_gets_no_cover_page(tmp_path: Path) -> None:
    built = build(tmp_path, "docx", "Memo", {"blocks": long_blocks(1, 40)}, "write a memo")
    assert built.effective_spec is not None
    assert not built.effective_spec.get("cover_page")


def test_a_named_preset_is_kept_over_the_tone(tmp_path: Path) -> None:
    built = build(
        tmp_path, "docx", "Java notes", {"blocks": long_blocks(2, 40), "theme": "midnight"}, "x"
    )
    assert built.effective_spec is not None
    assert built.effective_spec["design"]["base"] == "midnight"


# ---------------------------------------------------------------------------- xlsx

SALES = {
    "title": "Sales",
    "headers": ["Region", "Revenue", "Growth", "Closed", "Order ID"],
    "rows": [
        ["North", "$1,200.50", "12%", "2025-01-03", "10023"],
        ["South", "$980", "-3.5%", "2025-02-03", "10024"],
        ["East", "$1,540", "8%", "2025-03-03", "10025"],
        ["West", "$700", "5%", "2025-03-04", "10026"],
    ],
}


def test_minimal_sheet_is_formatted(tmp_path: Path) -> None:
    path = create_spreadsheet([SALES], tmp_path / "s.xlsx")
    assert verify_xlsx(path).status == "verified"
    ws = openpyxl.load_workbook(path).active
    assert ws is not None
    header = ws["A1"]
    assert header.font.bold and header.fill.fill_type == "solid"
    assert header.font.color.rgb != header.fill.start_color.rgb
    assert ws.freeze_panes in ("A2", "B2")
    assert ws.auto_filter.ref == "A1:E5"
    assert ws.column_dimensions["D"].width >= 12
    # Real values with formats, per column; identifiers stay text.
    assert ws["B2"].value == 1200.5 and "$" in ws["B2"].number_format
    assert ws["C2"].value == pytest.approx(0.12) and ws["C2"].number_format == "0.0%"
    assert ws["D2"].number_format == "dd-mmm-yyyy"
    assert ws["E2"].value == "10023"
    # Banded rows, conditional formatting on the key column, a chart beside the table.
    assert ws["A2"].fill.start_color.rgb != ws["A3"].fill.start_color.rgb
    assert ws.conditional_formatting
    sheet: Any = ws
    assert len(sheet._charts) == 1


def test_xlsx_explicit_formats_win_and_palette_follows_the_design(tmp_path: Path) -> None:
    sheet = {**SALES, "number_formats": {2: "0.000"}}
    path = create_spreadsheet(
        [sheet], tmp_path / "s.xlsx", style={"palette": {"primary": "#7A1F5C"}}
    )
    ws = openpyxl.load_workbook(path).active
    assert ws is not None
    assert ws["B2"].number_format == "0.000"
    assert ws["A1"].fill.start_color.rgb.endswith("7A1F5C")


def test_text_only_sheet_has_no_chart_and_ragged_rows_do_not_crash(tmp_path: Path) -> None:
    sheet = {"title": "Notes", "headers": ["Item", "Detail"], "rows": [["a", "x"], ["b"]]}
    path = create_spreadsheet(
        [sheet, {"title": "Notes", "headers": ["A"], "rows": []}], tmp_path / "t.xlsx"
    )
    workbook = openpyxl.load_workbook(path)
    assert workbook.sheetnames == ["Notes", "Notes (2)"]
    notes: Any = workbook["Notes"]
    assert len(notes._charts) == 0


def test_xlsx_through_build_document_keeps_a_design_for_edits(tmp_path: Path) -> None:
    built = build(tmp_path, "xlsx", "Sales", {"sheets": [SALES]}, "sales ka sheet banao")
    assert built.effective_spec is not None
    assert built.effective_spec["design"]["palette"]["primary"] == "#0F3D3E"
