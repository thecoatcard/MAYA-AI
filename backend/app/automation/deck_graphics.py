"""Reusable slide graphics built from :mod:`app.automation.deck_scene` primitives.

Everything here is a pure function of a :class:`Ctx` (theme + deck style) and geometry, so the PPTX
engine and the HTML mirror draw the same thing:

* cards (glass: translucent rounded card with a hairline border and soft shadow; solid; tinted;
  outline), pills, number badges, icon badges (a native icon, or a numbered badge for an unknown
  icon name), progress bars, divider ornaments;
* decoration motifs (circles, arcs, lines, dots, blobs, rings) kept inside the slide, because the
  deck verifier rejects a shape that leaves the canvas;
* the family header (title with an accent pill or a thin rule) and footer metrics;
* procedural background art (a soft gradient mesh with a little grain, drawn with Pillow) for the
  title and section slides of the glass themes, cached per theme and size.
"""

from __future__ import annotations

import io
import math
from dataclasses import dataclass
from functools import lru_cache

from app.automation import deck_icons
from app.automation.deck_scene import (
    Box,
    Gradient,
    IconShape,
    ImageShape,
    LineShape,
    Para,
    Prim,
    SceneFitError,
    TextBox,
)
from app.automation.design import AA_RATIO, DeckStyle, best_on, contrast_ratio, luminance, mix
from app.automation.inline import plain_text
from app.automation.textfit import fit_size
from app.automation.themes import Theme, ThemeColors

SLIDE_W = 13.333
SLIDE_H = 7.5
MARGIN_X = 0.7
CONTENT_W = SLIDE_W - 2 * MARGIN_X
BODY_TOP = 1.7
BODY_BOTTOM = 6.75
BODY_H = BODY_BOTTOM - BODY_TOP
BOX_INSET = 0.12

_FLAT_CARD = {"flat": "flat", "outlined": "outline", "soft-shadow": "solid", "glass": "glass"}


@dataclass(frozen=True)
class Ctx:
    """Theme, deck style and deck title: everything a graphic needs to pick colours and shape."""

    theme: Theme
    style: DeckStyle
    deck_title: str = ""

    @property
    def c(self) -> ThemeColors:
        return self.theme.colors

    @property
    def family(self) -> str:
        return self.style.family

    @property
    def card_style(self) -> str:
        return self.style.card_style or _FLAT_CARD.get(self.style.shape_style, "flat")

    @property
    def shadow(self) -> str:
        if self.style.shadow:
            return self.style.shadow
        return "soft" if self.style.shape_style in ("soft-shadow", "glass") else "none"

    @property
    def dark(self) -> bool:
        return self.theme.dark

    @property
    def accent(self) -> str:
        return self.c.accent

    @property
    def accent2(self) -> str:
        return self.style.accent2

    @property
    def title_color(self) -> str:
        """Title text on the page: plain text colour in a library theme (no title band)."""
        return self.c.text if self.family != "classic" else self.c.heading

    @property
    def title_pt(self) -> float:
        return self.theme.sizes.slide_title_pt * self.style.title_scale

    @property
    def big_pt(self) -> float:
        return self.theme.sizes.slide_big_pt * self.style.title_scale

    def radius(self, w: float, h: float, override_pt: float | None = None) -> float:
        """Corner radius in inches for a ``w`` x ``h`` shape (0 = square corners)."""
        points = self.style.corner_radius if override_pt is None else override_pt
        return max(0.0, min(points / 72.0, min(w, h) / 2))

    def on(self, fill: str) -> str:
        """A text colour that reads on ``fill``: the theme text when it passes AA, else black or
        white."""
        return self.c.text if contrast_ratio(self.c.text, fill) >= AA_RATIO else best_on(fill)

    def accent_text(self, background: str | None = None) -> str:
        """The accent colour when it reads as text on ``background``, else the text colour."""
        bg = background or self.c.page_bg
        return self.c.accent if contrast_ratio(self.c.accent, bg) >= AA_RATIO else self.c.text

    def series_colors(self) -> tuple[str, ...]:
        """Chart series colours: accent, second accent, then the theme's status colours."""
        c = self.c
        return (
            c.accent,
            self.accent2,
            c.success_fg,
            c.warning_fg,
            mix(c.accent, c.text, 0.4),
            mix(self.accent2, c.text, 0.4),
        )


