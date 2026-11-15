"""A deterministic HTML mirror of a deck, for the Edge screenshot path (no LibreOffice needed).

The mirror is drawn from the same validated deck, the same :func:`resolve_deck` result and the
same geometry constants as the PPTX renderer, so a slide that looks right here has the same
palette, fonts, layout boxes and decorations there. It is a review aid, not a second renderer:
the text is not measured, and pictures are used only when they are ``data:`` URIs (a file path
that the PPTX renderer resolves is shown as a placeholder here).

Output is byte-stable for the same input: no timestamps, no random ids, no scripts, no external
resources.
"""

from __future__ import annotations

import base64
import math
from pathlib import Path

from app.automation import deck_icons, deck_layouts
from app.automation import pptx_engine as geo
from app.automation.deck_checks import check_deck, halve
from app.automation.deck_graphics import Ctx, family_header
from app.automation.deck_graphics import card as scene_card
from app.automation.deck_graphics import para as deck_para
from app.automation.deck_scene import (
    Box,
    ChartShape,
    IconShape,
    ImageShape,
    LineShape,
    Prim,
    SceneFitError,
    TextBox,
)
from app.automation.design import AA_RATIO, DeckStyle, best_on, contrast_ratio, mix, resolve_deck
from app.automation.html_render import esc, inline_html
from app.automation.model import Deck, Slide
from app.automation.themes import Theme

# Each slide is a 16:9 frame at the top of an A4-sized page (794 x 1123 px), because the browser
# screenshot path slices pages at that size. Inside the frame, positions are percentages of the
# slide and text sizes are container units (cqw), so the same mirror scales to any width.
PAGE_W_PX, PAGE_H_PX = 794, 1123
SLIDE_H_PX = PAGE_W_PX * geo.SLIDE_H / geo.SLIDE_W


def _x(inches: float) -> str:
    return f"{inches / geo.SLIDE_W * 100:.4f}%"


def _y(inches: float) -> str:
    return f"{inches / geo.SLIDE_H * 100:.4f}%"


def _pad(inches: float) -> str:
    return f"{inches / geo.SLIDE_W * 100:.4f}cqw"


def _pt(points: float) -> str:
    """A font size or radius in points, as container units (1 slide width = 13.333 in)."""
    return f"{points / 72 / geo.SLIDE_W * 100:.4f}cqw"


