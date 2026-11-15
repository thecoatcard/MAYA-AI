"""Theme data: completeness and consistency (one theme drives PDF, PPTX and DOCX)."""

from __future__ import annotations

import re
from dataclasses import asdict

import pytest

from app.automation.themes import (
    DEFAULT_THEME,
    THEME_NAMES,
    THEMES,
    get_theme,
    theme_field_names,
)

HEX = re.compile(r"^#[0-9A-Fa-f]{6}$")


def test_required_themes_exist() -> None:
    assert {"executive", "minimal", "midnight"} <= set(THEME_NAMES)
    assert DEFAULT_THEME in THEMES


@pytest.mark.parametrize("name", THEME_NAMES)
def test_theme_is_complete_and_well_formed(name: str) -> None:
    theme = get_theme(name)
    assert theme.name == name and theme.label
    for key in theme_field_names()["colors"]:
        assert HEX.match(getattr(theme.colors, key)), f"{name}.colors.{key}"
    fonts = asdict(theme.fonts)
    assert all(isinstance(v, str) and v for v in fonts.values())
    for key, value in asdict(theme.sizes).items():
        assert value > 0, f"{name}.sizes.{key}"
    assert theme.sizes.slide_min_body_pt <= theme.sizes.slide_body_pt


@pytest.mark.parametrize("name", THEME_NAMES)
def test_font_stacks_include_unicode_capable_fallback(name: str) -> None:
    fonts = get_theme(name).fonts
    for stack in (fonts.heading_stack, fonts.body_stack, fonts.mono_stack):
        assert "Nirmala UI" in stack and "Noto Sans Devanagari" in stack
        assert stack.rstrip().endswith("sans-serif")
    assert fonts.complex_script_font == "Nirmala UI"


def test_midnight_is_dark_and_others_are_light() -> None:
    assert get_theme("midnight").dark
    assert not get_theme("executive").dark and not get_theme("minimal").dark


def test_themes_are_visually_distinct() -> None:
    primaries = {t.colors.primary for t in THEMES.values()}
    backgrounds = {t.colors.page_bg for t in THEMES.values()}
    assert len(primaries) == len(THEMES)
    assert len(backgrounds) >= 2


def test_unknown_theme_is_an_error_not_a_silent_default() -> None:
    with pytest.raises(ValueError, match="Unknown theme 'neon'"):
        get_theme("neon")
    assert get_theme(None).name == DEFAULT_THEME
