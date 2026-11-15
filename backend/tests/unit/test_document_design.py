"""AI-designed documents: DesignSpec validation, contrast repair and the runtime Theme."""

from __future__ import annotations

from typing import Any

import pytest

from app.automation.design import (
    AA_RATIO,
    FONT_ALLOWLIST,
    DesignSpec,
    contrast_ratio,
    contrast_report,
    ensure_contrast,
    normalize_hex,
    resolve_design,
)
from app.automation.html_render import render_document_html
from app.automation.model import document_from_dict
from app.automation.themes import THEMES, themed
from app.tasks.dispatch import _apply_user_style

KIDS: dict[str, Any] = {
    "base": "minimal",
    "mood": "playful kids",
    "palette": {
        "background": "#FFF8E1",
        "text": "#3B2F6B",
        "muted": "#6B5B95",
        "accent": "#B83F00",
        "primary": "#7048E8",
        "link": "#0B5FA5",
        "surface": "#FFEFC2",
    },
    "heading_font": "Comic Sans MS",
    "body_font": "Verdana",
    "cover": "bold",
    "heading_style": "bar",
    "table_style": "striped",
    "callout_style": "outlined",
    "density": "airy",
    "body_pt": 12,
}
LEGAL: dict[str, Any] = {
    "base": "executive",
    "mood": "sober legal",
    "palette": {
        "background": "#FFFFFF",
        "text": "#111111",
        "muted": "#444444",
        "accent": "#7A1F1F",
        "primary": "#1B2A41",
        "link": "#1B2A41",
        "surface": "#F4F4F4",
    },
    "heading_font": "Garamond",
    "body_font": "Times New Roman",
    "cover": "plain",
    "heading_style": "caps",
    "table_style": "lined",
    "callout_style": "bar",
    "density": "compact",
}


def _assert_aa(theme: Any) -> None:
    report = contrast_report(theme)
    for key in ("text_on_bg", "muted_on_bg", "accent_on_bg", "primary_on_bg", "link_on_bg"):
        assert report[key] >= AA_RATIO, (key, report[key])
    assert report["text_on_surface"] >= AA_RATIO
    assert report["table_header"] >= AA_RATIO


def test_two_briefs_give_distinct_valid_themes() -> None:
    kids, kids_notes = resolve_design(KIDS, "notes")
    legal, legal_notes = resolve_design(LEGAL, "notes")
    assert kids_notes == [] and legal_notes == []
    _assert_aa(kids)
    _assert_aa(legal)
    assert kids.colors.page_bg != legal.colors.page_bg
    assert kids.colors.primary != legal.colors.primary
    assert kids.fonts.heading_font == "Comic Sans MS" and legal.fonts.heading_font == "Garamond"
    assert kids.style != legal.style
    assert kids.sizes.line_height > legal.sizes.line_height  # airy vs compact
    kids_html = _html(KIDS)
    legal_html = _html(LEGAL)
    assert "#FFF8E1" in kids_html and "#FFF8E1" not in legal_html
    assert "Comic Sans MS" in kids_html and "Garamond" in legal_html
    assert kids_html != legal_html


def _html(design: dict[str, Any]) -> str:
    doc = document_from_dict(
        {
            "theme": "notes",
            "design": design,
            "title": "T",
            "blocks": [
                {"type": "heading", "level": 1, "text": "H"},
                {"type": "table", "headers": ["a", "b"], "rows": [["1", "2"]]},
                {"type": "callout", "kind": "info", "text": "x"},
            ],
        }
    )
    return render_document_html(doc)


@pytest.mark.parametrize(
    "bg,text",
    [
        ("#FFFFFF", "#EEEEEE"),
        ("#000000", "#222222"),
        ("#FFF8E1", "#CCCCCC"),
        ("#0F172A", "#334155"),
    ],
)
def test_low_contrast_text_is_repaired_not_ignored(bg: str, text: str) -> None:
    theme, notes = resolve_design(
        {"palette": {"background": bg, "text": text, "muted": text, "accent": text}}, "executive"
    )
    _assert_aa(theme)
    assert any("contrast" in n for n in notes)  # the repair is reported, never silent
    assert theme.colors.page_bg == bg


def test_accent_on_background_aa_is_enforced() -> None:
    theme, notes = resolve_design(
        {"palette": {"background": "#FFFFFF", "accent": "#FFFF00"}}, "executive"
    )
    assert contrast_ratio(theme.colors.accent, "#FFFFFF") >= AA_RATIO
    assert any(n.startswith("accent") for n in notes)


