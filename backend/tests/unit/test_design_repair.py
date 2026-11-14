"""A near-miss design from the model keeps its intent (palette, fonts) instead of being dropped."""

from __future__ import annotations

from app.automation.design import (
    CALLOUT_STYLES,
    COVER_STYLES,
    DENSITIES,
    DESIGN_PROMPT_RULE,
    FONT_NAMES,
    HEADING_STYLES,
    TABLE_STYLES,
    DesignSpec,
    nearest_font,
    resolve_design,
)
from app.automation.model import deck_from_dict  # noqa: F401  (import check)
from app.automation.themes import themed

# The exact failing input from a real 10-slide Advanced Java run.
OWNER_DESIGN = {
    "mood": "modern tech",
    "palette": {
        "background": "#0F172A",
        "text": "#E2E8F0",
        "muted": "#94A3B8",
        "accent": "#F97316",
        "primary": "#38BDF8",
        "link": "#7DD3FC",
        "surface": "#1E293B",
    },
    "heading_font": "Helvetica",
    "body_font": "Helvetica",
    "cover": "bold",
    "heading_style": "bold",
    "table_style": "striped",
    "callout_style": "tinted",
    "density": "comfortable",
}


def test_the_prompt_lists_every_allowed_value() -> None:
    for value in (
        *FONT_NAMES,
        *COVER_STYLES,
        *HEADING_STYLES,
        *TABLE_STYLES,
        *CALLOUT_STYLES,
        *DENSITIES,
    ):
        assert value in DESIGN_PROMPT_RULE


def test_the_owners_failing_design_keeps_its_intent_and_its_palette() -> None:
    theme, notes = resolve_design(OWNER_DESIGN, "executive")
    assert theme.fonts.heading_font == "Arial" and theme.fonts.body_font == "Arial"
    assert theme.fonts.heading_stack.startswith('"Arial"')
    assert theme.style.heading == "bar"  # 'bold' -> nearest heading style
    assert theme.style.cover == "bold" and theme.style.table == "striped"
    assert theme.colors.page_bg == "#0F172A" and theme.dark  # the palette survives
    assert theme.colors.accent == "#F97316"
    assert theme.name == "custom-executive"
    assert any("comfortable" in n and "normal" in n for n in notes)
    assert not any("used 'plain'" in n or "used 'executive'" in n for n in notes)


def test_density_comfortable_is_normal() -> None:
    plain, _ = resolve_design({"density": "normal"}, "executive")
    near, _ = resolve_design({"density": "comfortable"}, "executive")
    assert near.sizes == plain.sizes


def test_fonts_repair_to_the_nearest_allowed_family() -> None:
    assert nearest_font("Helvetica") == "Arial"
    assert nearest_font("Helvetica Neue, sans-serif") == "Arial"
    assert nearest_font("Inter") == "Segoe UI"
    assert nearest_font("Playfair Display") == "Georgia"
    assert nearest_font("Calibr") == "Calibri"
    assert nearest_font("Wingdings Zzz") is None


def test_the_palette_reaches_the_pptx_theme() -> None:
    theme = themed("executive", None, DesignSpec.model_validate(OWNER_DESIGN).model_dump())
    assert theme.colors.slide_bg == "#0F172A" and theme.colors.accent == "#F97316"