class _Page:
    def __init__(self, theme: Theme, style: DeckStyle, deck: Deck) -> None:
        self.theme = theme
        self.style = style
        self.deck = deck
        self.ctx = Ctx(theme, style, deck.title)
        self.parts: list[str] = []

    def box(self, x: float, y: float, w: float, h: float, css: str = "", inner: str = "") -> None:
        self.parts.append(
            f'<div style="position:absolute;left:{_x(x)};top:{_y(y)};'
            f'width:{_x(w)};height:{_y(h)};{css}">{inner}</div>'
        )

    def para(
        self,
        text: str,
        pt: float,
        color: str,
        *,
        bold: bool = False,
        italic: bool = False,
        font: str | None = None,
        align: str = "left",
        bullet: bool = False,
        space_after: float = 0.0,
        mono: bool = False,
    ) -> str:
        family = font or (self.theme.fonts.mono_font if mono else self.theme.fonts.body_font)
        marker = ""
        if bullet:
            marker = f'<span style="color:{self.theme.colors.accent}">&bull;&nbsp;</span>'
        return (
            f'<p style="margin:0 0 {_pt(space_after)};font-size:{_pt(pt)};color:{color};'
            f"font-family:'{esc(family)}',sans-serif;font-weight:{'700' if bold else '400'};"
            f"font-style:{'italic' if italic else 'normal'};text-align:{align};"
            f'line-height:1.2;white-space:pre-wrap">{marker}{inline_html(text)}</p>'
        )

    def text(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        paras: list[str],
        *,
        anchor: str = "top",
        inset: float = geo.BOX_INSET,
    ) -> None:
        justify = {"top": "flex-start", "middle": "center", "bottom": "flex-end"}[anchor]
        css = (
            f"display:flex;flex-direction:column;justify-content:{justify};"
            f"padding:{_pad(inset)};box-sizing:border-box;overflow:hidden"
        )
        self.box(x, y, w, h, css, "".join(paras))

    def surface(
        self,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: str,
        *,
        rounded: bool = True,
    ) -> None:
        if self.style.family != "classic":
            self.scene([scene_card(self.ctx, x, y, w, h, fill=fill)])
            return
        css = f"background:{fill};"
        if rounded and self.style.corner_radius > 0:
            css += f"border-radius:{_pt(self.style.corner_radius)};"
        if self.style.shape_style == "outlined":
            css += f"background:transparent;border:1.5px solid {self.theme.colors.accent};"
        elif self.style.shape_style == "glass":
            css += "opacity:0.6;"
        elif self.style.shape_style == "soft-shadow":
            css += "box-shadow:0 6px 18px rgba(0,0,0,0.22);"
        self.box(x, y, w, h, css)

    def background(self, start: str, end: str | None) -> str:
        if end:
            angle = self.style.gradient_angle if self.style.family != "classic" else 180
            return f"background:linear-gradient({angle:g}deg,{start},{end})"
        return f"background:{start}"

    def decorate(self, *, band: bool) -> None:
        c = self.theme.colors
        for name in self.style.decorations:
            if name == "top_bar":
                self.box(0, 0, geo.SLIDE_W, 0.12, f"background:{c.accent}")
            elif name == "side_bar":
                self.box(0, 0, 0.16, geo.SLIDE_H, f"background:{self.style.accent2}")
            elif name == "corner_circle":
                self.box(
                    geo.SLIDE_W - 1.35,
                    0.1,
                    1.0,
                    1.0,
                    f"background:{self.style.accent2};opacity:0.35;border-radius:50%",
                )
            elif name == "grid_dots":
                for col in range(4):
                    for row in range(3):
                        self.box(
                            geo.SLIDE_W - 1.45 + col * 0.3,
                            0.25 + row * 0.28,
                            0.09,
                            0.09,
                            f"background:{self.style.accent2};border-radius:50%",
                        )
            elif name == "divider_line" and band:
                self.box(0, geo.BAND_H, geo.SLIDE_W, 0.06, f"background:{c.accent}")

    def footer(self, number: int) -> None:
        c = self.theme.colors
        self.text(
            geo.MARGIN_X,
            geo.FOOTER_Y,
            8.0,
            0.35,
            [self.para(self.style.footer_text or self.deck.title, 11, c.muted)],
            anchor="middle",
            inset=0.02,
        )
        if self.style.slide_number:
            self.text(
                geo.SLIDE_W - geo.MARGIN_X - 1.0,
                geo.FOOTER_Y,
                1.0,
                0.35,
                [self.para(str(number), 11, c.muted, align="right")],
                anchor="middle",
                inset=0.02,
            )

    def header(self, title: str, number: int) -> None:
        if self.style.family != "classic":
            self.decorate(band=False)
            self.scene(family_header(self.ctx, title))
            self.footer(number)
            return
        c = self.theme.colors
        self.box(0, 0, geo.SLIDE_W, geo.BAND_H, f"background:{c.slide_title_bg}")
        self.decorate(band=True)
        self.text(
            geo.MARGIN_X,
            0.15,
            geo.CONTENT_W,
            geo.BAND_H - 0.3,
            [
                self.para(
                    title,
                    self.theme.sizes.slide_title_pt * self.style.title_scale,
                    c.slide_title_text,
                    bold=True,
                    font=self.theme.fonts.heading_font,
                )
            ],
            anchor="middle",
        )
        self.footer(number)

    # ------------------------------------------------------------------ scene primitives
    def scene(self, prims: list[Prim]) -> None:
        """Draw scene primitives (see :mod:`app.automation.deck_scene`) as CSS and SVG."""
        for prim in prims:
            if isinstance(prim, Box):
                self._scene_box(prim)
            elif isinstance(prim, TextBox):
                self._scene_text(prim)
            elif isinstance(prim, IconShape):
                self._scene_icon(prim)
            elif isinstance(prim, LineShape):
                self._scene_line(prim)
            elif isinstance(prim, ChartShape):
                self._scene_chart(prim)
            elif isinstance(prim, ImageShape):
                self._scene_image(prim)

    @staticmethod
    def _rgba(color: str, alpha: float) -> str:
        r, g, b = int(color[1:3], 16), int(color[3:5], 16), int(color[5:7], 16)
        return f"rgba({r},{g},{b},{alpha:.3f})"

    def _scene_box(self, box: Box) -> None:
        css = "box-sizing:border-box;"
        if box.gradient is not None:
            g = box.gradient
            css += (
                f"background:linear-gradient({g.angle:g}deg,{self._rgba(g.start, box.alpha)},"
                f"{self._rgba(g.end, box.alpha)});"
            )
        elif box.fill is not None:
            css += f"background:{self._rgba(box.fill, box.alpha)};"
        if box.line is not None:
            css += f"border:{_pt(box.line_w)} solid {self._rgba(box.line, box.line_alpha)};"
        if box.kind == "oval":
            css += "border-radius:50%;"
        elif box.kind == "round" and box.radius > 0:
            css += f"border-radius:{_pad(box.radius)};"
        elif box.kind == "chevron":
            css += "clip-path:polygon(0 0,68% 0,100% 50%,68% 100%,0 100%,32% 50%);"
        elif box.kind == "pentagon":
            css += "clip-path:polygon(0 0,68% 0,100% 50%,68% 100%,0 100%);"
        shadows = {
            "soft": f"0 {_pt(5)} {_pt(18)} rgba(0,0,0,0.24)",
            "lift": f"0 {_pt(10)} {_pt(30)} rgba(0,0,0,0.32)",
            "glow": f"0 0 {_pt(26)} {self._rgba(box.glow or '#000000', 0.42)}",
        }
        if box.shadow in shadows:
            css += f"box-shadow:{shadows[box.shadow]};"
        self.box(box.x, box.y, box.w, box.h, css)

    def _scene_text(self, prim: TextBox) -> None:
        fonts = self.theme.fonts
        family = {"heading": fonts.heading_font, "mono": fonts.mono_font}
        paras = [
            self.para(
                p.text,
                p.size,
                p.color,
                bold=p.bold,
                italic=p.italic,
                font=family.get(p.font),
                align=p.align,
                bullet=p.bullet,
                space_after=p.space_after,
            )
            for p in prim.paras
        ]
        self.text(prim.x, prim.y, prim.w, prim.h, paras, anchor=prim.anchor, inset=prim.inset)

    def _overlay(self, inner: str) -> None:
        """An SVG layer covering the slide, in inch units (lines, icons and charts)."""
        self.parts.append(
            f'<svg viewBox="0 0 {geo.SLIDE_W} {geo.SLIDE_H}" preserveAspectRatio="none" '
            'style="position:absolute;left:0;top:0;width:100%;height:100%;overflow:visible" '
            f'aria-hidden="true">{inner}</svg>'
        )

    def _scene_icon(self, icon: IconShape) -> None:
        path = deck_icons.icon_svg_path(icon.name)
        if path is None:
            return
        scale = icon.size / deck_icons.GRID
        self._overlay(
            f'<g transform="translate({icon.x:.4f} {icon.y:.4f}) scale({scale:.5f})" fill="none" '
            f'stroke="{icon.color}" stroke-width="{deck_icons.STROKE:g}" stroke-linecap="round" '
            f'stroke-linejoin="round" opacity="{icon.alpha:.2f}"><path d="{path}"/></g>'
        )

    def _scene_line(self, line: LineShape) -> None:
        dash = ' stroke-dasharray="0.08 0.06"' if line.dash else ""
        self._overlay(
            f'<line x1="{line.x1:.4f}" y1="{line.y1:.4f}" x2="{line.x2:.4f}" y2="{line.y2:.4f}" '
            f'stroke="{line.color}" stroke-width="{line.width / 72:.4f}" '
            f'stroke-opacity="{line.alpha:.2f}" stroke-linecap="round"{dash}/>'
        )

    def _scene_image(self, image: ImageShape) -> None:
        src = image.src
        if image.data:
            src = "data:image/png;base64," + base64.b64encode(image.data).decode("ascii")
        if not src.startswith("data:"):
            self.scene(
                [
                    scene_card(self.ctx, image.x, image.y, image.w, image.h),
                    TextBox(
                        image.x,
                        image.y,
                        image.w,
                        image.h,
                        (
                            deck_para(
                                self.ctx,
                                f"Image unavailable: {image.alt}",
                                16,
                                self.theme.colors.muted,
                                align="center",
                            ),
                        ),
                        anchor="middle",
                    ),
                ]
            )
            return
        radius = f"border-radius:{_pad(image.radius)};" if image.radius > 0 else ""
        fit = "contain" if image.fit else "fill"
        self.box(
            image.x,
            image.y,
            image.w,
            image.h,
            f"overflow:hidden;{radius}",
            f'<img alt="{esc(image.alt)}" src="{esc(src)}" '
            f'style="width:100%;height:100%;object-fit:{fit}">',
        )

    def _scene_chart(self, chart: ChartShape) -> None:
        """A simple SVG drawing of a native chart (inch units): enough to judge the layout."""
        values = [v for s in chart.series for v in s.values]
        peak = max([*values, 1.0])
        magnitude = 10 ** math.floor(math.log10(peak))
        top = next(m * magnitude for m in (1, 2, 2.5, 4, 5, 8, 10) if m * magnitude >= peak)
        pt = chart.font_pt / 72
        parts: list[str] = []
        text = f'fill="{chart.text_color}" font-family="{esc(chart.font)}" font-size="{pt:.3f}"'
        count = len(chart.categories)
        multi = len(chart.series) > 1 or chart.kind == "doughnut"
        legend_h = pt * 2.2 if multi else 0.0
        label_h = pt * 1.8 if chart.kind != "doughnut" else 0.0
        x0, y0 = chart.x + 0.1, chart.y + 0.1
        w, h = chart.w - 0.2, chart.h - 0.2 - legend_h - label_h
        if chart.kind == "doughnut":
            total = sum(chart.series[0].values) or 1.0
            cx, cy, r = x0 + w / 2, y0 + h / 2, min(w, h) / 2
            angle = -90.0
            for index, value in enumerate(chart.series[0].values):
                sweep = 359.99 * value / total
                a1, a2 = math.radians(angle), math.radians(angle + sweep)
                large = 1 if sweep > 180 else 0
                p1 = (cx + r * math.cos(a1), cy + r * math.sin(a1))
                p2 = (cx + r * math.cos(a2), cy + r * math.sin(a2))
                color = chart.colors[index % len(chart.colors)]
                parts.append(
                    f'<path d="M{cx:.3f} {cy:.3f} L{p1[0]:.3f} {p1[1]:.3f} A{r:.3f} {r:.3f} 0 '
                    f'{large} 1 {p2[0]:.3f} {p2[1]:.3f}Z" fill="{color}" '
                    f'stroke="{chart.grid_color}" stroke-width="0.014"/>'
                )
                angle += sweep
            hole = self.theme.colors.card_bg
            parts.append(f'<circle cx="{cx:.3f}" cy="{cy:.3f}" r="{r * 0.62:.3f}" fill="{hole}"/>')
        else:
            for step in range(5):
                gy = y0 + h - h * step / 4
                parts.append(
                    f'<line x1="{x0 + 0.5:.3f}" y1="{gy:.3f}" x2="{x0 + w:.3f}" y2="{gy:.3f}" '
                    f'stroke="{chart.grid_color}" stroke-width="0.012"/>'
                )
                parts.append(
                    f'<text x="{x0 + 0.42:.3f}" y="{gy + pt / 3:.3f}" text-anchor="end" {text}>'
                    f"{esc(f'{top * step / 4:g}')}</text>"
                )
            plot_x, plot_w = x0 + 0.6, w - 0.7
            slot = plot_w / max(1, count)
            for index, name in enumerate(chart.categories):
                parts.append(
                    f'<text x="{plot_x + slot * (index + 0.5):.3f}" y="{y0 + h + pt * 1.1:.3f}" '
                    f'text-anchor="middle" {text}>{esc(name)}</text>'
                )
            per = len(chart.series)
            for s_index, series in enumerate(chart.series):
                color = chart.colors[s_index % len(chart.colors)]
                points: list[str] = []
                for index, value in enumerate(series.values):
                    height = h * value / top
                    if chart.kind == "line":
                        px = plot_x + slot * (index + 0.5)
                        py = y0 + h - height
                        points.append(f"{px:.3f},{py:.3f}")
                        parts.append(
                            f'<circle cx="{px:.3f}" cy="{py:.3f}" r="0.06" fill="{color}"/>'
                        )
                    else:
                        bar = slot * 0.58 / per
                        bx = plot_x + slot * 0.21 + slot * index + bar * s_index
                        parts.append(
                            f'<rect x="{bx:.3f}" y="{y0 + h - height:.3f}" width="{bar:.3f}" '
                            f'height="{height:.3f}" rx="0.04" fill="{color}"/>'
                        )
                if chart.kind == "line":
                    parts.append(
                        f'<polyline points="{" ".join(points)}" fill="none" stroke="{color}" '
                        'stroke-width="0.04" stroke-linejoin="round"/>'
                    )
        if multi:
            names = (
                chart.categories
                if chart.kind == "doughnut"
                else tuple(s.name for s in chart.series)
            )
            lx = x0
            ly = chart.y + chart.h - legend_h
            for index, name in enumerate(names):
                color = chart.colors[index % len(chart.colors)]
                parts.append(
                    f'<rect x="{lx:.3f}" y="{ly:.3f}" width="0.16" height="0.16" fill="{color}"/>'
                )
                parts.append(
                    f'<text x="{lx + 0.24:.3f}" y="{ly + 0.15:.3f}" {text}>{esc(name)}</text>'
                )
                lx += 0.5 + len(name) * pt * 0.55
        self._overlay("".join(parts))

    def picture(self, src: str, alt: str, x: float, y: float, w: float, h: float) -> bool:
        if not src.startswith("data:"):
            return False
        self.box(
            x,
            y,
            w,
            h,
            "display:flex;align-items:center;justify-content:center",
            f'<img alt="{esc(alt)}" src="{esc(src)}" style="max-width:100%;max-height:100%">',
        )
        return True