def para(
    ctx: Ctx,
    text: str,
    size: float,
    color: str | None = None,
    *,
    bold: bool = False,
    italic: bool = False,
    font: str = "body",
    align: str = "left",
    space_after: float = 0.0,
    bullet: bool = False,
) -> Para:
    return Para(
        text=text,
        size=size,
        color=color or ctx.c.text,
        bold=bold,
        italic=italic,
        font=font,
        align=align,
        space_after=space_after,
        bullet=bullet,
    )


def fit(
    texts: list[tuple[str, float]] | list[str],
    width: float,
    height: float,
    start: float,
    minimum: float,
    *,
    space_after: float = 0.0,
    what: str = "text",
) -> float:
    """The largest whole-point size at which the texts fit ``width`` x ``height`` (the box's own
    size, insets are taken off here). ``texts`` are plain strings or ``(text, scale)`` pairs where
    scale multiplies the size. Raises :class:`SceneFitError` below ``minimum``."""
    pairs = [(t, 1.0) if isinstance(t, str) else t for t in texts]

    def build(size: float) -> list[tuple[str, float]]:
        return [(plain_text(text), size * scale) for text, scale in pairs]

    size = fit_size(
        build,
        width_in=max(0.1, width - 2 * BOX_INSET),
        height_in=max(0.1, height - 2 * BOX_INSET),
        start_pt=start,
        min_pt=minimum,
        space_after_pt=space_after,
    )
    if size is None:
        raise SceneFitError(f"{what} does not fit even at {minimum:g}pt; shorten it")
    return size


# ------------------------------------------------------------------------------ surfaces
def card(
    ctx: Ctx,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    fill: str | None = None,
    radius_pt: float | None = None,
    name: str = "visual:card",
) -> Box:
    """A card in the deck's card style: glass (translucent fill, hairline border, soft shadow),
    solid, tinted (no border) or outline."""
    c = ctx.c
    rad = ctx.radius(w, h, radius_pt)
    kind = "round" if rad > 0 else "rect"
    style = ctx.card_style
    shadow = ctx.shadow
    lift = shadow if shadow in ("soft", "lift", "glow") else "none"
    if style == "glass":
        return Box(
            x,
            y,
            w,
            h,
            kind=kind,
            fill=fill or c.card_bg,
            alpha=ctx.style.card_alpha if ctx.style.card_alpha < 1.0 else 0.6,
            radius=rad,
            line=mix(c.page_bg, c.text, 0.32),
            line_w=0.75,
            line_alpha=0.6,
            shadow=lift,
            glow=ctx.accent,
            name=name,
        )
    if style == "outline":
        return Box(
            x,
            y,
            w,
            h,
            kind=kind,
            fill=None,
            radius=rad,
            line=mix(c.page_bg, c.text, 0.45),
            line_w=1.25,
            shadow="none",
            name=name,
        )
    border = style == "solid" and lift == "none" and ctx.family != "classic"
    return Box(
        x,
        y,
        w,
        h,
        kind=kind,
        fill=fill or c.card_bg,
        radius=rad,
        line=c.border if border else None,
        line_w=0.75,
        shadow=lift,
        glow=ctx.accent,
        name=name,
    )


def pill(ctx: Ctx, x: float, y: float, w: float, h: float, color: str | None = None) -> Box:
    """A fully rounded bar (an accent marker or a progress track)."""
    return Box(
        x,
        y,
        w,
        h,
        kind="round" if h > 0 else "rect",
        fill=color or ctx.accent,
        radius=h / 2,
        name="visual:pill",
    )


def progress_bar(
    ctx: Ctx, x: float, y: float, w: float, percent: float, h: float = 0.14
) -> list[Prim]:
    """A rounded progress bar: a faint track and an accent fill of ``percent`` (0-100)."""
    share = max(0.0, min(100.0, percent)) / 100.0
    track = Box(
        x,
        y,
        w,
        h,
        kind="round",
        fill=mix(ctx.c.page_bg, ctx.c.text, 0.18),
        radius=h / 2,
        name="visual:progress-track",
    )
    out: list[Prim] = [track]
    if share > 0:
        width = max(h, w * share)
        out.append(
            Box(
                x,
                y,
                width,
                h,
                kind="round",
                gradient=(Gradient(ctx.accent, ctx.accent2, 90) if ctx.family == "glass" else None),
                fill=ctx.accent,
                radius=h / 2,
                name="visual:progress-fill",
            )
        )
    return out


