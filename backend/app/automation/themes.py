"""Document themes: one plain-data object drives the PDF, PPTX and DOCX renderers.

A theme is pure data (colors, font stacks, sizes, spacing). It contains no behavior, so the
three renderers stay visually consistent and a new theme needs no renderer change.

Font stacks are CSS lists for the browser renderer. Every stack ends with Unicode-capable
families (Nirmala UI, Noto Sans Devanagari, Mangal, Arial Unicode MS) so Devanagari and
Hinglish text falls back to a font that has the glyphs. PPTX and DOCX use the single
``*_font`` family names because those formats name one font per run.
"""

from __future__ import annotations

from dataclasses import dataclass, fields, replace
from typing import Any

DEFAULT_THEME = "executive"

_UNICODE_FALLBACK = (
    '"Nirmala UI", "Noto Sans Devanagari", "Noto Sans", "Mangal", "Arial Unicode MS", sans-serif'
)


@dataclass(frozen=True)
class ThemeColors:
    """All colors are ``#RRGGBB`` hex strings."""

    page_bg: str
    text: str
    muted: str
    heading: str
    primary: str
    accent: str
    border: str
    surface: str
    table_header_bg: str
    table_header_text: str
    table_stripe: str
    code_bg: str
    code_text: str
    link: str
    info_bg: str
    info_fg: str
    warning_bg: str
    warning_fg: str
    success_bg: str
    success_fg: str
    # Slide-only surfaces
    slide_bg: str
    slide_title_bg: str
    slide_title_text: str
    card_bg: str


@dataclass(frozen=True)
class ThemeFonts:
    """CSS font stacks (browser) and single family names (PPTX/DOCX)."""

    heading_stack: str
    body_stack: str
    mono_stack: str
    heading_font: str
    body_font: str
    mono_font: str
    complex_script_font: str


@dataclass(frozen=True)
class ThemeSizes:
    """Typography in points; spacing in points unless stated."""

    body_pt: float
    small_pt: float
    h1_pt: float
    h2_pt: float
    h3_pt: float
    title_pt: float
    subtitle_pt: float
    code_pt: float
    line_height: float
    block_gap_pt: float
    page_margin_mm: float
    # Slide typography
    slide_title_pt: float
    slide_body_pt: float
    slide_min_body_pt: float
    slide_big_pt: float


@dataclass(frozen=True)
class ThemeStyle:
    """Treatments the browser renderer applies (cover, headings, tables, callouts).

    The presets use the defaults, which print exactly as before. A designed theme
    (``design.py``) may choose others; PPTX and DOCX take colours and fonts from the theme and
    ignore these.
    """

    cover: str = "band"  # band | plain | bold | sidebar
    heading: str = "plain"  # plain | rule | bar | caps
    table: str = "filled"  # filled | lined | striped | minimal
    callout: str = "tinted"  # tinted | outlined | bar


@dataclass(frozen=True)
class Theme:
    name: str
    label: str
    dark: bool
    colors: ThemeColors
    fonts: ThemeFonts
    sizes: ThemeSizes
    style: ThemeStyle = ThemeStyle()


def _sizes(**overrides: float) -> ThemeSizes:
    base: dict[str, float] = {
        "body_pt": 10.5,
        "small_pt": 8.5,
        "h1_pt": 19,
        "h2_pt": 14.5,
        "h3_pt": 12,
        "title_pt": 28,
        "subtitle_pt": 13,
        "code_pt": 9,
        "line_height": 1.5,
        "block_gap_pt": 9,
        "page_margin_mm": 20,
        "slide_title_pt": 30,
        "slide_body_pt": 20,
        "slide_min_body_pt": 14,
        "slide_big_pt": 44,
    }
    base.update(overrides)
    return ThemeSizes(**base)