def _hero(page: _Page, slide: Slide, number: int, *, closing: bool, section: bool) -> None:
    c = page.theme.colors
    page.decorate(band=False)
    top = 2.3 if closing else (3.0 if section else 2.8)
    if section and slide.number:
        page.text(
            geo.MARGIN_X,
            1.1,
            geo.CONTENT_W,
            1.9,
            [
                page.para(
                    slide.number,
                    page.theme.sizes.slide_big_pt * page.style.title_scale * 1.6,
                    c.accent,
                    bold=True,
                    font=page.theme.fonts.heading_font,
                )
            ],
            anchor="bottom",
        )
        top = 3.4
    page.box(geo.MARGIN_X, top - 0.3, 1.4, 0.09, f"background:{c.accent}")
    paras = [
        page.para(
            slide.title,
            page.theme.sizes.slide_big_pt * page.style.title_scale - (8 if section else 0),
            c.slide_title_text,
            bold=True,
            font=page.theme.fonts.heading_font,
        )
    ]
    if slide.subtitle:
        paras.append(page.para(slide.subtitle, 18, c.slide_title_text))
    page.text(geo.MARGIN_X, top, geo.CONTENT_W, 3.0, paras)
    page.text(
        geo.MARGIN_X, 0.6, 4.0, 0.4, [page.para("MAYA AI", 14, c.accent, bold=True)], inset=0.02
    )
    if closing and slide.bullets:
        page.text(
            geo.MARGIN_X,
            5.5,
            geo.CONTENT_W,
            1.3,
            [page.para(b, 18, c.slide_title_text, space_after=4) for b in slide.bullets],
        )