def number_badge(
    ctx: Ctx, x: float, y: float, size: float, label: str, *, fill: str | None = None
) -> list[Prim]:
    """A round badge with a short label (a step number, a section number)."""
    color = fill or ctx.accent
    text_color = best_on(color)
    pt = max(10.0, min(40.0, size * 72 * (0.42 if len(label) <= 2 else 0.3)))
    return [
        Box(
            x,
            y,
            size,
            size,
            kind="oval",
            gradient=Gradient(color, ctx.accent2 if fill is None else color, 135),
            fill=color,
            name="visual:badge",
        ),
        TextBox(
            x,
            y,
            size,
            size,
            (para(ctx, label, pt, text_color, bold=True, font="heading", align="center"),),
            anchor="middle",
            inset=0.02,
            name="badge-text",
        ),
    ]


def icon_badge(ctx: Ctx, icon: str, index: int, x: float, y: float, size: float) -> list[Prim]:
    """A tinted rounded badge holding a native icon; a numbered badge when the icon is unknown."""
    if not deck_icons.has_icon(icon):
        return number_badge(ctx, x, y, size, f"{index:02d}" if index < 100 else str(index))
    tint = mix(ctx.c.page_bg, ctx.accent, 0.22 if ctx.dark else 0.14)
    glyph = ctx.accent if contrast_ratio(ctx.accent, tint) >= 3.0 else ctx.c.text
    pad = size * 0.24
    return [
        Box(
            x,
            y,
            size,
            size,
            kind="round",
            fill=tint,
            radius=size * 0.32,
            name="visual:icon-badge",
        ),
        IconShape(icon, x + pad, y + pad, size - 2 * pad, glyph),
    ]


def ornament(ctx: Ctx, cx: float, y: float, width: float = 1.6) -> list[Prim]:
    """A small divider: a thin rule with a centred accent dot."""
    muted = mix(ctx.c.page_bg, ctx.c.text, 0.3)
    gap = 0.14
    half = (width - gap * 2) / 2
    return [
        LineShape(cx - width / 2, y, cx - width / 2 + half, y, muted, 0.75, name="deco:ornament"),
        Box(cx - 0.05, y - 0.05, 0.1, 0.1, kind="oval", fill=ctx.accent, name="deco:ornament-dot"),
        LineShape(cx + gap, y, cx + gap + half, y, muted, 0.75, name="deco:ornament"),
    ]


# ------------------------------------------------------------------------------- motifs
def motif(
    ctx: Ctx,
    x: float,
    y: float,
    w: float,
    h: float,
    *,
    kind: str | None = None,
    strength: float = 1.0,
) -> list[Prim]:
    """Decoration drawn inside the ``x, y, w, h`` region (never outside it)."""
    shape = kind or ctx.style.motif
    if not shape:
        return []
    a1, a2 = ctx.accent, ctx.accent2
    alpha = max(0.05, min(1.0, strength))
    dark = ctx.dark
    soft = 0.22 if dark else 0.16
    out: list[Prim] = []
    side = min(w, h)
    if shape == "circles":
        big = side * 0.82
        out.append(
            Box(
                x + w - big,
                y + h - big,
                big,
                big,
                kind="oval",
                gradient=Gradient(a1, a2, 135),
                fill=a1,
                alpha=soft * 1.5 * alpha,
                name="deco:circle",
            )
        )
        mid = side * 0.5
        out.append(
            Box(
                x + w * 0.04,
                y + h * 0.1,
                mid,
                mid,
                kind="oval",
                fill=a2,
                alpha=soft * 1.2 * alpha,
                name="deco:circle",
            )
        )
        ring = side * 0.34
        out.append(
            Box(
                x + w * 0.5,
                y + h * 0.04,
                ring,
                ring,
                kind="oval",
                line=a1,
                line_w=1.5,
                line_alpha=0.7 * alpha,
                name="deco:circle",
            )
        )
    elif shape == "arcs":
        for index in range(4):
            size = side * (0.95 - index * 0.2)
            out.append(
                Box(
                    x + w - size,
                    y + (h - size) / 2,
                    size,
                    size,
                    kind="oval",
                    line=a1 if index % 2 == 0 else a2,
                    line_w=1.75 - index * 0.25,
                    line_alpha=(0.75 - index * 0.12) * alpha,
                    name="deco:arc",
                )
            )
    elif shape == "lines":
        count = 7
        for index in range(count):
            offset = y + h * (index + 0.5) / count
            length = w * (0.35 + 0.65 * ((index * 3) % count) / count)
            out.append(
                LineShape(
                    x + w - length,
                    offset,
                    x + w,
                    offset,
                    a1 if index == 2 else mix(ctx.c.page_bg, ctx.c.text, 0.35),
                    1.5 if index == 2 else 0.75,
                    0.9 * alpha,
                    name="deco:line",
                )
            )
    elif shape == "dots":
        cols = max(3, min(9, int(w / 0.5)))
        rows = max(3, min(9, int(h / 0.5)))
        step_x, step_y = w / cols, h / rows
        for row in range(rows):
            for col in range(cols):
                fade = 1.0 - 0.75 * ((col / cols + (1 - row / rows)) / 2)
                dot = 0.1 + 0.05 * fade
                out.append(
                    Box(
                        x + col * step_x + step_x / 2 - dot / 2,
                        y + row * step_y + step_y / 2 - dot / 2,
                        dot,
                        dot,
                        kind="oval",
                        fill=a1 if (row + col) % 5 == 0 else a2,
                        alpha=max(0.12, 0.6 * fade) * alpha,
                        name="deco:dot",
                    )
                )
    elif shape == "blobs":
        specs = (
            (0.05, 0.25, 0.7, 0.62, a1, 0.30),
            (0.35, 0.0, 0.6, 0.55, a2, 0.26),
            (0.3, 0.45, 0.65, 0.55, mix(a1, a2, 0.5), 0.22),
        )
        for bx, by, bw, bh, color, op in specs:
            out.append(
                Box(
                    x + w * bx,
                    y + h * by,
                    w * bw,
                    h * bh,
                    kind="oval",
                    fill=color,
                    alpha=op * alpha * (1.0 if dark else 0.7),
                    name="deco:blob",
                )
            )
    else:  # rings
        for index in range(3):
            size = side * (0.9 - index * 0.28)
            out.append(
                Box(
                    x + (w - size) / 2,
                    y + (h - size) / 2,
                    size,
                    size,
                    kind="oval",
                    line=a1 if index != 1 else a2,
                    line_w=2.0 - index * 0.4,
                    line_alpha=(0.75 - index * 0.15) * alpha,
                    name="deco:ring",
                )
            )
        dot = side * 0.12
        out.append(
            Box(
                x + (w - dot) / 2,
                y + (h - dot) / 2,
                dot,
                dot,
                kind="oval",
                fill=a1,
                alpha=0.85 * alpha,
                name="deco:ring-dot",
            )
        )
    return out