EXECUTIVE = Theme(
    name="executive",
    label="Executive (navy, serif accent)",
    dark=False,
    colors=ThemeColors(
        page_bg="#FFFFFF",
        text="#1F2933",
        muted="#5B6770",
        heading="#12294D",
        primary="#12294D",
        accent="#B8860B",
        border="#D5DBE3",
        surface="#F3F6FA",
        table_header_bg="#12294D",
        table_header_text="#FFFFFF",
        table_stripe="#F5F7FA",
        code_bg="#F1F3F6",
        code_text="#1F2933",
        link="#1D5FAD",
        info_bg="#E8F0FB",
        info_fg="#12294D",
        warning_bg="#FFF4DB",
        warning_fg="#6B4A00",
        success_bg="#E6F4EA",
        success_fg="#14532D",
        slide_bg="#F5F7FB",
        slide_title_bg="#12294D",
        slide_title_text="#FFFFFF",
        card_bg="#FFFFFF",
    ),
    fonts=ThemeFonts(
        heading_stack=f'Georgia, "Times New Roman", "Noto Serif", serif, {_UNICODE_FALLBACK}',
        body_stack=f'"Segoe UI", Calibri, "Helvetica Neue", Arial, {_UNICODE_FALLBACK}',
        mono_stack=f'Consolas, "Courier New", monospace, {_UNICODE_FALLBACK}',
        heading_font="Georgia",
        body_font="Calibri",
        mono_font="Consolas",
        complex_script_font="Nirmala UI",
    ),
    sizes=_sizes(),
)

MINIMAL = Theme(
    name="minimal",
    label="Minimal (light, airy)",
    dark=False,
    colors=ThemeColors(
        page_bg="#FFFFFF",
        text="#222222",
        muted="#707070",
        heading="#111111",
        primary="#2F6FEB",
        accent="#2F6FEB",
        border="#E3E3E3",
        surface="#F7F7F8",
        table_header_bg="#F0F2F5",
        table_header_text="#111111",
        table_stripe="#FAFAFB",
        code_bg="#F5F5F6",
        code_text="#222222",
        link="#2F6FEB",
        info_bg="#EEF4FF",
        info_fg="#1E3A8A",
        warning_bg="#FFF8E6",
        warning_fg="#7A5200",
        success_bg="#EAF7EE",
        success_fg="#166534",
        slide_bg="#FFFFFF",
        slide_title_bg="#FFFFFF",
        slide_title_text="#111111",
        card_bg="#F7F7F8",
    ),
    fonts=ThemeFonts(
        heading_stack=f'"Segoe UI", Calibri, "Helvetica Neue", Arial, {_UNICODE_FALLBACK}',
        body_stack=f'"Segoe UI", Calibri, "Helvetica Neue", Arial, {_UNICODE_FALLBACK}',
        mono_stack=f'Consolas, "Courier New", monospace, {_UNICODE_FALLBACK}',
        heading_font="Calibri",
        body_font="Calibri",
        mono_font="Consolas",
        complex_script_font="Nirmala UI",
    ),
    sizes=_sizes(body_pt=11, h1_pt=20, h2_pt=15, line_height=1.6, block_gap_pt=10),
)

MIDNIGHT = Theme(
    name="midnight",
    label="Midnight (dark)",
    dark=True,
    colors=ThemeColors(
        page_bg="#0F172A",
        text="#E2E8F0",
        muted="#94A3B8",
        heading="#F8FAFC",
        primary="#38BDF8",
        accent="#38BDF8",
        border="#2A3650",
        surface="#18233D",
        table_header_bg="#1E3A5F",
        table_header_text="#F8FAFC",
        table_stripe="#131D34",
        code_bg="#0B1224",
        code_text="#CBD5E1",
        link="#7DD3FC",
        info_bg="#16304F",
        info_fg="#BAE6FD",
        warning_bg="#3B2F12",
        warning_fg="#FDE68A",
        success_bg="#12351F",
        success_fg="#BBF7D0",
        slide_bg="#0F172A",
        slide_title_bg="#0B1224",
        slide_title_text="#F8FAFC",
        card_bg="#18233D",
    ),
    fonts=ThemeFonts(
        heading_stack=f'"Segoe UI Semibold", "Segoe UI", Calibri, Arial, {_UNICODE_FALLBACK}',
        body_stack=f'"Segoe UI", Calibri, "Helvetica Neue", Arial, {_UNICODE_FALLBACK}',
        mono_stack=f'Consolas, "Courier New", monospace, {_UNICODE_FALLBACK}',
        heading_font="Calibri",
        body_font="Calibri",
        mono_font="Consolas",
        complex_script_font="Nirmala UI",
    ),
    sizes=_sizes(),
)