def _columns(page: _Page, slide: Slide, number: int) -> None:
    c = page.theme.colors
    gap, col_w, pad = 0.4, (geo.CONTENT_W - 0.4) / 2, 0.2
    page.header(slide.title, number)
    for index, col in enumerate((slide.left, slide.right)):
        x = geo.MARGIN_X + index * (col_w + gap)
        if col is None:
            page.surface(x, geo.BODY_TOP, col_w, geo.BODY_H, c.card_bg)
            label = slide.image.alt if slide.image else "Image"
            page.text(
                x,
                geo.BODY_TOP,
                col_w,
                geo.BODY_H,
                [page.para(f"Image placeholder: {label}", 18, c.muted, align="center")],
                anchor="middle",
                inset=pad,
            )
            continue
        page.surface(x, geo.BODY_TOP, col_w, geo.BODY_H, c.card_bg)
        paras = []
        if col.heading:
            paras.append(page.para(col.heading, 22, c.heading, bold=True, space_after=8))
        paras += [page.para(b, 18, c.text, bullet=True, space_after=8) for b in col.bullets]
        page.text(x, geo.BODY_TOP, col_w, geo.BODY_H, paras, inset=pad)


def _cards(page: _Page, slide: Slide, number: int) -> None:
    c = page.theme.colors
    page.header(slide.title, number)
    count = len(slide.cards)
    gap, pad = 0.35, 0.22
    card_w = (geo.CONTENT_W - gap * (count - 1)) / count
    top, height = geo.BODY_TOP + 0.15, 4.4
    for index, card in enumerate(slide.cards):
        x = geo.MARGIN_X + index * (card_w + gap)
        page.surface(x, top, card_w, height, c.card_bg)
        head = 0.0
        if card.glyph:
            head = 0.85
            page.box(
                x + pad,
                top + pad,
                0.6,
                0.6,
                f"background:{c.accent};border-radius:50%;color:{c.slide_title_text};"
                "display:flex;align-items:center;justify-content:center;"
                f"font-weight:700;font-size:{_pt(16)}",
                esc(card.glyph),
            )
        else:
            page.box(x + pad, top + 0.12, 0.8, 0.07, f"background:{c.accent}")
        paras = [page.para(card.title, 22, c.heading, bold=True, space_after=8)]
        if card.body:
            paras.append(page.para(card.body, 18, c.text))
        page.text(
            x, top + (0.2 if not head else head), card_w, height - head - 0.2, paras, inset=pad
        )