# ------------------------------------------------------------------------------- chrome
def family_header(ctx: Ctx, title: str) -> list[Prim]:
    """The title of a library-theme content slide: no band. Glass puts a small accent pill above
    the title; editorial draws a thin rule under it with a short accent segment."""
    c = ctx.c
    box_h = 0.95
    size = fit(
        [(title, 1.0)],
        CONTENT_W,
        box_h,
        ctx.title_pt,
        20,
        what=f"title '{title[:40]}'",
    )
    out: list[Prim] = []
    if ctx.family == "glass":
        out.append(pill(ctx, MARGIN_X + BOX_INSET, 0.36, 0.8, 0.08))
        top = 0.45
    else:
        top = 0.32
        rule_y = 1.38
        out.append(
            LineShape(
                MARGIN_X, rule_y, SLIDE_W - MARGIN_X, rule_y, mix(c.page_bg, c.text, 0.22), 0.75
            )
        )
        out.append(
            Box(
                MARGIN_X,
                rule_y - 0.02,
                1.2,
                0.04,
                kind="rect",
                fill=ctx.accent,
                name="deco:rule-accent",
            )
        )
    out.append(
        TextBox(
            MARGIN_X,
            top,
            CONTENT_W,
            box_h,
            (para(ctx, title, size, ctx.title_color, bold=True, font="heading"),),
            anchor="middle",
            name="title",
        )
    )
    return out


# ------------------------------------------------------------------- procedural art (Pillow)
ART_SIZE = (1280, 720)
ART_MAX_BYTES = 300_000


def _hex_rgb(color: str) -> tuple[int, int, int]:
    return int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)