NOTES = Theme(
    name="notes",
    label="Notes (clean study notes, teal accent, print-friendly)",
    dark=False,
    colors=ThemeColors(
        page_bg="#FFFFFF",
        text="#1B1F23",
        muted="#59636E",
        heading="#0B3D5C",
        primary="#0B3D5C",
        accent="#0F766E",
        border="#D0D7DE",
        surface="#F4F8F8",
        table_header_bg="#0B3D5C",
        table_header_text="#FFFFFF",
        table_stripe="#F6F8FA",
        code_bg="#F3F5F7",
        code_text="#1B1F23",
        link="#0B5FA5",
        info_bg="#E7F3F8",
        info_fg="#0B3D5C",
        warning_bg="#FFF3D6",
        warning_fg="#6B4A00",
        success_bg="#E3F4EA",
        success_fg="#14532D",
        slide_bg="#FFFFFF",
        slide_title_bg="#0B3D5C",
        slide_title_text="#FFFFFF",
        card_bg="#F4F8F8",
    ),
    fonts=ThemeFonts(
        heading_stack=(
            '"Segoe UI Semibold", "Segoe UI", Calibri, "Helvetica Neue", Arial, '
            + _UNICODE_FALLBACK
        ),
        body_stack=f'"Segoe UI", Calibri, "Helvetica Neue", Arial, {_UNICODE_FALLBACK}',
        mono_stack=f'Consolas, "Courier New", monospace, {_UNICODE_FALLBACK}',
        heading_font="Calibri",
        body_font="Calibri",
        mono_font="Consolas",
        complex_script_font="Nirmala UI",
    ),
    sizes=_sizes(
        body_pt=10.5,
        h1_pt=21,
        h2_pt=14,
        h3_pt=11.5,
        line_height=1.45,
        block_gap_pt=6,
        page_margin_mm=16,
    ),
)


def _build_library() -> dict[str, Theme]:
    from app.automation.deck_themes import build_deck_themes  # local: it imports this module lazily

    return build_deck_themes()


THEMES: dict[str, Theme] = {t.name: t for t in (EXECUTIVE, MINIMAL, MIDNIGHT, NOTES)}
# The four presets above stay first and unchanged. The slide theme library (25 glass and editorial
# themes, see deck_themes.py) is registered after them, so every list of theme names sees it.
DOCUMENT_THEME_NAMES: tuple[str, ...] = tuple(THEMES)
THEMES.update(_build_library())
THEME_NAMES: tuple[str, ...] = tuple(THEMES)


def compact_sizes(sizes: ThemeSizes) -> ThemeSizes:
    """The ``compact`` density of a theme's sizes: tighter lines, gaps and page margins."""
    return replace(
        sizes,
        body_pt=round(sizes.body_pt - 0.5, 2),
        line_height=round(max(1.3, sizes.line_height - 0.1), 2),
        block_gap_pt=round(sizes.block_gap_pt * 0.7, 2),
        page_margin_mm=max(12, sizes.page_margin_mm - 3),
    )


def get_theme(name: str | None) -> Theme:
    """Return the named theme; an unknown name is an error, never a silent default."""
    key = DEFAULT_THEME if name is None else name
    try:
        return THEMES[key]
    except KeyError:
        allowed = ", ".join(THEME_NAMES)
        raise ValueError(f"Unknown theme '{name}'. Allowed themes: {allowed}") from None


def themed(
    name: str | None, accent_color: str | None = None, design: dict[str, Any] | None = None
) -> Theme:
    """The theme for a document: the named preset, or the model's ``design`` resolved on top of
    it (validated and contrast-repaired), with the user's accent colour winning either way."""
    if design:
        from app.automation.design import resolve_design  # local: design imports this module

        return resolve_design(design, name, user_accent=accent_color)[0]
    theme = get_theme(name)
    if not accent_color:
        return theme
    colors = replace(theme.colors, accent=accent_color, primary=accent_color, link=accent_color)
    return replace(theme, colors=colors)


def theme_field_names() -> dict[str, list[str]]:
    """Field names per theme section (used by completeness tests)."""
    return {
        "colors": [f.name for f in fields(ThemeColors)],
        "fonts": [f.name for f in fields(ThemeFonts)],
        "sizes": [f.name for f in fields(ThemeSizes)],
    }