def _stats(page: _Page, slide: Slide, number: int) -> None:
    c = page.theme.colors
    page.header(slide.title, number)
    count = len(slide.metrics)
    gap, pad = 0.4, 0.15
    w = (geo.CONTENT_W - gap * (count - 1)) / count
    top, height = geo.BODY_TOP + 0.6, 3.4
    for index, metric in enumerate(slide.metrics):
        x = geo.MARGIN_X + index * (w + gap)
        page.surface(x, top, w, height, c.card_bg)
        page.box(x, top, w, 0.09, f"background:{c.accent}")
        page.text(
            x,
            top + 0.25,
            w,
            1.5,
            [
                page.para(
                    metric.value,
                    page.theme.sizes.slide_big_pt * page.style.title_scale,
                    c.heading,
                    bold=True,
                    font=page.theme.fonts.heading_font,
                    align="center",
                )
            ],
            anchor="middle",
            inset=pad,
        )
        page.text(
            x,
            top + 1.85,
            w,
            1.45,
            [page.para(metric.label, 18, c.muted, align="center")],
            inset=pad,
        )


def _timeline(page: _Page, slide: Slide, number: int) -> None:
    c = page.theme.colors
    page.header(slide.title, number)
    count = len(slide.steps)
    col_w = geo.CONTENT_W / count
    line_y = geo.BODY_TOP + 0.55
    page.box(
        geo.MARGIN_X + col_w / 2,
        line_y - 0.02,
        geo.CONTENT_W - col_w,
        0.05,
        f"background:{c.border}",
    )
    text_top = line_y + 0.55
    for index, step in enumerate(slide.steps):
        x = geo.MARGIN_X + index * col_w
        page.box(
            x + col_w / 2 - 0.3,
            line_y - 0.3,
            0.6,
            0.6,
            f"background:{c.accent};border-radius:50%;color:#FFFFFF;display:flex;"
            "align-items:center;justify-content:center;font-weight:700;"
            f"font-size:{_pt(18)}",
            str(index + 1),
        )
        paras = [page.para(step.title, 20, c.heading, bold=True, align="center", space_after=6)]
        if step.detail:
            paras.append(page.para(step.detail, 18, c.text, align="center"))
        page.text(x + 0.05, text_top, col_w - 0.1, geo.BODY_BOTTOM - text_top, paras)


