# =============================================================================================
# SCHEMA SUMMARY (AI-facing design fields). Keep in step with DesignSpec / DesignPalette below.
#
# Shared by documents (PDF, DOCX, HTML) and decks (PPTX). Every field is optional; a bad value is
# repaired to the nearest allowed value and reported in the notes, never silently dropped.
#
# palette.background      #RRGGBB solid page colour (deck: gradient start)
# palette.background_end  #RRGGBB gradient end (deck only; empty = solid background)
# palette.text            #RRGGBB body text (WCAG AA 4.5:1 on background, repaired)
# palette.muted           #RRGGBB secondary text (AA repaired)
# palette.accent          #RRGGBB main accent (rules, dots, bars)
# palette.accent2         #RRGGBB second accent (deck decorations; default primary)
# palette.primary         #RRGGBB title band / table header colour
# palette.link            #RRGGBB hyperlink colour (AA repaired)
# palette.surface         #RRGGBB card / panel fill
# heading_font, body_font one of FONT_NAMES (allowlist; near misses map to the nearest family)
# title_scale             number 0.8-1.4, multiplier on deck title sizes (default 1.0)
# corner_radius           number 0-24, panel corner radius in points (default 12, 0 = square)
# shape_style             one of SHAPE_STYLES: flat | outlined | soft-shadow | glass
# decorations             list of DECORATIONS: top_bar | side_bar | corner_circle | grid_dots |
#                         divider_line | none (default ["divider_line"])
# slide_number            true | false (deck; default true; the cover never shows it)
# footer_text             short deck footer text, max 60 characters (default: the deck title)
# document fields         cover, heading_style, table_style, callout_style, density, body_pt,
#                         heading_scale, mood, base (see DesignSpec and DESIGN_PROMPT_RULE)
#
# The PPTX layouts a slide may use (the closed list, see app.automation.model.SLIDE_LAYOUTS):
# cover, section, bullets, two_column, cards, stat_callout, timeline, comparison, table, code,
# quote, closing, image. Legacy names "title" -> cover and "metrics" -> stat_callout still load.
# =============================================================================================
"""AI-designed documents: a model-chosen ``DesignSpec`` validated and turned into a runtime Theme.

The named themes (``themes.py``) stay as presets. For each request the model may also fill a
``DesignSpec`` (one structured field folded into the existing spec/outline call): a palette, a
heading/body font pairing from a safe allowlist, cover / heading / table / callout treatments,
density and sizes. :func:`resolve_design` validates it strictly and *repairs* every bad part
deterministically (never silently ignores it; each repair is returned as a note):

* colours must be ``#RRGGBB`` (``#RGB`` and bare hex are normalised, anything else is replaced by
  the preset's colour);
* WCAG AA (4.5:1): text, muted text, accent, primary and link are all checked against the page
  background (and text against the surface). A failing colour keeps its hue and is moved toward
  black or white until it passes;
* fonts only from :data:`FONT_ALLOWLIST` (families that render in Edge print, PPTX and DOCX on
  Windows/Office); anything else falls back to the preset's font;
* sizes are clamped; enums fall back to the default.

The user's explicit choices win: a named theme the user stated replaces the whole design (the
caller drops it), and a stated accent colour replaces the design's accent, primary and link.
"""

from __future__ import annotations

import difflib
import re
from dataclasses import dataclass, replace
from typing import Any

from pydantic import BaseModel, Field

from app.automation.deck_themes import (
    DECK_THEME_NAMES,
    DeckThemeSpec,
    default_transition,
    spec_for_theme_name,
)
from app.automation.themes import (
    _UNICODE_FALLBACK,
    DEFAULT_THEME,
    THEME_NAMES,
    THEMES,
    Theme,
    ThemeColors,
    ThemeSizes,
    ThemeStyle,
    compact_sizes,
)

AA_RATIO = 4.5

COVER_STYLES = ("band", "plain", "bold", "sidebar")
HEADING_STYLES = ("plain", "rule", "bar", "caps")
TABLE_STYLES = ("filled", "lined", "striped", "minimal")
CALLOUT_STYLES = ("tinted", "outlined", "bar")
DENSITIES = ("compact", "normal", "airy")
SHAPE_STYLES = ("flat", "outlined", "soft-shadow", "glass")
DECORATIONS = ("top_bar", "side_bar", "corner_circle", "grid_dots", "divider_line", "none")

# family -> (css generic fallback chain). Only families that ship with Windows or Office, so Edge
# print, PowerPoint and Word render them without an embedded font.
FONT_ALLOWLIST: dict[str, str] = {
    "Calibri": "Carlito, Arial, sans-serif",
    "Segoe UI": "Calibri, Arial, sans-serif",
    "Arial": "Helvetica, sans-serif",
    "Verdana": "Tahoma, Arial, sans-serif",
    "Tahoma": "Verdana, Arial, sans-serif",
    "Trebuchet MS": "Calibri, Arial, sans-serif",
    "Century Gothic": "Calibri, Arial, sans-serif",
    "Corbel": "Calibri, Arial, sans-serif",
    "Candara": "Calibri, Arial, sans-serif",
    "Georgia": '"Times New Roman", serif',
    "Cambria": 'Georgia, "Times New Roman", serif',
    "Times New Roman": "Times, serif",
    "Palatino Linotype": 'Georgia, "Times New Roman", serif',
    "Garamond": 'Georgia, "Times New Roman", serif',
    "Comic Sans MS": '"Segoe UI", Arial, sans-serif',
    "Impact": 'Arial, "Segoe UI", sans-serif',
}
FONT_NAMES: tuple[str, ...] = tuple(FONT_ALLOWLIST)
_LIBRARY: frozenset[str] = frozenset(DECK_THEME_NAMES)