@lru_cache(maxsize=64)
def _art_png(
    theme_key: str,
    bg: str,
    bg_end: str,
    accent: str,
    accent2: str,
    angle: float,
    dark: bool,
    layout: str,
    width: int,
    height: int,
) -> bytes:
    """A soft gradient mesh with a touch of grain as PNG bytes (cached per theme and size).

    The same page gradient, then a few heavily blurred accent glows placed away from the title
    text of ``layout``. Palettised (256 colours, dithered) so the file stays small.
    """
    from PIL import Image, ImageChops, ImageDraw, ImageFilter

    del theme_key  # part of the cache key only
    # The gradient is drawn on a small canvas and scaled up: smooth and cheap.
    small_w, small_h = 160, max(1, round(160 * height / width))
    start, end = _hex_rgb(bg), _hex_rgb(bg_end)
    theta = math.radians(angle)
    dx, dy = math.sin(theta), -math.cos(theta)  # CSS angle: 180 deg points down
    corners = [(0, 0), (small_w, 0), (0, small_h), (small_w, small_h)]
    projections = [x * dx + y * dy for x, y in corners]
    low, high = min(projections), max(projections)
    ramp: list[tuple[int, int, int]] = []
    for py in range(small_h):
        for px in range(small_w):
            t = ((px + 0.5) * dx + (py + 0.5) * dy - low) / max(1e-6, high - low)
            t = max(0.0, min(1.0, t))
            r, g, b = (round(a + (z - a) * t) for a, z in zip(start, end, strict=True))
            ramp.append((r, g, b))
    base = Image.new("RGB", (small_w, small_h))
    base.putdata(ramp)
    image = base.resize((width, height), Image.Resampling.BICUBIC)

    glows = {
        "left": (
            (0.82, 0.30, 0.36, accent),
            (0.95, 0.85, 0.30, accent2),
            (0.60, 0.05, 0.22, accent2),
        ),
        "center": (
            (0.10, 0.12, 0.30, accent),
            (0.92, 0.88, 0.34, accent2),
            (0.90, 0.10, 0.20, accent),
        ),
        "split": (
            (0.30, 0.95, 0.34, accent2),
            (0.05, 0.05, 0.26, accent),
            (0.55, 0.50, 0.18, accent2),
        ),
        "bottom": (
            (0.85, 0.10, 0.40, accent),
            (0.15, 0.20, 0.30, accent2),
            (0.60, 0.30, 0.26, accent),
        ),
    }.get(layout, ())
    strength = 0.34 if dark else 0.2
    for cx, cy, radius, color in glows:
        layer = Image.new("L", (width, height), 0)
        draw = ImageDraw.Draw(layer)
        reach = radius * width
        draw.ellipse(
            (cx * width - reach, cy * height - reach, cx * width + reach, cy * height + reach),
            fill=255,
        )
        layer = layer.filter(ImageFilter.GaussianBlur(reach * 0.55))
        layer = layer.point([int(v * strength) for v in range(256)])
        image = Image.composite(Image.new("RGB", (width, height), _hex_rgb(color)), image, layer)

    noise = Image.effect_noise((width, height), 14).convert("RGB")
    grain = ImageChops.blend(image, noise, 0.035 if dark else 0.02)
    image = Image.blend(image, grain, 1.0)
    paletted = image.quantize(
        colors=128, method=Image.Quantize.MEDIANCUT, dither=Image.Dither.FLOYDSTEINBERG
    )
    buffer = io.BytesIO()
    paletted.save(buffer, format="PNG", optimize=True)
    data = buffer.getvalue()
    if len(data) > ART_MAX_BYTES:  # fall back to a smaller, grain-free image
        image = base.resize((width // 2, height // 2), Image.Resampling.BICUBIC)
        buffer = io.BytesIO()
        image.quantize(colors=96, dither=Image.Dither.NONE).save(
            buffer, format="PNG", optimize=True
        )
        data = buffer.getvalue()
    return data


def background_art(ctx: Ctx, layout: str | None = None) -> ImageShape | None:
    """Full-slide procedural art for a glass title or section slide (``None`` for other
    families). The PNG is cached per theme, colours, layout and size."""
    style = ctx.style
    if ctx.family != "glass" or not style.background_end:
        return None
    width, height = ART_SIZE
    data = _art_png(
        style.theme_key or ctx.theme.name,
        ctx.c.page_bg,
        style.background_end,
        ctx.accent,
        ctx.accent2,
        style.gradient_angle,
        luminance(ctx.c.page_bg) < 0.25,
        layout or style.title_layout,
        width,
        height,
    )
    return ImageShape(0, 0, SLIDE_W, SLIDE_H, data=data, alt="", fit=False, name="deco:art")


__all__ = [
    "ART_MAX_BYTES",
    "ART_SIZE",
    "BODY_BOTTOM",
    "BODY_H",
    "BODY_TOP",
    "BOX_INSET",
    "CONTENT_W",
    "MARGIN_X",
    "SLIDE_H",
    "SLIDE_W",
    "Ctx",
    "background_art",
    "card",
    "family_header",
    "fit",
    "icon_badge",
    "motif",
    "number_badge",
    "ornament",
    "para",
    "pill",
    "progress_bar",
]