def _comparison(page: _Page, slide: Slide, number: int) -> None:
    c = page.theme.colors
    page.header(slide.title, number)
    gap, pad, head_h = 0.4, 0.2, 0.7
    col_w = (geo.CONTENT_W - gap) / 2
    for index, col in enumerate((slide.left, slide.right)):
        assert col is not None
        x = geo.MARGIN_X + index * (col_w + gap)
        head_fill = c.primary if index == 0 else page.style.accent2
        page.surface(x, geo.BODY_TOP, col_w, geo.BODY_H, c.card_bg)
        page.box(x, geo.BODY_TOP, col_w, head_h, f"background:{head_fill}")
        heading = col.heading or ("Before" if index == 0 else "After")
        page.text(
            x,
            geo.BODY_TOP,
            col_w,
            head_h,
            [page.para(heading, 20, best_on(head_fill), bold=True, align="center")],
            anchor="middle",
            inset=0.04,
        )
        paras = [page.para(b, 18, c.text, bullet=True, space_after=8) for b in col.bullets]
        page.text(x, geo.BODY_TOP + head_h, col_w, geo.BODY_H - head_h, paras, inset=pad)


def _table(page: _Page, slide: Slide, number: int) -> None:
    c = page.theme.colors
    page.header(slide.title, number)
    assert slide.table is not None
    head = "".join(f"<th>{inline_html(h)}</th>" for h in slide.table.headers)
    rows = []
    for row in slide.table.rows:
        rows.append("<tr>" + "".join(f"<td>{inline_html(cell)}</td>" for cell in row) + "</tr>")
    css = (
        "width:100%;height:100%;border-collapse:collapse;font-size:"
        f"{_pt(16)};color:{c.text};font-family:'{esc(page.theme.fonts.body_font)}',sans-serif;"
        f"th{{background:{c.table_header_bg};color:{c.table_header_text};padding:6px 10px;"
        "text-align:left}"
        f"td{{padding:6px 10px;border-bottom:1px solid {c.border};background:{c.card_bg}}}"
    )
    page.box(
        geo.MARGIN_X,
        geo.BODY_TOP,
        geo.CONTENT_W,
        geo.BODY_H,
        "",
        f'<table style="{css}"><thead><tr>{head}</tr></thead>'
        f"<tbody>{''.join(rows)}</tbody></table>",
    )