_HEX6 = re.compile(r"^#?([0-9A-Fa-f]{6})$")
_HEX3 = re.compile(r"^#?([0-9A-Fa-f]{3})$")

MIN_BODY_PT, MAX_BODY_PT = 9.0, 13.0
MIN_SCALE, MAX_SCALE = 0.85, 1.35

# Page fitting (see ``app.automation.page_fit``): when a request states an exact page count the
# renderer may print the same content denser (negative step) or looser (positive step). One step
# moves the body size, line height, block gap and page margin a little; every value stays inside
# the readable limits below, so text never shrinks under MIN_BODY_PT and a page is never padded.
FIT_MIN_STEP, FIT_MAX_STEP = -3, 3
FIT_BODY_STEP_PT = 0.5
FIT_LINE_HEIGHT_STEP = 0.05
FIT_MARGIN_STEP_MM = 2.0
FIT_GAP_TIGHTER, FIT_GAP_LOOSER = 0.85, 1.18
MIN_LINE_HEIGHT, MAX_LINE_HEIGHT = 1.3, 1.8
MIN_PAGE_MARGIN_MM, MAX_PAGE_MARGIN_MM = 12.0, 26.0
MIN_BLOCK_GAP_PT, MAX_BLOCK_GAP_PT = 4.0, 18.0


def _toward(current: float, wanted: float, low: float, high: float) -> float:
    """``wanted`` clamped to ``low``-``high``, but never moved against the direction asked for (a
    value that is already outside the limits stays where it is)."""
    if wanted < current:
        return min(current, max(low, wanted))
    return max(current, min(high, wanted))


def fit_sizes(sizes: ThemeSizes, step: int) -> ThemeSizes:
    """``sizes`` at page-fit ``step`` (``FIT_MIN_STEP`` densest to ``FIT_MAX_STEP`` loosest; 0 is
    unchanged). Headings, code and small text keep their size: only the body rhythm moves."""
    step = max(FIT_MIN_STEP, min(FIT_MAX_STEP, int(step)))
    if step == 0:
        return sizes
    count = abs(step)
    sign = 1 if step > 0 else -1
    gap = (FIT_GAP_LOOSER if step > 0 else FIT_GAP_TIGHTER) ** count
    return replace(
        sizes,
        body_pt=round(
            _toward(
                sizes.body_pt,
                sizes.body_pt + sign * FIT_BODY_STEP_PT * count,
                MIN_BODY_PT,
                MAX_BODY_PT,
            ),
            2,
        ),
        line_height=round(
            _toward(
                sizes.line_height,
                sizes.line_height + sign * FIT_LINE_HEIGHT_STEP * count,
                MIN_LINE_HEIGHT,
                MAX_LINE_HEIGHT,
            ),
            2,
        ),
        block_gap_pt=round(
            _toward(
                sizes.block_gap_pt, sizes.block_gap_pt * gap, MIN_BLOCK_GAP_PT, MAX_BLOCK_GAP_PT
            ),
            2,
        ),
        page_margin_mm=round(
            _toward(
                sizes.page_margin_mm,
                sizes.page_margin_mm + sign * FIT_MARGIN_STEP_MM * count,
                MIN_PAGE_MARGIN_MM,
                MAX_PAGE_MARGIN_MM,
            ),
            2,
        ),
    )


def fit_theme(theme: Theme, step: int) -> Theme:
    """``theme`` with its sizes at page-fit ``step`` (the same theme when ``step`` is 0)."""
    return theme if not step else replace(theme, sizes=fit_sizes(theme.sizes, step))


class DesignPalette(BaseModel):
    """Colours as ``#RRGGBB``; an empty string leaves the preset's colour in place."""

    background: str = ""
    background_end: str = ""  # deck only: second colour of a gradient background
    text: str = ""
    muted: str = ""
    accent: str = ""
    accent2: str = ""  # deck only: second accent for decorations
    primary: str = ""
    link: str = ""
    surface: str = ""