def test_dark_background_is_detected_and_passes() -> None:
    theme, _ = resolve_design({"palette": {"background": "#101820", "text": "#EAEAEA"}}, "notes")
    assert theme.dark
    _assert_aa(theme)


def test_invalid_parts_are_repaired_deterministically() -> None:
    spec = {
        "base": "nope",
        "palette": {"background": "javascript:1", "accent": "#12G456", "text": "abc"},
        "heading_font": "Papyrus",
        "cover": "wild",
        "body_pt": 99,
        "heading_scale": 0.1,
    }
    first = resolve_design(spec, "notes")
    second = resolve_design(spec, "notes")
    assert first == second
    theme, notes = first
    text = " | ".join(notes)
    for needle in ("base", "palette.background", "palette.accent", "heading_font", "cover"):
        assert needle in text
    assert theme.colors.text == "#AABBCC" or contrast_ratio(theme.colors.text, "#FFFFFF") >= 4.5
    assert theme.fonts.heading_font == THEMES["notes"].fonts.heading_font
    assert theme.sizes.body_pt == 13.0
    assert theme.style.cover == "band"
    _assert_aa(theme)


def test_fonts_only_from_allowlist() -> None:
    for family in FONT_ALLOWLIST:
        theme, notes = resolve_design({"heading_font": family, "body_font": family}, "notes")
        assert theme.fonts.heading_font == family and not notes
        assert f'"{family}"' in theme.fonts.heading_stack
    theme, _ = resolve_design({"body_font": "Zapfino"}, "notes")
    assert theme.fonts.body_font in FONT_ALLOWLIST


def test_normalize_hex_and_ensure_contrast() -> None:
    assert normalize_hex("abc") == "#AABBCC"
    assert normalize_hex("#aabbcc") == "#AABBCC"
    assert normalize_hex("red") is None and normalize_hex(5) is None
    passing = "#222222"
    assert ensure_contrast(passing, "#FFFFFF") == passing
    assert contrast_ratio(ensure_contrast("#999999", "#FFFFFF"), "#FFFFFF") >= AA_RATIO


def test_user_accent_wins_over_design_and_stays_readable() -> None:
    theme, _ = resolve_design(KIDS, "notes", user_accent="#0B6E4F")
    assert theme.colors.accent == "#0B6E4F" and theme.colors.primary == "#0B6E4F"
    bright, notes = resolve_design(KIDS, "notes", user_accent="#FFEB3B")
    assert contrast_ratio(bright.colors.accent, bright.colors.page_bg) >= AA_RATIO
    assert any("accent" in n for n in notes)


def test_user_theme_replaces_design_and_accent_is_baked_into_pptx_design() -> None:
    out: dict[str, Any] = {"file_type": "pdf", "theme": "notes", "design": dict(KIDS)}
    _apply_user_style(out, {"requirements": {"style": {"theme": "midnight"}}})
    assert out["theme"] == "midnight" and "design" not in out
    deck: dict[str, Any] = {"file_type": "pptx", "theme": "executive"}
    _apply_user_style(deck, {"requirements": {"style": {"accent_color": "#0b6e4f"}}})
    assert deck["design"]["palette"]["accent"] == "#0B6E4F"


def test_no_design_is_the_unchanged_preset() -> None:
    assert themed("executive") is THEMES["executive"]
    assert resolve_design(None, "midnight")[0] is THEMES["midnight"]
    assert themed("notes", None, KIDS).name.startswith("custom-")
    assert DesignSpec().palette.accent == ""


def test_designed_theme_renders_in_docx_and_pptx(tmp_path: Any) -> None:
    from app.automation.docx_engine import create_document_from_model
    from app.automation.model import deck_from_dict
    from app.automation.pptx_engine import create_deck

    doc = document_from_dict(
        {
            "theme": "notes",
            "design": KIDS,
            "title": "Kids",
            "blocks": [{"type": "heading", "level": 1, "text": "Hi"}],
        }
    )
    assert create_document_from_model(doc, tmp_path / "a.docx").path.exists()
    deck = deck_from_dict(
        {
            "theme": "executive",
            "design": LEGAL,
            "title": "Pitch",
            "slides": [{"layout": "title", "title": "Pitch", "subtitle": "x"}],
        }
    )
    assert create_deck(deck, tmp_path / "a.pptx").path.exists()