def _code(page: _Page, slide: Slide, number: int) -> None:
    c = page.theme.colors
    page.header(slide.title, number)
    assert slide.code is not None
    page.surface(geo.MARGIN_X, geo.BODY_TOP, geo.CONTENT_W, geo.BODY_H, c.code_bg)
    if slide.code.language:
        page.text(
            geo.MARGIN_X + geo.CONTENT_W - 3.2,
            geo.BODY_TOP + 0.1,
            3.0,
            0.35,
            [page.para(slide.code.language, 12, c.muted, align="right")],
            inset=0.02,
        )
    lines = "\n".join(esc(line) for line in slide.code.text.split("\n"))
    page.text(
        geo.MARGIN_X,
        geo.BODY_TOP + (0.35 if slide.code.language else 0.0),
        geo.CONTENT_W,
        geo.BODY_H - (0.35 if slide.code.language else 0.0),
        [
            f"<pre style=\"margin:0;font-family:'{esc(page.theme.fonts.mono_font)}',monospace;"
            f'font-size:{_pt(16)};color:{c.code_text};white-space:pre">{lines}</pre>'
        ],
        inset=0.25,
    )


def _quote(page: _Page, slide: Slide, number: int) -> None:
    c = page.theme.colors
    page.decorate(band=False)
    page.box(0, 0, 0.35, geo.SLIDE_H, f"background:{c.accent}")
    if slide.title:
        page.text(
            1.2,
            0.6,
            geo.SLIDE_W - 2.4,
            0.6,
            [page.para(slide.title, 16, c.muted, bold=True)],
            inset=0.02,
        )
    page.text(
        1.2,
        1.7,
        geo.SLIDE_W - 2.4,
        3.5,
        [
            page.para(
                f"“{slide.quote}”", 36, c.heading, italic=True, font=page.theme.fonts.heading_font
            )
        ],
        anchor="middle",
    )
    if slide.by:
        page.text(
            1.2, 5.4, geo.SLIDE_W - 2.4, 0.6, [page.para(f"— {slide.by}", 18, c.muted)], inset=0.02
        )
    page.footer(number)


def _image(page: _Page, slide: Slide, number: int) -> None:
    c = page.theme.colors
    page.header(slide.title or "", number)
    assert slide.image is not None
    caption_h = 0.5 if slide.image.caption else 0.0
    box_h = geo.BODY_H - caption_h
    if not page.picture(
        slide.image.src, slide.image.alt, geo.MARGIN_X, geo.BODY_TOP, geo.CONTENT_W, box_h
    ):
        page.text(
            geo.MARGIN_X,
            geo.BODY_TOP,
            geo.CONTENT_W,
            box_h,
            [page.para(f"Image unavailable: {slide.image.alt}", 18, c.muted, align="center")],
            anchor="middle",
        )


def _bullets(page: _Page, slide: Slide, number: int) -> None:
    c = page.theme.colors
    page.header(slide.title, number)
    paras = [
        page.para(b, page.theme.sizes.slide_body_pt, c.text, bullet=True, space_after=8)
        for b in slide.bullets
    ]
    page.text(geo.MARGIN_X, geo.BODY_TOP, geo.CONTENT_W, geo.BODY_H, paras)


def _hero_end(theme: Theme, style: DeckStyle) -> str | None:
    """The same gradient rule as the PPTX cover, section and closing slides."""
    c = theme.colors
    if not style.background_end:
        return None
    for candidate in (
        mix(c.slide_title_bg, style.background_end, 0.35),
        mix(c.slide_title_bg, "#000000", 0.3),
    ):
        if contrast_ratio(c.slide_title_text, candidate) >= AA_RATIO:
            return candidate
    return None


_SCENE_LAYOUTS: dict[str, deck_layouts.Builder] = {
    "stat_grid": deck_layouts.stat_grid,
    "icon_cards": deck_layouts.icon_cards,
    "process": deck_layouts.process,
    "image_text": deck_layouts.image_text,
    "chart": deck_layouts.chart,
}
_HERO_KIND = {
    "cover": "cover",
    "hero_cover": "cover",
    "section": "section",
    "section_break": "section",
    "closing": "closing",
}