class DesignSpec(BaseModel):
    """The model's per-request design. Every field is optional; strings are validated later."""

    # Preset to start from (a name from the theme list); empty = chosen from the request form.
    base: str = Field(default="", json_schema_extra={"enum": ["", *THEME_NAMES]})
    mood: str = ""  # two or three words describing the look ("playful kids", "sober legal")
    palette: DesignPalette = Field(default_factory=DesignPalette)
    heading_font: str = Field(default="", json_schema_extra={"enum": ["", *FONT_NAMES]})
    body_font: str = Field(default="", json_schema_extra={"enum": ["", *FONT_NAMES]})
    cover: str = Field(default="", json_schema_extra={"enum": ["", *COVER_STYLES]})
    heading_style: str = Field(default="", json_schema_extra={"enum": ["", *HEADING_STYLES]})
    table_style: str = Field(default="", json_schema_extra={"enum": ["", *TABLE_STYLES]})
    callout_style: str = Field(default="", json_schema_extra={"enum": ["", *CALLOUT_STYLES]})
    density: str = Field(default="", json_schema_extra={"enum": ["", *DENSITIES]})
    body_pt: float = 0  # 0 = preset size; clamped to 9-13
    heading_scale: float = 0  # 0 = preset; clamped to 0.85-1.35
    # Deck fields (see the schema summary at the top of this module). They are typed loosely so
    # a bad value is repaired and reported instead of making the whole design invalid.
    title_scale: Any = None  # number 0.8-1.4; None = 1.0
    corner_radius: Any = None  # number 0-24 pt; None = 12
    shape_style: str = Field(default="", json_schema_extra={"enum": ["", *SHAPE_STYLES]})
    decorations: list[str] = Field(
        default_factory=list,
        json_schema_extra={"items": {"enum": list(DECORATIONS)}},
    )
    slide_number: Any = None  # true | false; None = on
    footer_text: str = ""  # empty = the deck title


DESIGN_PROMPT_RULE = (
    "\nDESIGN: also fill 'design' so this document looks made for THIS request, not like a "
    "default template. Pick 'mood' (2-3 words), a 'palette' of #RRGGBB colours (background, "
    "text, muted, accent, primary, link, surface) that fits the audience and subject, a "
    "'heading_font' and 'body_font' pairing, and a cover, heading_style, table_style, "
    "callout_style and density. Use ONLY these exact values (anything else is replaced by the "
    "nearest one): "
    f"heading_font and body_font: {', '.join(FONT_NAMES)}; "
    f"cover: {', '.join(COVER_STYLES)}; "
    f"heading_style: {', '.join(HEADING_STYLES)}; "
    f"table_style: {', '.join(TABLE_STYLES)}; "
    f"callout_style: {', '.join(CALLOUT_STYLES)}; "
    f"density: {', '.join(DENSITIES)}; "
    f"base (optional preset to start from): {', '.join(THEME_NAMES)}. "
    "Keep text readable: text, muted, accent, primary "
    "and link must each contrast strongly with the background (WCAG AA 4.5:1). A kids' guide "
    "can be bright and rounded; a legal memo sober serif on white; a pitch bold and modern; an "
    "exam cheat sheet dense and high-contrast. If the user stated colours, a brand or a mood, "
    "follow that exactly. Never put design in the content."
)


# --------------------------------------------------------------------------- colour maths
def normalize_hex(value: object) -> str | None:
    """``#RRGGBB`` upper-case from ``#RRGGBB``, ``RRGGBB`` or ``#RGB``; ``None`` if invalid."""
    if not isinstance(value, str):
        return None
    text = value.strip()
    m = _HEX6.match(text)
    if m:
        return "#" + m.group(1).upper()
    m = _HEX3.match(text)
    if m:
        return "#" + "".join(ch * 2 for ch in m.group(1)).upper()
    return None


def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def _hex(rgb: tuple[float, float, float]) -> str:
    r, g, b = (max(0, min(255, round(v))) for v in rgb)
    return f"#{r:02X}{g:02X}{b:02X}"


