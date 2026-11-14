"""The slide theme library: 25 data-driven themes in two families.

* **glass** (12): dark-first. Deep two-stop gradients, translucent rounded cards with an alpha
  fill and a hairline border, soft shadows and accent glows. One light variant (``glass-frost``).
* **editorial** (13): light-first. Generous white space, refined serif or sans type, thin rules,
  a subtle accent and quiet motifs.

A theme is plain data (:class:`DeckThemeSpec`): palette, gradient stops, a font pair from families
that ship with a default Windows or Mac Office install (a PPTX cannot embed fonts), a corner
radius scale, a shadow style, a decoration motif, a card style and a title-slide composition.
:func:`build_deck_themes` turns the specs into ordinary :class:`~app.automation.themes.Theme`
objects, which ``themes.py`` registers next to the four document presets, so every place that
lists theme names (the model schema, ``design.base``, ``auto_design``) sees them without change.

This module imports nothing from ``themes.py`` at import time (``themes.py`` imports it), so the
builder imports lazily.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

if TYPE_CHECKING:  # pragma: no cover - typing only
    from app.automation.themes import Theme

FAMILIES: tuple[str, ...] = ("glass", "editorial")
MOTIFS: tuple[str, ...] = ("circles", "arcs", "lines", "dots", "blobs", "rings")
SHADOWS: tuple[str, ...] = ("none", "hairline", "soft", "lift", "glow")
CARD_STYLES: tuple[str, ...] = ("glass", "solid", "tinted", "outline")
TITLE_LAYOUTS: tuple[str, ...] = ("left", "center", "split", "bottom")
FAMILY_DEFAULT_TRANSITION: dict[str, str] = {"glass": "fade", "editorial": "none"}


@dataclass(frozen=True)
class DeckThemeSpec:
    name: str
    label: str
    family: str  # glass | editorial
    dark: bool
    bg: str  # gradient start (or the solid page colour)
    bg_end: str | None  # gradient end; None = solid
    gradient_angle: float  # CSS degrees: 180 = top to bottom, 135 = top-left to bottom-right
    card: str  # card fill (opaque equivalent; drawn with ``card_alpha``)
    card_alpha: float
    text: str
    muted: str
    accent: str
    accent2: str
    success: str
    warn: str
    heading_font: str
    body_font: str
    radius: tuple[float, float, float]  # corner radius scale in points: small, medium, large
    shadow: str
    motif: str
    card_style: str
    title_layout: str
    tones: tuple[str, ...] = ()  # kinds of deck this theme suits (see auto_design.pick_tone)

    @property
    def corner_radius(self) -> float:
        return self.radius[1]


def _glass(
    name: str,
    label: str,
    bg: str,
    end: str,
    card: str,
    text: str,
    muted: str,
    accent: str,
    accent2: str,
    *,
    heading_font: str = "Segoe UI",
    body_font: str = "Segoe UI",
    shadow: str = "soft",
    motif: str = "circles",
    layout: str = "left",
    angle: float = 160,
    alpha: float = 0.72,
    dark: bool = True,
    radius: tuple[float, float, float] = (10, 18, 28),
    tones: tuple[str, ...] = (),
) -> DeckThemeSpec:
    return DeckThemeSpec(
        name=name,
        label=label,
        family="glass",
        dark=dark,
        bg=bg,
        bg_end=end,
        gradient_angle=angle,
        card=card,
        card_alpha=alpha,
        text=text,
        muted=muted,
        accent=accent,
        accent2=accent2,
        success="#4ADE80" if dark else "#15803D",
        warn="#FBBF24" if dark else "#B45309",
        heading_font=heading_font,
        body_font=body_font,
        radius=radius,
        shadow=shadow,
        motif=motif,
        card_style="glass",
        title_layout=layout,
        tones=tones,
    )


def _editorial(
    name: str,
    label: str,
    bg: str,
    card: str,
    text: str,
    muted: str,
    accent: str,
    accent2: str,
    *,
    heading_font: str = "Georgia",
    body_font: str = "Calibri",
    shadow: str = "hairline",
    motif: str = "lines",
    layout: str = "left",
    style: str = "solid",
    radius: tuple[float, float, float] = (4, 10, 16),
    end: str | None = None,
    tones: tuple[str, ...] = (),
) -> DeckThemeSpec:
    return DeckThemeSpec(
        name=name,
        label=label,
        family="editorial",
        dark=False,
        bg=bg,
        bg_end=end,
        gradient_angle=180,
        card=card,
        card_alpha=1.0,
        text=text,
        muted=muted,
        accent=accent,
        accent2=accent2,
        success="#15803D",
        warn="#B45309",
        heading_font=heading_font,
        body_font=body_font,
        radius=radius,
        shadow=shadow,
        motif=motif,
        card_style=style,
        title_layout=layout,
        tones=tones,
    )


_SPECS: tuple[DeckThemeSpec, ...] = (
    # ------------------------------------------------------------------ glass (dark-first)
    _glass(
        "glass-aurora",
        "Glass Aurora (deep indigo, teal glow)",
        "#0B1026",
        "#1B1147",
        "#2A2F5C",
        "#F4F6FF",
        "#B4BEDD",
        "#5EEAD4",
        "#A78BFA",
        shadow="glow",
        motif="blobs",
        layout="left",
        tones=("pitch", "tech"),
    ),
    _glass(
        "glass-sapphire",
        "Glass Sapphire (royal blue)",
        "#07142E",
        "#0E2A5C",
        "#22406F",
        "#F1F6FF",
        "#B0C3E3",
        "#60A5FA",
        "#22D3EE",
        shadow="soft",
        motif="circles",
        layout="center",
        tones=("tech",),
    ),
    _glass(
        "glass-sunset",
        "Glass Sunset (plum to ember)",
        "#1F1030",
        "#4A1942",
        "#6B2D5E",
        "#FFF5F0",
        "#EBC6D4",
        "#FDBA74",
        "#F472B6",
        shadow="glow",
        motif="arcs",
        layout="bottom",
        angle=145,
        tones=("pitch",),
    ),
    _glass(
        "glass-emerald",
        "Glass Emerald (forest teal)",
        "#04201C",
        "#0B3B33",
        "#1C5A4D",
        "#ECFDF5",
        "#B3DDCD",
        "#34D399",
        "#FDE68A",
        heading_font="Calibri",
        body_font="Calibri",
        shadow="soft",
        motif="rings",
        layout="split",
        tones=("health",),
    ),
    _glass(
        "glass-graphite",
        "Glass Graphite (neutral dark, amber)",
        "#121316",
        "#26282E",
        "#3A3D46",
        "#F5F5F7",
        "#B8BBC4",
        "#FBBF24",
        "#FB7185",
        heading_font="Arial",
        body_font="Arial",
        shadow="lift",
        motif="lines",
        layout="left",
        angle=180,
        tones=("finance",),
    ),
    _glass(
        "glass-ocean",
        "Glass Ocean (deep teal, aqua)",
        "#04202B",
        "#0B4157",
        "#1D5A74",
        "#ECFAFF",
        "#B1D6E4",
        "#2DD4BF",
        "#38BDF8",
        heading_font="Trebuchet MS",
        body_font="Calibri",
        shadow="soft",
        motif="arcs",
        layout="center",
        tones=("health", "tech"),
    ),
    _glass(
        "glass-violet",
        "Glass Violet (indigo to purple)",
        "#170B33",
        "#35157A",
        "#503092",
        "#F6F2FF",
        "#C9BEEB",
        "#C4B5FD",
        "#F0ABFC",
        shadow="glow",
        motif="circles",
        layout="split",
        angle=150,
        tones=("pitch",),
    ),
    _glass(
        "glass-rose",
        "Glass Rose (wine and pink)",
        "#2A0A1B",
        "#5B1235",
        "#6A2244",
        "#FFF1F5",
        "#EEC3D3",
        "#FB7185",
        "#FDBA74",
        heading_font="Trebuchet MS",
        body_font="Calibri",
        shadow="soft",
        motif="blobs",
        layout="bottom",
        tones=("pitch",),
    ),
    _glass(
        "glass-neon",
        "Glass Neon (near black, lime)",
        "#07070C",
        "#14101F",
        "#2D2A44",
        "#F7F7FB",
        "#BDBDD2",
        "#A3E635",
        "#E879F9",
        heading_font="Arial",
        body_font="Arial",
        shadow="glow",
        motif="dots",
        layout="left",
        angle=170,
        tones=("tech",),
    ),
    _glass(
        "glass-slate",
        "Glass Slate (blue gray, ice)",
        "#0F172A",
        "#1E293B",
        "#364458",
        "#F1F5F9",
        "#B2BFD0",
        "#93C5FD",
        "#FCA5A5",
        heading_font="Calibri",
        body_font="Calibri",
        shadow="lift",
        motif="lines",
        layout="center",
        angle=180,
        tones=("tech", "report"),
    ),
    _glass(
        "glass-copper",
        "Glass Copper (espresso and copper)",
        "#1A110C",
        "#3A2416",
        "#58402D",
        "#FBF3EA",
        "#D9C4AE",
        "#E8A267",
        "#7DD3C0",
        heading_font="Georgia",
        body_font="Calibri",
        shadow="soft",
        motif="rings",
        layout="split",
        tones=("finance",),
    ),
    _glass(
        "glass-frost",
        "Glass Frost (light, icy blue)",
        "#EEF4FF",
        "#D8E4FA",
        "#FFFFFF",
        "#0F172A",
        "#475569",
        "#1E40AF",
        "#7C3AED",
        shadow="soft",
        motif="blobs",
        layout="left",
        angle=160,
        alpha=0.62,
        dark=False,
        tones=("report",),
    ),
    # ------------------------------------------------------------------ editorial (light)
    _editorial(
        "editorial-paper",
        "Editorial Paper (warm white, terracotta)",
        "#FBF8F3",
        "#F2EDE4",
        "#1F1B16",
        "#6B6358",
        "#B5452C",
        "#2F6F6B",
        heading_font="Georgia",
        motif="lines",
        layout="left",
        tones=("report",),
    ),
    _editorial(
        "editorial-ink",
        "Editorial Ink (white, ink blue)",
        "#FFFFFF",
        "#F3F5F9",
        "#0B0B0F",
        "#5B5F6A",
        "#1D4ED8",
        "#0E7490",
        heading_font="Cambria",
        motif="dots",
        layout="left",
        tones=("report", "tech"),
    ),
    _editorial(
        "editorial-sage",
        "Editorial Sage (soft green, ochre)",
        "#F6F8F3",
        "#E8EEE4",
        "#1E2A22",
        "#5E6B61",
        "#2F6B4F",
        "#9A6A12",
        heading_font="Georgia",
        motif="arcs",
        layout="split",
        tones=("health", "notes"),
    ),
    _editorial(
        "editorial-navy",
        "Editorial Navy (white, navy and gold)",
        "#F7F9FC",
        "#E8EDF5",
        "#0F2340",
        "#55627A",
        "#1D3A6B",
        "#8A5A08",
        heading_font="Cambria",
        motif="lines",
        layout="left",
        shadow="soft",
        tones=("finance",),
    ),
    _editorial(
        "editorial-blush",
        "Editorial Blush (cream pink, plum)",
        "#FFF7F5",
        "#FCE9E6",
        "#3A1F2B",
        "#7A5A66",
        "#BE3A5B",
        "#7C3AED",
        heading_font="Georgia",
        motif="circles",
        layout="center",
        tones=("pitch",),
    ),
    _editorial(
        "editorial-sky",
        "Editorial Sky (white, clear blue)",
        "#F5FAFF",
        "#E6F1FC",
        "#10243E",
        "#50657F",
        "#0B6BCB",
        "#0E7490",
        heading_font="Calibri",
        body_font="Calibri",
        motif="rings",
        layout="left",
        shadow="soft",
        tones=("notes", "tech"),
    ),
    _editorial(
        "editorial-mono",
        "Editorial Mono (black and white, red)",
        "#FFFFFF",
        "#F4F4F4",
        "#111111",
        "#666666",
        "#C42B1C",
        "#333333",
        heading_font="Arial",
        body_font="Arial",
        motif="lines",
        layout="bottom",
        radius=(0, 2, 4),
        shadow="none",
        style="outline",
        tones=("report",),
    ),
    _editorial(
        "editorial-sand",
        "Editorial Sand (beige, deep teal)",
        "#F7F1E8",
        "#EDE3D3",
        "#2B2118",
        "#6F6254",
        "#0B6B63",
        "#B45309",
        heading_font="Garamond",
        motif="arcs",
        layout="center",
        tones=("report", "notes"),
    ),
    _editorial(
        "editorial-mint",
        "Editorial Mint (fresh green)",
        "#F2FBF7",
        "#E0F4EB",
        "#12302A",
        "#4F6F66",
        "#047857",
        "#7C3AED",
        heading_font="Georgia",
        motif="dots",
        layout="split",
        tones=("health",),
    ),
    _editorial(
        "editorial-lavender",
        "Editorial Lavender (soft violet)",
        "#F8F6FF",
        "#ECE8FB",
        "#241B3D",
        "#645A82",
        "#6D28D9",
        "#BE185D",
        heading_font="Cambria",
        motif="blobs",
        layout="left",
        tones=("pitch",),
    ),
    _editorial(
        "editorial-citrus",
        "Editorial Citrus (playful, rounded)",
        "#FFF9E6",
        "#FFEFC2",
        "#3B2160",
        "#6B5188",
        "#C2410C",
        "#7E22CE",
        heading_font="Trebuchet MS",
        body_font="Verdana",
        motif="circles",
        layout="center",
        radius=(14, 22, 32),
        shadow="soft",
        style="tinted",
        tones=("kids",),
    ),
    _editorial(
        "editorial-amber",
        "Editorial Amber (warm cream)",
        "#FFFBEB",
        "#FDF1CC",
        "#2E2410",
        "#6E5D37",
        "#A64B08",
        "#0E7490",
        heading_font="Georgia",
        motif="rings",
        layout="bottom",
        tones=("kids", "report"),
    ),
    _editorial(
        "editorial-slate",
        "Editorial Slate (cool gray, indigo)",
        "#F8FAFC",
        "#E9EEF5",
        "#0F172A",
        "#475569",
        "#4F46E5",
        "#0D9488",
        heading_font="Calibri",
        body_font="Calibri",
        motif="dots",
        layout="left",
        tones=("notes", "tech"),
    ),
)

DECK_THEME_SPECS: dict[str, DeckThemeSpec] = {spec.name: spec for spec in _SPECS}
DECK_THEME_NAMES: tuple[str, ...] = tuple(DECK_THEME_SPECS)
GLASS_THEME_NAMES: tuple[str, ...] = tuple(s.name for s in _SPECS if s.family == "glass")
EDITORIAL_THEME_NAMES: tuple[str, ...] = tuple(s.name for s in _SPECS if s.family == "editorial")


def family_spec(name: str | None) -> DeckThemeSpec | None:
    """The spec of a library theme, or ``None`` for any other name (including ``custom-<name>``
    is NOT folded here: callers strip that prefix themselves)."""
    return DECK_THEME_SPECS.get(name or "")


def spec_for_theme_name(name: str | None) -> DeckThemeSpec | None:
    """The spec for ``name`` or for ``custom-<name>`` (the runtime theme of a design on a base)."""
    if not name:
        return None
    return DECK_THEME_SPECS.get(name.removeprefix("custom-"))


def default_transition(spec: DeckThemeSpec | None) -> str:
    """The deck-level transition a theme asks for when the user states none."""
    return FAMILY_DEFAULT_TRANSITION.get(spec.family, "none") if spec else "none"


def tone_theme(tone: str) -> str | None:
    """The library theme auto-design picks for a tone (first theme that lists the tone, in the
    order of the preferred-names table below), or ``None`` when the tone has no pick."""
    return _TONE_PICKS.get(tone)


# tone -> theme (pitch and tech decks look best in glass; reports and notes in editorial).
_TONE_PICKS: dict[str, str] = {
    "pitch": "glass-aurora",
    "tech": "glass-sapphire",
    "report": "editorial-paper",
    "finance": "editorial-navy",
    "notes": "editorial-sky",
    "kids": "editorial-citrus",
    "health": "editorial-mint",
}


# Colour maths (the same formulas as design.py, which cannot be imported while themes.py loads).
def _rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


def mix(a: str, b: str, amount: float) -> str:
    """``a`` moved ``amount`` (0-1) of the way toward ``b``."""
    ra, rb = _rgb(a), _rgb(b)
    r, g, bl = (round(x + (y - x) * amount) for x, y in zip(ra, rb, strict=True))
    return f"#{r:02X}{g:02X}{bl:02X}"


def _luminance(color: str) -> float:
    def chan(v: int) -> float:
        s = v / 255
        return s / 12.92 if s <= 0.03928 else ((s + 0.055) / 1.055) ** 2.4

    r, g, b = _rgb(color)
    return 0.2126 * chan(r) + 0.7152 * chan(g) + 0.0722 * chan(b)


def contrast(a: str, b: str) -> float:
    la, lb = _luminance(a), _luminance(b)
    return (max(la, lb) + 0.05) / (min(la, lb) + 0.05)


def best_on(background: str) -> str:
    return (
        "#FFFFFF"
        if contrast("#FFFFFF", background) >= contrast("#000000", background)
        else "#000000"
    )


def ensure_contrast(color: str, background: str, ratio: float = 4.5) -> str:
    if contrast(color, background) >= ratio:
        return color
    target = best_on(background)
    for step in range(1, 26):
        candidate = mix(color, target, step * 0.04)
        if contrast(candidate, background) >= ratio:
            return candidate
    return target


def build_deck_themes() -> dict[str, Theme]:
    """Every library theme as a :class:`~app.automation.themes.Theme` (name -> theme)."""
    from app.automation.themes import (
        _UNICODE_FALLBACK,
        Theme,
        ThemeColors,
        ThemeFonts,
        ThemeSizes,
    )

    mono = f'Consolas, "Courier New", monospace, {_UNICODE_FALLBACK}'
    themes: dict[str, Theme] = {}
    for spec in _SPECS:
        bg, text = spec.bg, spec.text
        card = mix(bg, spec.card, spec.card_alpha) if spec.family == "glass" else spec.card
        info_bg = mix(bg, spec.accent, 0.16)
        warn_bg = mix(bg, spec.warn, 0.16)
        ok_bg = mix(bg, spec.success, 0.16)
        colors = ThemeColors(
            page_bg=bg,
            text=text,
            muted=spec.muted,
            heading=text,
            primary=spec.accent,
            accent=spec.accent,
            border=mix(bg, text, 0.2),
            surface=card,
            table_header_bg=spec.accent,
            table_header_text=best_on(spec.accent),
            table_stripe=mix(bg, text, 0.05),
            code_bg=mix(bg, "#000000", 0.3) if spec.dark else mix(bg, text, 0.06),
            code_text=text,
            link=spec.accent,
            info_bg=info_bg,
            info_fg=ensure_contrast(spec.accent, info_bg),
            warning_bg=warn_bg,
            warning_fg=ensure_contrast(spec.warn, warn_bg),
            success_bg=ok_bg,
            success_fg=ensure_contrast(spec.success, ok_bg),
            slide_bg=bg,
            slide_title_bg=bg,
            slide_title_text=text,
            card_bg=card,
        )
        heading = spec.heading_font
        body = spec.body_font
        serif = heading in ("Georgia", "Cambria", "Garamond")
        generic = '"Times New Roman", serif' if serif else '"Helvetica Neue", Arial, sans-serif'
        fonts = ThemeFonts(
            heading_stack=f'"{heading}", {generic}, {_UNICODE_FALLBACK}',
            body_stack=f'"{body}", "Helvetica Neue", Calibri, Arial, {_UNICODE_FALLBACK}',
            mono_stack=mono,
            heading_font=heading,
            body_font=body,
            mono_font="Consolas",
            complex_script_font="Nirmala UI",
        )
        title_pt = 32.0 if spec.family == "glass" else 34.0
        sizes = ThemeSizes(
            body_pt=10.5,
            small_pt=8.5,
            h1_pt=19,
            h2_pt=14.5,
            h3_pt=12,
            title_pt=28,
            subtitle_pt=13,
            code_pt=9,
            line_height=1.5,
            block_gap_pt=9,
            page_margin_mm=20,
            slide_title_pt=title_pt,
            slide_body_pt=20,
            slide_min_body_pt=14,
            slide_big_pt=46,
        )
        themes[spec.name] = Theme(
            name=spec.name,
            label=spec.label,
            dark=spec.dark,
            colors=colors,
            fonts=fonts,
            sizes=sizes,
        )
    return themes


__all__ = [
    "CARD_STYLES",
    "DECK_THEME_NAMES",
    "DECK_THEME_SPECS",
    "EDITORIAL_THEME_NAMES",
    "FAMILIES",
    "FAMILY_DEFAULT_TRANSITION",
    "GLASS_THEME_NAMES",
    "MOTIFS",
    "SHADOWS",
    "TITLE_LAYOUTS",
    "DeckThemeSpec",
    "build_deck_themes",
    "default_transition",
    "family_spec",
    "spec_for_theme_name",
    "tone_theme",
]