def _scene_slide(page: _Page, slide: Slide, number: int, builder: deck_layouts.Builder) -> None:
    page.header(slide.title, number)
    page.scene(builder(page.ctx, slide))


def _slide_html(deck: Deck, theme: Theme, style: DeckStyle, slide: Slide, number: int) -> str:
    page = _Page(theme, style, deck)
    c = theme.colors
    family = style.family != "classic"
    layout = slide.layout
    if layout in _HERO_KIND and not family:
        start, end = c.slide_title_bg, _hero_end(theme, style)
    else:
        start, end = style.background, style.background_end
    page.parts.append(
        f'<div style="position:absolute;inset:0;{page.background(start, end)}"></div>'
    )
    if layout in _HERO_KIND and family:
        page.decorate(band=False)
        page.scene(deck_layouts.hero(page.ctx, slide, _HERO_KIND[layout]))
    elif layout in ("cover", "hero_cover"):
        _hero(page, slide, number, closing=False, section=False)
        page.box(geo.SLIDE_W - 2.6, 0, 2.6, 1.6, f"background:{c.accent}")
    elif layout == "closing":
        _hero(page, slide, number, closing=True, section=False)
    elif layout in ("section", "section_break"):
        _hero(page, slide, number, closing=False, section=True)
    elif layout == "quote" and family:
        page.decorate(band=False)
        page.scene(deck_layouts.quote(page.ctx, slide))
        page.footer(number)
    elif layout in deck_layouts.FAMILY_BUILDERS and family:
        _scene_slide(page, slide, number, deck_layouts.FAMILY_BUILDERS[layout])
    elif layout in _SCENE_LAYOUTS:
        _scene_slide(page, slide, number, _SCENE_LAYOUTS[layout])
    elif layout == "quote":
        _quote(page, slide, number)
    elif layout == "two_column":
        _columns(page, slide, number)
    elif layout == "cards":
        _cards(page, slide, number)
    elif layout == "stat_callout":
        _stats(page, slide, number)
    elif layout == "timeline":
        _timeline(page, slide, number)
    elif layout == "comparison":
        _comparison(page, slide, number)
    elif layout == "table":
        _table(page, slide, number)
    elif layout == "code":
        _code(page, slide, number)
    elif layout == "image":
        _image(page, slide, number)
    else:
        _bullets(page, slide, number)
    return (
        f'<section class="page" aria-label="Slide {number}"><div class="slide">'
        + "".join(page.parts)
        + "</div></section>"
    )


def render_deck_html(deck: Deck) -> str:
    """The deck as one HTML document: one 16:9 section per slide, in a fixed 1280 x 720 frame
    (each ``section`` is absolutely positioned inside its frame, so it screenshots cleanly)."""
    checked, _notes = check_deck(deck)
    theme, style, _design_notes = resolve_deck(deck.design, deck.theme)
    sections: list[str] = []

    def emit(slide: Slide) -> None:
        # Like the PPTX engine: a slide whose text cannot fit at the smallest size is split.
        try:
            sections.append(_slide_html(checked, theme, style, slide, len(sections) + 1))
        except SceneFitError:
            parts = halve(slide)
            if parts is None:
                raise
            for part in parts:
                emit(part)

    for slide in checked.slides:
        emit(slide)
    body = "".join(sections)
    fonts = f"'{esc(theme.fonts.heading_font)}', '{esc(theme.fonts.body_font)}', sans-serif"
    return (
        '<!doctype html><html><head><meta charset="utf-8">'
        f"<title>{esc(deck.title)}</title>"
        "<style>@page{margin:0}html,body{margin:0;background:#FFFFFF;font-family:" + fonts + "}"
        f".page{{position:relative;width:{PAGE_W_PX}px;height:{PAGE_H_PX}px;overflow:hidden;"
        "background:#FFFFFF}"
        f".slide{{position:absolute;left:0;top:0;width:{PAGE_W_PX}px;height:{SLIDE_H_PX:.2f}px;"
        "overflow:hidden;container-type:inline-size}"
        "</style></head><body>" + body + "</body></html>"
    )


def write_deck_html(deck: Deck, path: Path | str) -> Path:
    """Write :func:`render_deck_html` to ``path`` (UTF-8). Returns the resolved path."""
    target = Path(path).resolve()
    target.parent.mkdir(parents=True, exist_ok=True)
    target.write_text(render_deck_html(deck), encoding="utf-8")
    return target


__all__ = ["render_deck_html", "write_deck_html"]