def luminance(color: str) -> float:
    def chan(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = _rgb(color)
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def contrast_ratio(a: str, b: str) -> float:
    """WCAG 2 contrast ratio between two ``#RRGGBB`` colours (1 to 21)."""
    la, lb = luminance(a), luminance(b)
    hi, lo = max(la, lb), min(la, lb)
    return (hi + 0.05) / (lo + 0.05)


def mix(a: str, b: str, amount: float) -> str:
    """``a`` moved ``amount`` (0-1) of the way toward ``b``."""
    ra, rb = _rgb(a), _rgb(b)
    return _hex(tuple(x + (y - x) * amount for x, y in zip(ra, rb, strict=True)))  # type: ignore[arg-type]


def best_on(background: str) -> str:
    """Black or white, whichever reads better on ``background``."""
    return (
        "#FFFFFF"
        if contrast_ratio("#FFFFFF", background) >= contrast_ratio("#000000", background)
        else "#000000"
    )


def ensure_contrast(color: str, background: str, ratio: float = AA_RATIO) -> str:
    """``color`` itself when it passes; otherwise the same hue moved toward black or white (the
    direction that can reach ``ratio``) in 4 % steps; pure black/white as the last resort."""
    if contrast_ratio(color, background) >= ratio:
        return color
    target = best_on(background)
    for step in range(1, 26):
        candidate = mix(color, target, step * 0.04)
        if contrast_ratio(candidate, background) >= ratio:
            return candidate
    return target


# --------------------------------------------------------------------------- resolution
def _font_stack(name: str, base_stack: str | None = None) -> str:
    generic = FONT_ALLOWLIST[name]
    return f'"{name}", {generic}, {_UNICODE_FALLBACK}'


# Near-miss vocabulary a model reaches for, mapped to the allowed value that keeps its intent.
_SYNONYMS: dict[str, dict[str, str]] = {
    "density": {
        "comfortable": "normal",
        "standard": "normal",
        "default": "normal",
        "medium": "normal",
        "regular": "normal",
        "balanced": "normal",
        "cozy": "normal",
        "tight": "compact",
        "dense": "compact",
        "condensed": "compact",
        "small": "compact",
        "spacious": "airy",
        "relaxed": "airy",
        "loose": "airy",
        "roomy": "airy",
        "open": "airy",
        "wide": "airy",
        "generous": "airy",
    },
    "heading_style": {
        "bold": "bar",
        "strong": "bar",
        "heavy": "bar",
        "accent": "bar",
        "block": "bar",
        "underline": "rule",
        "underlined": "rule",
        "line": "rule",
        "divider": "rule",
        "border": "rule",
        "uppercase": "caps",
        "allcaps": "caps",
        "capitals": "caps",
        "small-caps": "caps",
        "simple": "plain",
        "clean": "plain",
        "minimal": "plain",
        "none": "plain",
        "normal": "plain",
        "default": "plain",
    },
    "table_style": {
        "zebra": "striped",
        "banded": "striped",
        "stripes": "striped",
        "grid": "lined",
        "bordered": "lined",
        "outlined": "lined",
        "borders": "lined",
        "solid": "filled",
        "header": "filled",
        "bold": "filled",
        "plain": "minimal",
        "simple": "minimal",
        "clean": "minimal",
        "none": "minimal",
    },
    "callout_style": {
        "filled": "tinted",
        "colored": "tinted",
        "coloured": "tinted",
        "shaded": "tinted",
        "soft": "tinted",
        "box": "outlined",
        "bordered": "outlined",
        "border": "outlined",
        "line": "bar",
        "side": "bar",
        "left-bar": "bar",
        "accent": "bar",
    },
    "cover": {
        "modern": "band",
        "banner": "band",
        "header": "band",
        "minimal": "plain",
        "simple": "plain",
        "clean": "plain",
        "none": "plain",
        "big": "bold",
        "large": "bold",
        "full": "bold",
        "side": "sidebar",
        "split": "sidebar",
        "left": "sidebar",
    },
}
_FONT_SYNONYMS: dict[str, str] = {
    "helvetica": "Arial",
    "helvetica neue": "Arial",
    "arial nova": "Arial",
    "swiss": "Arial",
    "inter": "Segoe UI",
    "roboto": "Segoe UI",
    "open sans": "Segoe UI",
    "lato": "Segoe UI",
    "source sans pro": "Segoe UI",
    "source sans": "Segoe UI",
    "noto sans": "Segoe UI",
    "sf pro": "Segoe UI",
    "san francisco": "Segoe UI",
    "system-ui": "Segoe UI",
    "sans-serif": "Calibri",
    "sans serif": "Calibri",
    "sans": "Calibri",
    "montserrat": "Century Gothic",
    "poppins": "Century Gothic",
    "futura": "Century Gothic",
    "raleway": "Century Gothic",
    "nunito": "Trebuchet MS",
    "ubuntu": "Trebuchet MS",
    "fira sans": "Calibri",
    "gill sans": "Calibri",
    "lucida sans": "Tahoma",
    "geneva": "Verdana",
    "dejavu sans": "Verdana",
    "times": "Times New Roman",
    "serif": "Georgia",
    "merriweather": "Georgia",
    "playfair display": "Georgia",
    "lora": "Georgia",
    "baskerville": "Palatino Linotype",
    "book antiqua": "Palatino Linotype",
    "palatino": "Palatino Linotype",
    "bookman": "Georgia",
    "caslon": "Garamond",
    "minion": "Garamond",
    "constantia": "Cambria",
    "charter": "Georgia",
    "chalkboard": "Comic Sans MS",
    "marker felt": "Comic Sans MS",
    "poster": "Impact",
    "anton": "Impact",
    "bebas neue": "Impact",
}


def _pick(value: str, allowed: tuple[str, ...], default: str, what: str, notes: list[str]) -> str:
    text = (value or "").strip().lower()
    if not text:
        return default
    if text in allowed:
        return text
    key = text.replace("_", " ").replace("-", " ").strip()
    synonyms = _SYNONYMS.get(what, {})
    near = synonyms.get(text) or synonyms.get(key)
    if near is None:
        close = difflib.get_close_matches(text, allowed, n=1, cutoff=0.6)
        near = close[0] if close else None
    if near is not None and near in allowed:
        notes.append(f"{what} '{value}' is not allowed; used the nearest allowed value '{near}'")
        return near
    notes.append(f"{what} '{value}' is not allowed; used '{default}'")
    return default


def nearest_font(value: str) -> str | None:
    """The allowed family closest to ``value`` (a synonym, a substring or a spelling near-miss);
    ``None`` when nothing is close."""
    text = " ".join((value or "").replace("_", " ").replace("-", " ").lower().split())
    if not text:
        return None
    by_lower = {f.lower(): f for f in FONT_ALLOWLIST}
    if text in by_lower:
        return by_lower[text]
    first = text.split(",")[0].strip().strip(chr(34) + chr(39))
    for candidate in (text, first):
        if candidate in _FONT_SYNONYMS:
            return _FONT_SYNONYMS[candidate]
    for lower, family in by_lower.items():
        if lower in text or (len(text) > 3 and text in lower):
            return family
    close = difflib.get_close_matches(first, list(by_lower), n=1, cutoff=0.7)
    if close:
        return by_lower[close[0]]
    if "serif" in text and "sans" not in text:
        return "Georgia"
    if "sans" in text or "grotesk" in text or "gothic" in text:
        return "Calibri"
    return None


def _font(value: str, default_family: str, what: str, notes: list[str]) -> str:
    text = (value or "").strip()
    if not text:
        return default_family
    for family in FONT_ALLOWLIST:
        if family.lower() == text.lower():
            return family
    near = nearest_font(text)
    if near is not None:
        notes.append(f"{what} '{value}' is not in the safe font list; used the nearest, '{near}'")
        return near
    notes.append(f"{what} '{value}' is not in the safe font list; used '{default_family}'")
    return default_family


def _from_spec(requested: str, chosen: str) -> bool:
    """True when ``chosen`` is what the model asked for (exactly, or its nearest allowed family)."""
    text = (requested or "").strip()
    return bool(text) and (text.lower() == chosen.lower() or nearest_font(text) == chosen)


def nearest_preset(value: str) -> str | None:
    """The preset theme name closest to ``value`` (``None`` when nothing is close)."""
    text = (value or "").strip().lower()
    if text in THEMES:
        return text
    close = difflib.get_close_matches(text, list(THEMES), n=1, cutoff=0.6)
    return close[0] if close else None


def coerce_design(design: DesignSpec | dict[str, Any] | None) -> DesignSpec | None:
    """A :class:`DesignSpec` from a model object or a stored dict; ``None`` when unusable."""
    if design is None or design == {}:
        return None
    if isinstance(design, DesignSpec):
        return design
    try:
        return DesignSpec.model_validate(design)
    except ValueError:
        return None


def resolve_design(
    design: DesignSpec | dict[str, Any] | None,
    base_name: str | None = None,
    *,
    user_accent: str | None = None,
    band_primary: bool = False,
) -> tuple[Theme, list[str]]:
    """The runtime theme for a design, plus a note for every repair that was needed.

    ``band_primary`` (decks) keeps the primary colour as the title-band fill: it is a fill, not
    text, so it is not moved for text contrast. Text that uses it is then checked by the caller.

    ``base_name`` is the preset used when the design names none. ``user_accent`` (a colour the
    user stated) replaces the design's accent, primary and link before contrast repair.
    With no usable design this is just the named preset.
    """
    notes: list[str] = []
    spec = coerce_design(design)
    if spec is None:
        if design:
            notes.append("the design was not valid; the preset theme was used")
        preset_name = base_name if base_name in THEMES else DEFAULT_THEME
        return THEMES[preset_name], notes
    near_base = nearest_preset(spec.base) if spec.base else None
    preset_name = near_base if near_base else (base_name if base_name in THEMES else DEFAULT_THEME)
    if spec.base and spec.base != near_base:
        notes.append(f"base '{spec.base}' is not a preset; used '{preset_name}'")
    preset = THEMES[preset_name]
    pc = preset.colors

    chosen: dict[str, str] = {}
    for key in ("background", "text", "muted", "accent", "primary", "link", "surface"):
        raw = getattr(spec.palette, key)
        if not raw or not raw.strip():
            continue
        fixed = normalize_hex(raw)
        if fixed is None:
            notes.append(f"palette.{key} '{raw}' is not a #RRGGBB colour; the preset's was used")
        else:
            chosen[key] = fixed
    accent_user = normalize_hex(user_accent) if user_accent else None
    if accent_user:
        chosen["accent"] = chosen["primary"] = chosen["link"] = accent_user

    bg = chosen.get("background", pc.page_bg)
    dark = luminance(bg) < 0.25
    text = chosen.get("text", pc.text)
    muted = chosen.get("muted", pc.muted)
    accent = chosen.get("accent", pc.accent)
    primary = chosen.get("primary", pc.primary)
    link = chosen.get("link", pc.link)
    # A library theme (deck_themes.py) brings its own card colour; a custom background replaces it.
    library_card = pc.card_bg if preset_name in _LIBRARY and "background" not in chosen else None
    surface = chosen.get("surface", library_card or mix(bg, text, 0.05))
    # A preset colour taken from a light preset may not suit a dark custom background (and the
    # reverse); the contrast repair below fixes it, so no special case is needed here.

    def fix(name: str, color: str, against: str, ratio: float = AA_RATIO) -> str:
        repaired = ensure_contrast(color, against, ratio)
        if repaired != color:
            notes.append(
                f"{name} {color} had contrast {contrast_ratio(color, against):.1f}:1 on "
                f"{against}; changed to {repaired} (AA needs {ratio}:1)"
            )
        return repaired

    text = fix("text", text, bg)
    muted = fix("muted", muted, bg)
    accent = fix("accent", accent, bg)
    if not band_primary:
        primary = fix("primary", primary, bg)
    link = fix("link", link, bg)
    if contrast_ratio(text, surface) < AA_RATIO or contrast_ratio(muted, surface) < AA_RATIO:
        repaired_surface = mix(bg, text, 0.03)
        if (
            contrast_ratio(text, repaired_surface) >= AA_RATIO
            and contrast_ratio(muted, repaired_surface) >= AA_RATIO
        ):
            notes.append(
                f"surface {surface} was too close to the text colour; used {repaired_surface}"
            )
            surface = repaired_surface
        else:
            surface = bg
            notes.append("surface was too close to the text colour; the background was used")

    heading = primary if contrast_ratio(primary, bg) >= AA_RATIO else text
    border = mix(bg, text, 0.18)
    stripe = mix(bg, text, 0.04)
    code_bg = mix(bg, text, 0.07)
    th_bg = primary
    th_text = best_on(th_bg)

    def tint(hue: str) -> tuple[str, str]:
        tint_bg = mix(bg, hue, 0.14)
        return tint_bg, ensure_contrast(hue, tint_bg)

    info_bg, info_fg = tint(primary)
    warning_bg, warning_fg = tint("#B45309" if not dark else "#FBBF24")
    success_bg, success_fg = tint("#15803D" if not dark else "#4ADE80")
    slide_title_bg = primary
    colors = ThemeColors(
        page_bg=bg,
        text=text,
        muted=muted,
        heading=heading,
        primary=primary,
        accent=accent,
        border=border,
        surface=surface,
        table_header_bg=th_bg,
        table_header_text=th_text,
        table_stripe=stripe,
        code_bg=code_bg,
        code_text=text,
        link=link,
        info_bg=info_bg,
        info_fg=info_fg,
        warning_bg=warning_bg,
        warning_fg=warning_fg,
        success_bg=success_bg,
        success_fg=success_fg,
        slide_bg=bg,
        slide_title_bg=slide_title_bg,
        slide_title_text=best_on(slide_title_bg),
        card_bg=surface,
    )

    heading_font = _font(spec.heading_font, preset.fonts.heading_font, "heading_font", notes)
    body_font = _font(spec.body_font, preset.fonts.body_font, "body_font", notes)
    fonts = replace(
        preset.fonts,
        heading_font=heading_font,
        body_font=body_font,
        heading_stack=(
            _font_stack(heading_font)
            if _from_spec(spec.heading_font, heading_font)
            else preset.fonts.heading_stack
        ),
        body_stack=(
            _font_stack(body_font)
            if _from_spec(spec.body_font, body_font)
            else preset.fonts.body_stack
        ),
    )

    sizes = preset.sizes
    if spec.body_pt:
        body = min(MAX_BODY_PT, max(MIN_BODY_PT, float(spec.body_pt)))
        if body != spec.body_pt:
            notes.append(f"body_pt {spec.body_pt} clamped to {body}")
        sizes = replace(sizes, body_pt=round(body, 2))
    if spec.heading_scale:
        scale = min(MAX_SCALE, max(MIN_SCALE, float(spec.heading_scale)))
        if scale != spec.heading_scale:
            notes.append(f"heading_scale {spec.heading_scale} clamped to {scale}")
        sizes = replace(
            sizes,
            h1_pt=round(sizes.h1_pt * scale, 2),
            h2_pt=round(sizes.h2_pt * scale, 2),
            h3_pt=round(sizes.h3_pt * scale, 2),
            title_pt=round(sizes.title_pt * scale, 2),
        )
    density = _pick(spec.density, DENSITIES, "normal", "density", notes)
    if density == "compact":
        sizes = compact_sizes(sizes)
    elif density == "airy":
        sizes = replace(
            sizes,
            line_height=round(min(1.8, sizes.line_height + 0.12), 2),
            block_gap_pt=round(sizes.block_gap_pt * 1.3, 2),
            page_margin_mm=min(26, sizes.page_margin_mm + 3),
        )

    style = ThemeStyle(
        cover=_pick(spec.cover, COVER_STYLES, "band", "cover", notes),
        heading=_pick(spec.heading_style, HEADING_STYLES, "plain", "heading_style", notes),
        table=_pick(spec.table_style, TABLE_STYLES, "filled", "table_style", notes),
        callout=_pick(spec.callout_style, CALLOUT_STYLES, "tinted", "callout_style", notes),
    )
    mood = re.sub(r"[^\w ,&-]", "", spec.mood)[:40].strip()
    theme = Theme(
        name=f"custom-{preset_name}",
        label=f"Custom ({mood or preset_name})",
        dark=dark,
        colors=colors,
        fonts=fonts,
        sizes=sizes,
        style=style,
    )
    return theme, notes


def design_to_dict(spec: DesignSpec | dict[str, Any] | None) -> dict[str, Any] | None:
    """A plain dict for storage in a document context (JSON-safe), or ``None`` when empty."""
    parsed = coerce_design(spec)
    if parsed is None:
        return None
    return parsed.model_dump()


def contrast_report(theme: Theme) -> dict[str, float]:
    """The AA-relevant ratios of a theme (used by tests and the document proof)."""
    c = theme.colors
    return {
        "text_on_bg": contrast_ratio(c.text, c.page_bg),
        "muted_on_bg": contrast_ratio(c.muted, c.page_bg),
        "accent_on_bg": contrast_ratio(c.accent, c.page_bg),
        "primary_on_bg": contrast_ratio(c.primary, c.page_bg),
        "link_on_bg": contrast_ratio(c.link, c.page_bg),
        "text_on_surface": contrast_ratio(c.text, c.surface),
        "table_header": contrast_ratio(c.table_header_text, c.table_header_bg),
    }


# --------------------------------------------------------------------------- deck style
MIN_TITLE_SCALE, MAX_TITLE_SCALE = 0.8, 1.4
DEFAULT_CORNER_RADIUS = 12.0
MIN_CORNER_RADIUS, MAX_CORNER_RADIUS = 0.0, 24.0
DEFAULT_DECORATIONS: tuple[str, ...] = ("divider_line",)
MAX_FOOTER_CHARS = 60

_SYNONYMS["shape_style"] = {
    "shadow": "soft-shadow",
    "soft": "soft-shadow",
    "elevated": "soft-shadow",
    "card": "soft-shadow",
    "border": "outlined",
    "outline": "outlined",
    "bordered": "outlined",
    "line": "outlined",
    "transparent": "glass",
    "frosted": "glass",
    "translucent": "glass",
    "blur": "glass",
    "solid": "flat",
    "plain": "flat",
    "minimal": "flat",
    "none": "flat",
}
_DECORATION_SYNONYMS: dict[str, str] = {
    "bar": "top_bar",
    "topbar": "top_bar",
    "header bar": "top_bar",
    "sidebar": "side_bar",
    "side": "side_bar",
    "left bar": "side_bar",
    "circle": "corner_circle",
    "corner": "corner_circle",
    "dots": "grid_dots",
    "grid": "grid_dots",
    "dot grid": "grid_dots",
    "divider": "divider_line",
    "rule": "divider_line",
    "line": "divider_line",
    "underline": "divider_line",
    "no": "none",
    "nothing": "none",
    "off": "none",
}


@dataclass(frozen=True)
class DeckStyle:
    """The deck-only look, validated: what the PPTX renderer and the HTML mirror draw."""

    background: str  # solid colour, or the gradient start
    background_end: str | None  # gradient end; None = solid background
    accent2: str
    title_scale: float
    corner_radius: float  # points
    shape_style: str  # one of SHAPE_STYLES
    decorations: tuple[str, ...]  # subset of DECORATIONS (empty = none)
    slide_number: bool
    footer_text: str  # "" means the deck title
    # Slide theme library fields (deck_themes.py). The defaults describe the classic look, so a
    # DeckStyle built without them draws exactly as before.
    family: str = "classic"  # classic | glass | editorial
    theme_key: str = ""  # the library theme this style came from ("" for a classic preset)
    card_style: str = ""  # glass | solid | tinted | outline ("" = derived from shape_style)
    card_alpha: float = 1.0
    shadow: str = ""  # none | hairline | soft | lift | glow ("" = derived from shape_style)
    motif: str = ""  # circles | arcs | lines | dots | blobs | rings ("" = none)
    title_layout: str = "left"  # left | center | split | bottom (library title slides)
    gradient_angle: float = 180.0  # CSS degrees of the page gradient
    transition: str = "none"  # deck-level slide transition (see deck_transitions.py)


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        try:
            return float(value.strip().lower().removesuffix("pt").strip())
        except ValueError:
            return None
    return None


def _clamped(
    value: Any, default: float, low: float, high: float, what: str, notes: list[str]
) -> float:
    if value is None or value == "":
        return default
    number = _number(value)
    if number is None:
        notes.append(f"{what} '{value}' is not a number; used {default:g}")
        return default
    fixed = min(high, max(low, number))
    if fixed != number:
        notes.append(f"{what} {value} is outside {low:g}-{high:g}; used {fixed:g}")
    return round(fixed, 2)


def _flag(value: Any, default: bool, notes: list[str]) -> bool:
    if value is None or value == "":
        return default
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in ("on", "yes", "true", "1", "show", "visible"):
        return True
    if text in ("off", "no", "false", "0", "hide", "hidden", "none"):
        return False
    notes.append(f"slide_number '{value}' is not on or off; used {'on' if default else 'off'}")
    return default


def _decorations(values: list[str], notes: list[str]) -> tuple[str, ...]:
    names = {name.replace("_", " "): name for name in DECORATIONS}
    out: list[str] = []
    for raw in values:
        text = " ".join(str(raw).replace("_", " ").replace("-", " ").lower().split())
        if not text:
            continue
        if text in names:
            name: str | None = names[text]
        elif text in _DECORATION_SYNONYMS:
            name = _DECORATION_SYNONYMS[text]
        else:
            close = difflib.get_close_matches(text, list(names), n=1, cutoff=0.6)
            name = names[close[0]] if close else None
        if name is None:
            notes.append(f"decoration '{raw}' is not allowed; dropped")
            continue
        if name != str(raw).strip():
            notes.append(f"decoration '{raw}' is not allowed; used '{name}'")
        if name not in out:
            out.append(name)
    if "none" in out:
        out = [name for name in out if name != "none"]
        if out:
            notes.append("decoration 'none' was ignored because other decorations were set")
    return tuple(out)


def _footer(value: str, notes: list[str]) -> str:
    text = re.sub(r"[\x00-\x1f]", " ", value or "").strip()
    if len(text) > MAX_FOOTER_CHARS:
        notes.append(f"footer_text was cut to {MAX_FOOTER_CHARS} characters")
        text = text[:MAX_FOOTER_CHARS].rstrip()
    return text


def _gradient_end(end: str, bg: str, text: str, muted: str, notes: list[str]) -> str | None:
    """``end`` itself when text and muted text pass AA on it; otherwise moved toward ``bg`` in
    5 % steps until they pass (``None`` when that is the background, i.e. a solid page)."""
    chosen = bg
    for step in range(21):
        candidate = mix(end, bg, step * 0.05)
        if contrast_ratio(text, candidate) >= AA_RATIO and contrast_ratio(muted, candidate) >= (
            AA_RATIO
        ):
            chosen = candidate
            break
    if chosen != end:
        notes.append(
            f"background_end {end} lowered text contrast; moved to {chosen} (AA needs 4.5:1)"
        )
    return None if chosen == bg else chosen


def _library_shape_style(library: DeckThemeSpec) -> str:
    """The ``shape_style`` a library theme's card style maps to."""
    return {
        "glass": "glass",
        "solid": "soft-shadow",
        "tinted": "soft-shadow",
        "outline": "outlined",
    }[library.card_style]


def _library_fields(library: DeckThemeSpec | None) -> dict[str, Any]:
    """The family fields of :class:`DeckStyle` for a library theme (empty for a classic preset)."""
    if library is None:
        return {}
    return {
        "family": library.family,
        "theme_key": library.name,
        "card_style": library.card_style,
        "card_alpha": library.card_alpha,
        "shadow": library.shadow,
        "motif": library.motif,
        "title_layout": library.title_layout,
        "gradient_angle": library.gradient_angle,
        "transition": default_transition(library),
    }


def _library_style(library: DeckThemeSpec, theme: Theme) -> DeckStyle:
    """The deck style of a library theme used without a design."""
    return DeckStyle(
        background=theme.colors.slide_bg,
        background_end=library.bg_end,
        accent2=library.accent2,
        title_scale=1.0,
        corner_radius=library.corner_radius,
        shape_style=_library_shape_style(library),
        decorations=(),
        slide_number=True,
        footer_text="",
        **_library_fields(library),
    )


def resolve_deck(
    design: DesignSpec | dict[str, Any] | None,
    base_name: str | None = None,
) -> tuple[Theme, DeckStyle, list[str]]:
    """The deck's theme, deck style and every repair note (documents use :func:`resolve_design`).

    With no usable design the named preset is returned with the default deck style.
    """
    theme, notes = resolve_design(design, base_name, band_primary=True)
    spec = coerce_design(design)
    bg = theme.colors.page_bg
    library = spec_for_theme_name(theme.name)
    if spec is None:
        if library is not None:
            return theme, _library_style(library, theme), notes
        return (
            theme,
            DeckStyle(
                background=theme.colors.slide_bg,
                background_end=None,
                accent2=theme.colors.primary,
                title_scale=1.0,
                corner_radius=DEFAULT_CORNER_RADIUS,
                shape_style="flat",
                decorations=DEFAULT_DECORATIONS,
                slide_number=True,
                footer_text="",
            ),
            notes,
        )

    palette = spec.palette
    background_end: str | None = None
    raw_end = palette.background_end.strip()
    if raw_end:
        end = normalize_hex(raw_end)
        if end is None:
            notes.append(f"palette.background_end '{raw_end}' is not a #RRGGBB colour; solid used")
        else:
            background_end = _gradient_end(end, bg, theme.colors.text, theme.colors.muted, notes)

    if library is not None and not raw_end and not palette.background.strip() and library.bg_end:
        background_end = _gradient_end(
            library.bg_end, bg, theme.colors.text, theme.colors.muted, notes
        )

    raw_accent2 = palette.accent2.strip()
    accent2 = normalize_hex(raw_accent2) if raw_accent2 else None
    if raw_accent2 and accent2 is None:
        notes.append(f"palette.accent2 '{raw_accent2}' is not a #RRGGBB colour; primary used")
    accent2 = accent2 or (library.accent2 if library is not None else theme.colors.primary)

    title_scale = _clamped(
        spec.title_scale, 1.0, MIN_TITLE_SCALE, MAX_TITLE_SCALE, "title_scale", notes
    )
    corner = _clamped(
        spec.corner_radius,
        library.corner_radius if library is not None else DEFAULT_CORNER_RADIUS,
        MIN_CORNER_RADIUS,
        MAX_CORNER_RADIUS,
        "corner_radius",
        notes,
    )
    shape_default = _library_shape_style(library) if library is not None else "flat"
    shape = _pick(spec.shape_style, SHAPE_STYLES, shape_default, "shape_style", notes)
    decoration_default = () if library is not None else DEFAULT_DECORATIONS
    decorations = (
        _decorations(list(spec.decorations), notes) if spec.decorations else decoration_default
    )
    return (
        theme,
        DeckStyle(
            background=theme.colors.slide_bg,
            background_end=background_end,
            accent2=accent2,
            title_scale=title_scale,
            corner_radius=corner,
            shape_style=shape,
            decorations=decorations,
            slide_number=_flag(spec.slide_number, True, notes),
            footer_text=_footer(spec.footer_text, notes),
            **_library_fields(library),
        ),
        notes,
    )
