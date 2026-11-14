"""PPTX generation: native, editable shapes driven by the AI's deck design.

Every slide is built from text boxes, native shapes (panels, bars, dots, circles), native tables,
and simple pictures, so text stays editable in PowerPoint. The design comes from
:func:`app.automation.design.resolve_deck`: a palette (solid or gradient background), fonts,
title scale, corner radius, shape style (flat, outlined, soft-shadow, glass), decorations and the
slide number. Each slide picks one layout from ``model.SLIDE_LAYOUTS``.

Text sizes come from a conservative character-count estimate. Text that does not fit at the
floor size splits the slide into continuation slides, and the split is reported in
``DeckRenderResult.repairs``. A slide with no visual element beyond text is reported in
``DeckRenderResult.warnings``. Nothing here renders glyphs, so a pass is an estimate.
"""

from __future__ import annotations

import io
import logging
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from lxml import etree
from pptx import Presentation
from pptx.dml.color import RGBColor
from pptx.enum.shapes import MSO_SHAPE, MSO_SHAPE_TYPE
from pptx.enum.text import MSO_ANCHOR, MSO_AUTO_SIZE, PP_ALIGN
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

from app.automation import deck_layouts, deck_pptx_scene
from app.automation.assets import decode_data_uri, make_image_resolver
from app.automation.deck_checks import check_deck, halve
from app.automation.deck_graphics import Ctx, family_header
from app.automation.deck_graphics import card as scene_card
from app.automation.deck_graphics import para as deck_para
from app.automation.deck_scene import (
    Box,
    ChartShape,
    Gradient,
    IconShape,
    ImageShape,
    LineShape,
    Prim,
    SceneFitError,
    TextBox,
)
from app.automation.deck_transitions import apply_transition
from app.automation.design import (
    AA_RATIO,
    DeckStyle,
    best_on,
    contrast_ratio,
    mix,
    resolve_deck,
)
from app.automation.html_render import ImageResolver
from app.automation.inline import parse_inline, plain_text, xml_safe
from app.automation.model import Deck, Slide, deck_from_dict
from app.automation.pptx_fit import overflows
from app.automation.textfit import estimate_height_in, fit_size

logger = logging.getLogger(__name__)

RENDERER = "python-pptx-native"

SLIDE_W = 13.333
SLIDE_H = 7.5
MARGIN_X = 0.7
CONTENT_W = SLIDE_W - 2 * MARGIN_X
BAND_H = 1.25
BODY_TOP = 1.7
BODY_BOTTOM = 6.75
BODY_H = BODY_BOTTOM - BODY_TOP
FOOTER_Y = 6.98
BULLET_INDENT_IN = 0.3
BOX_INSET = 0.12  # text-frame inset applied on all four sides of body text boxes
CODE_WIDTH_FACTOR = 1.1  # monospace glyphs are wider than the proportional estimate

# Visual elements are named with these prefixes so the warning check can find them.
DECORATION_PREFIX = "deco:"
VISUAL_PREFIX = "visual:"

# Kept for callers that imported the old constants.
SLIDE_WIDTH_INCHES = SLIDE_W
SLIDE_HEIGHT_INCHES = SLIDE_H


class SlideFitError(ValueError):
    """Text does not fit its slide area even at the smallest allowed font size."""


@dataclass(frozen=True)
class DeckRenderResult:
    path: Path
    renderer: str
    theme: str
    slide_count: int
    warnings: list[str] = field(default_factory=list)
    # Deterministic repairs made to the AI's deck or design (design notes, item-limit splits,
    # text-fit splits). Reported to the user; they never fail the render.
    repairs: list[str] = field(default_factory=list)


@dataclass
class _Para:
    text: str
    size: float
    bold: bool = False
    italic: bool = False
    color: str | None = None
    heading_font: bool = False
    align: Any = PP_ALIGN.LEFT
    space_after: float = 0.0
    bullet: bool = False
    mono: bool = False  # monospace font, inline markup still applied
    raw: bool = False  # one plain run, no inline markup (code)


def _rgb(value: str) -> RGBColor:
    color: RGBColor = RGBColor.from_string(value.lstrip("#").upper())
    return color


def _in(value: float) -> Emu:
    return Emu(int(Inches(value)))


def _name(shape: Any, name: str) -> None:
    """Name a shape. The first child of every shape element holds its ``cNvPr``."""
    shape._element.xpath("./*[1]/p:cNvPr")[0].set("name", name)


def _set_opacity(shape: Any, opacity: float) -> None:
    solid = shape._element.spPr.find(qn("a:solidFill"))
    if solid is not None and len(solid):
        etree.SubElement(solid[0], qn("a:alpha")).set("val", str(int(round(opacity * 100000))))


def _soft_shadow(shape: Any) -> None:
    """A soft outer shadow written as DrawingML (python-pptx has no blur API)."""
    spPr = shape._element.spPr
    for old in spPr.findall(qn("a:effectLst")):
        spPr.remove(old)
    effects = etree.SubElement(spPr, qn("a:effectLst"))
    shadow = etree.SubElement(
        effects,
        qn("a:outerShdw"),
        blurRad="190500",
        dist="50800",
        dir="5400000",
        algn="t",
        rotWithShape="0",
    )
    color = etree.SubElement(shadow, qn("a:srgbClr"), val="000000")
    etree.SubElement(color, qn("a:alpha"), val="22000")


class _DeckBuilder:
    def __init__(self, deck: Deck, resolver: ImageResolver | None) -> None:
        self.deck = deck
        self.theme, self.style, design_notes = resolve_deck(deck.design, deck.theme)
        self.repairs: list[str] = list(design_notes)
        self.resolver = resolver
        self.warnings: list[str] = []
        self.prs = Presentation()
        self.prs.slide_width = _in(SLIDE_W)
        self.prs.slide_height = _in(SLIDE_H)
        self.blank = self.prs.slide_layouts[6]
        self.ctx = Ctx(self.theme, self.style, deck.title)
        self.index = 0
        self.title_pt = self.theme.sizes.slide_title_pt * self.style.title_scale
        self.big_pt = self.theme.sizes.slide_big_pt * self.style.title_scale
        self.prs.core_properties.title = deck.title
        self.prs.core_properties.author = "MAYA AI"
        self.prs.core_properties.comments = f"theme={deck.theme}; renderer={RENDERER}"

    # ------------------------------------------------------------------ primitives
    def _fail(self, message: str) -> SlideFitError:
        return SlideFitError(f"Slide {self.index}: {message}")

    def _set_run_fonts(self, run: Any, font_name: str) -> None:
        run.font.name = font_name
        latin = run._r.get_or_add_rPr().find(qn("a:latin"))
        if latin is not None and run._r.rPr.find(qn("a:cs")) is None:
            cs = etree.SubElement(run._r.rPr, qn("a:cs"))
            cs.set("typeface", self.theme.fonts.complex_script_font)
            latin.addnext(cs)

    def _add_runs(self, paragraph: Any, para: _Para) -> None:
        theme = self.theme
        default_color = para.color or theme.colors.text
        if para.raw:
            run = paragraph.add_run()
            run.text = para.text or " "
            self._set_run_fonts(run, theme.fonts.mono_font)
            run.font.size = Pt(para.size)
            run.font.color.rgb = _rgb(default_color)
            return
        if para.mono:
            base_font = theme.fonts.mono_font
        elif para.heading_font:
            base_font = theme.fonts.heading_font
        else:
            base_font = theme.fonts.body_font
        for span in parse_inline(xml_safe(para.text)):
            pieces = span.text.split("\n")
            for number, piece in enumerate(pieces):
                if number:
                    paragraph.add_line_break()
                if not piece:
                    continue
                run = paragraph.add_run()
                run.text = piece
                self._set_run_fonts(run, theme.fonts.mono_font if span.code else base_font)
                run.font.size = Pt(para.size)
                run.font.bold = para.bold or span.bold
                run.font.italic = para.italic or span.italic
                run.font.color.rgb = _rgb(theme.colors.link if span.href else default_color)
                if span.href:
                    run.hyperlink.address = span.href

    def _make_bullet(self, paragraph: Any) -> None:
        pPr = paragraph._p.get_or_add_pPr()
        pPr.set("marL", str(int(_in(BULLET_INDENT_IN))))
        pPr.set("indent", str(-int(_in(BULLET_INDENT_IN))))
        color = etree.SubElement(pPr, qn("a:buClr"))
        etree.SubElement(color, qn("a:srgbClr")).set("val", self.theme.colors.accent.lstrip("#"))
        etree.SubElement(pPr, qn("a:buFont")).set("typeface", "Arial")
        etree.SubElement(pPr, qn("a:buChar")).set("char", "•")

    def _write(self, frame: Any, paras: list[_Para]) -> None:
        for number, para in enumerate(paras):
            paragraph = frame.paragraphs[0] if number == 0 else frame.add_paragraph()
            paragraph.alignment = para.align
            if para.space_after:
                paragraph.space_after = Pt(para.space_after)
            if para.bullet:
                self._make_bullet(paragraph)
            self._add_runs(paragraph, para)

    def _textbox(
        self,
        slide: Any,
        x: float,
        y: float,
        w: float,
        h: float,
        paras: list[_Para],
        *,
        anchor: Any = MSO_ANCHOR.TOP,
        inset: float = BOX_INSET,
    ) -> Any:
        box = slide.shapes.add_textbox(_in(x), _in(y), _in(w), _in(h))
        self._style_frame(box.text_frame, anchor, inset)
        self._write(box.text_frame, paras)
        self._check(box)
        return box

    def _style_frame(self, frame: Any, anchor: Any, inset: float) -> None:
        frame.word_wrap = True
        frame.auto_size = MSO_AUTO_SIZE.NONE
        frame.vertical_anchor = anchor
        frame.margin_left = frame.margin_right = _in(inset)
        frame.margin_top = frame.margin_bottom = _in(inset)

    def _check(self, shape: Any) -> None:
        if overflows(shape):
            raise self._fail("text does not fit its box; shorten the text or split the slide")

    def _shape(
        self,
        slide: Any,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: str,
        *,
        kind: Any = MSO_SHAPE.RECTANGLE,
        name: str | None = None,
    ) -> Any:
        shape = slide.shapes.add_shape(kind, _in(x), _in(y), _in(w), _in(h))
        shape.fill.solid()
        shape.fill.fore_color.rgb = _rgb(fill)
        shape.line.fill.background()
        shape.shadow.inherit = False
        if name:
            _name(shape, name)
        return shape

    def _rect(
        self,
        slide: Any,
        x: float,
        y: float,
        w: float,
        h: float,
        fill: str,
        *,
        line: str | None = None,
        name: str | None = None,
    ) -> Any:
        shape = self._shape(slide, x, y, w, h, fill, name=name)
        if line:
            shape.line.color.rgb = _rgb(line)
            shape.line.width = Pt(1)
        return shape

    def _oval(
        self,
        slide: Any,
        x: float,
        y: float,
        size: float,
        fill: str,
        *,
        opacity: float | None = None,
        name: str | None = None,
    ) -> Any:
        shape = self._shape(slide, x, y, size, size, fill, kind=MSO_SHAPE.OVAL, name=name)
        if opacity is not None:
            _set_opacity(shape, opacity)
        return shape

    def _panel(
        self,
        slide: Any,
        x: float,
        y: float,
        w: float,
        h: float,
        *,
        fill: str | None = None,
        name: str = VISUAL_PREFIX + "panel",
    ) -> Any:
        """A card-like surface drawn in the deck's shape style and corner radius."""
        if self.family != "classic":
            return deck_pptx_scene.draw_box(
                slide, scene_card(self.ctx, x, y, w, h, fill=fill, name=name)
            )
        c = self.theme.colors
        radius = self.style.corner_radius
        kind = MSO_SHAPE.ROUNDED_RECTANGLE if radius > 0 else MSO_SHAPE.RECTANGLE
        shape = self._shape(slide, x, y, w, h, fill or c.card_bg, kind=kind, name=name)
        if kind == MSO_SHAPE.ROUNDED_RECTANGLE:
            shape.adjustments[0] = min(0.5, (radius / 72.0) / max(0.01, min(w, h)))
        style = self.style.shape_style
        if style == "outlined":
            shape.fill.background()
            shape.line.color.rgb = _rgb(c.accent)
            shape.line.width = Pt(1.5)
        elif style == "glass":
            _set_opacity(shape, 0.6)
            shape.line.color.rgb = _rgb(c.border)
            shape.line.width = Pt(0.75)
        elif style == "soft-shadow":
            _soft_shadow(shape)
        return shape

    @property
    def family(self) -> str:
        """``classic`` for the original presets, ``glass`` or ``editorial`` for the library."""
        return self.style.family

    def _paint(self, slide: Any, start: str, end: str | None) -> None:
        fill = slide.background.fill
        if end and self.family != "classic":
            fill.gradient()
            grad = slide._element.xpath("./p:cSld/p:bg/p:bgPr/a:gradFill")[0]
            deck_pptx_scene.write_gradient(grad, Gradient(start, end, self.style.gradient_angle))
        elif end:
            fill.gradient()
            fill.gradient_angle = 90.0
            stops = fill.gradient_stops
            stops[0].color.rgb = _rgb(start)
            stops[0].position = 0.0
            stops[1].color.rgb = _rgb(end)
            stops[1].position = 1.0
        else:
            fill.solid()
            fill.fore_color.rgb = _rgb(start)

    def _page_background(self, slide: Any) -> None:
        self._paint(slide, self.style.background, self.style.background_end)

    def _hero_background(self, slide: Any) -> None:
        """Cover, section and closing: a gradient from the band colour toward the deck's second
        colour, or toward a darker shade of the band colour when the second colour would make the
        title text unreadable. Solid only when neither candidate keeps AA contrast."""
        c = self.theme.colors
        end = None
        if self.style.background_end:
            candidates = (
                mix(c.slide_title_bg, self.style.background_end, 0.35),
                mix(c.slide_title_bg, "#000000", 0.3),
            )
            for candidate in candidates:
                if contrast_ratio(c.slide_title_text, candidate) >= AA_RATIO:
                    end = candidate
                    break
        self._paint(slide, c.slide_title_bg, end)

    def _decorate(self, slide: Any, *, band: bool) -> None:
        """The deck's decorations, drawn in the order the AI listed them."""
        c = self.theme.colors
        for name in self.style.decorations:
            if name == "top_bar":
                self._rect(slide, 0, 0, SLIDE_W, 0.12, c.accent, name=DECORATION_PREFIX + name)
            elif name == "side_bar":
                self._rect(
                    slide, 0, 0, 0.16, SLIDE_H, self.style.accent2, name=DECORATION_PREFIX + name
                )
            elif name == "corner_circle":
                self._oval(
                    slide,
                    SLIDE_W - 1.35,
                    0.1,
                    1.0,
                    self.style.accent2,
                    opacity=0.35,
                    name=DECORATION_PREFIX + name,
                )
            elif name == "grid_dots":
                for col in range(4):
                    for row in range(3):
                        self._oval(
                            slide,
                            SLIDE_W - 1.45 + col * 0.3,
                            0.25 + row * 0.28,
                            0.09,
                            self.style.accent2,
                            name=DECORATION_PREFIX + name,
                        )
            elif name == "divider_line" and band:
                self._rect(slide, 0, BAND_H, SLIDE_W, 0.06, c.accent, name=DECORATION_PREFIX + name)

    def _slide_number(self, slide: Any, color: str) -> None:
        box = slide.shapes.add_textbox(
            _in(SLIDE_W - MARGIN_X - 1.0), _in(FOOTER_Y), _in(1.0), _in(0.35)
        )
        frame = box.text_frame
        self._style_frame(frame, MSO_ANCHOR.MIDDLE, 0.02)
        paragraph = frame.paragraphs[0]
        paragraph.alignment = PP_ALIGN.RIGHT
        fld = etree.SubElement(paragraph._p, qn("a:fld"))
        fld.set("id", "{B6F15528-21DE-4FAA-801E-634DDDAF4B2B}")
        fld.set("type", "slidenum")
        rpr = etree.SubElement(fld, qn("a:rPr"))
        rpr.set("lang", "en-US")
        rpr.set("sz", "1100")
        fill = etree.SubElement(rpr, qn("a:solidFill"))
        etree.SubElement(fill, qn("a:srgbClr")).set("val", color.lstrip("#"))
        etree.SubElement(rpr, qn("a:latin")).set("typeface", self.theme.fonts.body_font)
        etree.SubElement(fld, qn("a:t")).text = str(self.index)

    def _footer(self, slide: Any) -> None:
        c = self.theme.colors
        self._textbox(
            slide,
            MARGIN_X,
            FOOTER_Y,
            8.0,
            0.35,
            [_Para(self.style.footer_text or self.deck.title, 11, color=c.muted)],
            anchor=MSO_ANCHOR.MIDDLE,
            inset=0.02,
        )
        if self.style.slide_number:
            self._slide_number(slide, c.muted)

    def _content_header(self, slide: Any, title: str) -> None:
        if self.family != "classic":
            self._page_background(slide)
            self._decorate(slide, band=False)
            self._scene(slide, family_header(self.ctx, title))
            self._footer(slide)
            return
        c = self.theme.colors
        self._page_background(slide)
        self._rect(slide, 0, 0, SLIDE_W, BAND_H, c.slide_title_bg, name="header:band")
        self._decorate(slide, band=True)
        size = fit_size(
            lambda s: [(plain_text(title), s)],
            width_in=CONTENT_W - 2 * BOX_INSET,
            height_in=BAND_H - 0.3 - 2 * BOX_INSET,
            start_pt=self.title_pt,
            min_pt=20,
        )
        if size is None:
            raise self._fail(f"title '{title[:40]}...' is too long for the slide header")
        self._textbox(
            slide,
            MARGIN_X,
            0.15,
            CONTENT_W,
            BAND_H - 0.3,
            [_Para(title, size, bold=True, color=c.slide_title_text, heading_font=True)],
            anchor=MSO_ANCHOR.MIDDLE,
        )
        self._footer(slide)

    def _fit(
        self,
        build: Callable[[float], list[tuple[str, float]]],
        width: float,
        height: float,
        start: float,
        minimum: float,
        space_after: float = 0.0,
        what: str = "text",
    ) -> float:
        size = fit_size(
            build,
            width_in=width,
            height_in=height,
            start_pt=start,
            min_pt=minimum,
            space_after_pt=space_after,
        )
        if size is None:
            raise self._fail(f"{what} does not fit even at {minimum:g}pt; shorten it")
        return size

    def _picture_fit(
        self, slide: Any, src: str, alt: str, x: float, y: float, w: float, h: float
    ) -> bool:
        """A picture scaled to fit the box and centred in it; ``False`` when it cannot load."""
        if not src.startswith("data:"):
            src = (self.resolver(src) if self.resolver else None) or ""
        blob = decode_data_uri(src) if src else None
        if blob is None:
            return False
        try:
            from pptx.parts.image import Image as PptxImage

            px_w, px_h = PptxImage.from_blob(blob).size
            scale = min(w / px_w, h / px_h)
            pw, ph = px_w * scale, px_h * scale
            picture = slide.shapes.add_picture(
                io.BytesIO(blob),
                _in(x + (w - pw) / 2),
                _in(y + (h - ph) / 2),
                _in(pw),
                _in(ph),
            )
            picture._element.nvPicPr.cNvPr.set("descr", xml_safe(alt))
            return True
        except Exception:
            logger.warning("pptx_image_failed", exc_info=True)
            return False

    def _visual_check(self, slide: Any, model: Slide) -> None:
        for shape in slide.shapes:
            name = shape.name or ""
            if name.startswith((DECORATION_PREFIX, VISUAL_PREFIX)):
                return
            if shape.shape_type == MSO_SHAPE_TYPE.PICTURE or getattr(shape, "has_table", False):
                return
        self.warnings.append(
            f"Slide {self.index} ({model.layout}) has no visual element beyond text; "
            "add decorations or use a layout with panels"
        )

    def _drop_last_slide(self) -> None:
        slide_ids = self.prs.slides._sldIdLst
        last = slide_ids[-1]
        self.prs.part.drop_rel(last.rId)
        slide_ids.remove(last)

    # ------------------------------------------------------------------ layouts
    def build(self) -> None:
        for model in self.deck.slides:
            self._render(model)

    def _render(self, model: Slide) -> None:
        """Render one slide. Text that does not fit at the floor size is split in two and each
        half rendered as a continuation slide; the split is reported in ``repairs``."""
        self.index += 1
        number = self.index
        slide = self.prs.slides.add_slide(self.blank)
        try:
            getattr(self, f"_layout_{model.layout}")(slide, model)
        except (SlideFitError, SceneFitError):
            self._drop_last_slide()
            self.index -= 1
            parts = halve(model)
            if parts is None:
                raise
            self.repairs.append(
                f"slide {number} ({model.layout}) text did not fit even at the smallest size; "
                f"split into {len(parts)} slides"
            )
            for part in parts:
                self._render(part)
            return
        self._visual_check(slide, model)
        self._notes(slide, model.notes)
        apply_transition(slide, model.transition or self.deck.transition or self.style.transition)

    # ------------------------------------------------------------------ scene primitives
    def _scene(self, slide: Any, prims: list[Prim]) -> None:
        """Draw scene primitives (see :mod:`app.automation.deck_scene`) as native shapes."""
        for prim in prims:
            if isinstance(prim, Box):
                deck_pptx_scene.draw_box(slide, prim)
            elif isinstance(prim, TextBox):
                self._scene_text(slide, prim)
            elif isinstance(prim, IconShape):
                deck_pptx_scene.draw_icon(slide, prim)
            elif isinstance(prim, LineShape):
                deck_pptx_scene.draw_line(slide, prim)
            elif isinstance(prim, ChartShape):
                deck_pptx_scene.draw_chart(slide, prim)
            elif isinstance(prim, ImageShape):
                self._scene_image(slide, prim)

    def _scene_text(self, slide: Any, prim: TextBox) -> None:
        align = {"left": PP_ALIGN.LEFT, "center": PP_ALIGN.CENTER, "right": PP_ALIGN.RIGHT}
        anchor = {
            "top": MSO_ANCHOR.TOP,
            "middle": MSO_ANCHOR.MIDDLE,
            "bottom": MSO_ANCHOR.BOTTOM,
        }[prim.anchor]
        paras = [
            _Para(
                p.text,
                p.size,
                bold=p.bold,
                italic=p.italic,
                color=p.color,
                heading_font=p.font == "heading",
                mono=p.font == "mono",
                align=align[p.align],
                space_after=p.space_after,
                bullet=p.bullet,
            )
            for p in prim.paras
        ]
        box = self._textbox(
            slide, prim.x, prim.y, prim.w, prim.h, paras, anchor=anchor, inset=prim.inset
        )
        _name(box, prim.name)

    def _scene_image(self, slide: Any, prim: ImageShape) -> None:
        blob: bytes | None = prim.data or None
        if blob is None and prim.src:
            src = prim.src
            if not src.startswith("data:"):
                src = (self.resolver(src) if self.resolver else None) or ""
            blob = decode_data_uri(src) if src else None
        size: tuple[int, int] | None = None
        if blob is not None:
            try:
                from pptx.parts.image import Image as PptxImage

                size = PptxImage.from_blob(blob).size
            except Exception:
                logger.warning("pptx_image_failed", exc_info=True)
        if blob is None or size is None:
            self._scene(
                slide,
                [
                    scene_card(self.ctx, prim.x, prim.y, prim.w, prim.h),
                    TextBox(
                        prim.x,
                        prim.y,
                        prim.w,
                        prim.h,
                        (
                            deck_para(
                                self.ctx,
                                f"Image unavailable: {prim.alt}",
                                16,
                                self.theme.colors.muted,
                                align="center",
                            ),
                        ),
                        anchor="middle",
                        name="image-unavailable",
                    ),
                ],
            )
            return
        deck_pptx_scene.draw_image(slide, prim, blob, size)

    def _scene_layout(
        self,
        slide: Any,
        model: Slide,
        builder: deck_layouts.Builder,
        *,
        header: bool = True,
    ) -> None:
        """A layout drawn from a scene builder, with the deck's header (or none) and footer."""
        if header:
            self._content_header(slide, model.title)
        else:
            self._page_background(slide)
            self._decorate(slide, band=False)
        self._scene(slide, builder(self.ctx, model))
        if not header:
            self._footer(slide)

    def _notes(self, slide: Any, notes: str | None) -> None:
        if notes:
            slide.notes_slide.notes_text_frame.text = xml_safe(plain_text(notes))

    def _hero(
        self, slide: Any, model: Slide, *, closing: bool = False, section: bool = False
    ) -> None:
        if self.family != "classic":
            kind = "closing" if closing else ("section" if section else "cover")
            self._page_background(slide)
            self._decorate(slide, band=False)
            self._scene(slide, deck_layouts.hero(self.ctx, model, kind))
            return
        c = self.theme.colors
        self._hero_background(slide)
        self._decorate(slide, band=False)
        top = 2.3 if closing else (3.0 if section else 2.8)
        inner_w = CONTENT_W - 2 * BOX_INSET
        if section and model.number:
            numeral_color = (
                c.accent
                if contrast_ratio(c.accent, c.slide_title_bg) >= AA_RATIO
                else c.slide_title_text
            )
            numeral = self._fit(
                lambda s: [(plain_text(model.number), s)],
                inner_w,
                1.9 - 2 * BOX_INSET,
                max(48.0, self.big_pt * 1.6),
                48,
                what="section number",
            )
            self._textbox(
                slide,
                MARGIN_X,
                1.1,
                CONTENT_W,
                1.9,
                [
                    _Para(
                        model.number,
                        numeral,
                        bold=True,
                        color=numeral_color,
                        heading_font=True,
                    )
                ],
                anchor=MSO_ANCHOR.BOTTOM,
            )
            top = 3.4
        self._rect(slide, MARGIN_X, top - 0.3, 1.4, 0.09, c.accent, name=VISUAL_PREFIX + "rule")
        title_size = self._fit(
            lambda s: [(plain_text(model.title), s)],
            inner_w,
            1.7,
            self.big_pt - (8 if section else 0),
            26,
            what="title",
        )
        paras = [
            _Para(
                model.title,
                title_size,
                bold=True,
                color=c.slide_title_text,
                heading_font=True,
                space_after=10,
            )
        ]
        if model.subtitle:
            sub_size = min(22.0, title_size * 0.5 + 4)
            paras.append(_Para(model.subtitle, sub_size, color=c.slide_title_text))
        self._textbox(slide, MARGIN_X, top, CONTENT_W, 3.0, paras)
        self._textbox(
            slide,
            MARGIN_X,
            0.6,
            4.0,
            0.4,
            [_Para("MAYA AI", 14, bold=True, color=c.accent)],
            inset=0.02,
        )
        if closing and model.bullets:
            size = self._fit(
                lambda s: [(plain_text(b), s) for b in model.bullets],
                inner_w,
                1.3 - 2 * BOX_INSET,
                18,
                12,
                space_after=4,
                what="closing lines",
            )
            self._textbox(
                slide,
                MARGIN_X,
                5.5,
                CONTENT_W,
                1.3,
                [_Para(b, size, color=c.slide_title_text, space_after=4) for b in model.bullets],
            )

    def _layout_cover(self, slide: Any, model: Slide) -> None:
        c = self.theme.colors
        self._hero(slide, model)
        if self.family != "classic":
            return
        self._rect(slide, SLIDE_W - 2.6, 0, 2.6, 1.6, c.accent, name=VISUAL_PREFIX + "accent_block")

    def _layout_closing(self, slide: Any, model: Slide) -> None:
        self._hero(slide, model, closing=True)

    # Library layouts (see deck_layouts.py). On a classic preset the two title layouts draw as the
    # classic cover and section; the others draw with the preset's colours.
    def _layout_hero_cover(self, slide: Any, model: Slide) -> None:
        self._layout_cover(slide, model)

    def _layout_section_break(self, slide: Any, model: Slide) -> None:
        self._layout_section(slide, model)

    def _layout_stat_grid(self, slide: Any, model: Slide) -> None:
        self._scene_layout(slide, model, deck_layouts.stat_grid)

    def _layout_icon_cards(self, slide: Any, model: Slide) -> None:
        self._scene_layout(slide, model, deck_layouts.icon_cards)

    def _layout_process(self, slide: Any, model: Slide) -> None:
        self._scene_layout(slide, model, deck_layouts.process)

    def _layout_image_text(self, slide: Any, model: Slide) -> None:
        self._scene_layout(slide, model, deck_layouts.image_text)

    def _layout_chart(self, slide: Any, model: Slide) -> None:
        self._scene_layout(slide, model, deck_layouts.chart)

    def _layout_section(self, slide: Any, model: Slide) -> None:
        self._hero(slide, model, section=True)

    def _layout_bullets(self, slide: Any, model: Slide) -> None:
        c, sz = self.theme.colors, self.theme.sizes
        self._content_header(slide, model.title)
        width = CONTENT_W - 2 * BOX_INSET - BULLET_INDENT_IN
        height = BODY_H - 2 * BOX_INSET
        size = self._fit(
            lambda s: [(plain_text(b), s) for b in model.bullets],
            width,
            height,
            sz.slide_body_pt,
            sz.slide_min_body_pt,
            space_after=8,
            what="bullets",
        )
        paras = [_Para(b, size, color=c.text, space_after=8, bullet=True) for b in model.bullets]
        self._textbox(slide, MARGIN_X, BODY_TOP, CONTENT_W, BODY_H, paras)

    def _layout_two_column(self, slide: Any, model: Slide) -> None:
        assert model.left is not None
        c = self.theme.colors
        self._content_header(slide, model.title)
        gap = 0.4
        col_w = (CONTENT_W - gap) / 2
        pad = 0.2
        inner_w = col_w - 2 * pad - BULLET_INDENT_IN
        columns = [model.left, model.right]
        present = [col for col in columns if col is not None]

        def tallest(size: float) -> list[tuple[str, float]]:
            worst: list[tuple[str, float]] = []
            worst_h = -1.0
            for col in present:
                items = [(plain_text(col.heading), size + 2)] if col.heading else []
                items += [(plain_text(b), size) for b in col.bullets]
                h = estimate_height_in(items, inner_w, 8)
                if h > worst_h:
                    worst, worst_h = items, h
            return worst

        size = self._fit(
            tallest, inner_w, BODY_H - 2 * pad, 20, 12, space_after=8, what="column text"
        )
        for number, col in enumerate(columns):
            x = MARGIN_X + number * (col_w + gap)
            if col is None:
                self._picture_panel(slide, model, x, col_w)
                continue
            self._panel(slide, x, BODY_TOP, col_w, BODY_H)
            paras: list[_Para] = []
            if col.heading:
                paras.append(
                    _Para(
                        col.heading,
                        size + 2,
                        bold=True,
                        color=c.heading,
                        heading_font=True,
                        space_after=8,
                    )
                )
            paras += [_Para(b, size, color=c.text, space_after=8, bullet=True) for b in col.bullets]
            self._textbox(slide, x, BODY_TOP, col_w, BODY_H, paras, inset=pad)

    def _picture_panel(self, slide: Any, model: Slide, x: float, w: float) -> None:
        """The image half of a two-column slide: the picture, or a labelled placeholder."""
        c = self.theme.colors
        pad = 0.2
        self._panel(slide, x, BODY_TOP, w, BODY_H, name=VISUAL_PREFIX + "picture_panel")
        image = model.image
        if image is not None and image.src:
            if self._picture_fit(
                slide, image.src, image.alt, x + pad, BODY_TOP + pad, w - 2 * pad, BODY_H - 2 * pad
            ):
                return
            self.warnings.append(
                f"Slide {self.index}: image '{image.src[:60]}' could not be loaded"
            )
        label = image.alt if image is not None else "Image"
        self._textbox(
            slide,
            x,
            BODY_TOP,
            w,
            BODY_H,
            [
                _Para(
                    f"Image placeholder: {label}",
                    18,
                    color=c.muted,
                    align=PP_ALIGN.CENTER,
                )
            ],
            anchor=MSO_ANCHOR.MIDDLE,
            inset=pad,
        )

    def _layout_cards(self, slide: Any, model: Slide) -> None:
        if self.family != "classic":
            self._scene_layout(slide, model, deck_layouts.icon_cards)
            return
        c = self.theme.colors
        self._content_header(slide, model.title)
        count = len(model.cards)
        gap = 0.35
        card_w = (CONTENT_W - gap * (count - 1)) / count
        pad = 0.22
        height = 4.4
        top = BODY_TOP + 0.15
        inner_w = card_w - 2 * pad
        has_glyph = any(card.glyph for card in model.cards)
        badge = 0.6 if has_glyph else 0.0
        head_h = (pad + badge + 0.15) if has_glyph else 0.2

        # Measure the tallest card: sizes are shared so cards look uniform.
        def tallest(size: float) -> list[tuple[str, float]]:
            worst: list[tuple[str, float]] = []
            worst_h = -1.0
            for card in model.cards:
                items = [(plain_text(card.title), size + 4)]
                if card.body:
                    items.append((plain_text(card.body), size))
                h = estimate_height_in(items, inner_w, 8)
                if h > worst_h:
                    worst, worst_h = items, h
            return worst

        size = self._fit(
            tallest,
            inner_w,
            height - head_h - 2 * pad,
            18,
            12,
            space_after=8,
            what="card text",
        )
        for number, card in enumerate(model.cards):
            x = MARGIN_X + number * (card_w + gap)
            self._panel(slide, x, top, card_w, height, name=VISUAL_PREFIX + "card")
            if card.glyph:
                badge_shape = self._oval(
                    slide,
                    x + pad,
                    top + pad,
                    badge,
                    c.accent,
                    name=VISUAL_PREFIX + "card_badge",
                )
                self._write(
                    badge_shape.text_frame,
                    [
                        _Para(
                            card.glyph,
                            16,
                            bold=True,
                            color=best_on(c.accent),
                            align=PP_ALIGN.CENTER,
                        )
                    ],
                )
                self._style_frame(badge_shape.text_frame, MSO_ANCHOR.MIDDLE, 0.0)
            else:
                self._rect(
                    slide,
                    x + pad,
                    top + 0.12,
                    0.8,
                    0.07,
                    c.accent,
                    name=VISUAL_PREFIX + "card_stub",
                )
            paras = [
                _Para(
                    card.title,
                    size + 4,
                    bold=True,
                    color=c.heading,
                    heading_font=True,
                    space_after=8,
                )
            ]
            if card.body:
                paras.append(_Para(card.body, size, color=c.text))
            self._textbox(
                slide,
                x,
                top + head_h,
                card_w,
                height - head_h,
                paras,
                inset=pad,
            )

    def _layout_stat_callout(self, slide: Any, model: Slide) -> None:
        if self.family != "classic":
            self._scene_layout(slide, model, deck_layouts.stat_grid)
            return
        c = self.theme.colors
        self._content_header(slide, model.title)
        count = len(model.metrics)
        gap = 0.4
        w = (CONTENT_W - gap * (count - 1)) / count
        pad = 0.15
        inner_w = w - 2 * pad
        height = 3.4
        top = BODY_TOP + 0.6
        value_size = self._fit(
            lambda s: [(max((plain_text(m.value) for m in model.metrics), key=len), s)],
            inner_w,
            1.5 - 2 * pad,
            self.big_pt,
            20,
            what="metric value",
        )
        label_size = self._fit(
            lambda s: [(max((plain_text(m.label) for m in model.metrics), key=len), s)],
            inner_w,
            1.45 - 2 * pad,
            18,
            12,
            what="metric label",
        )
        for number, metric in enumerate(model.metrics):
            x = MARGIN_X + number * (w + gap)
            self._panel(slide, x, top, w, height, name=VISUAL_PREFIX + "stat_panel")
            self._rect(slide, x, top, w, 0.09, c.accent, name=VISUAL_PREFIX + "stat_strip")
            self._textbox(
                slide,
                x,
                top + 0.25,
                w,
                1.5,
                [
                    _Para(
                        metric.value,
                        value_size,
                        bold=True,
                        color=c.heading,
                        heading_font=True,
                        align=PP_ALIGN.CENTER,
                    )
                ],
                anchor=MSO_ANCHOR.MIDDLE,
                inset=pad,
            )
            self._textbox(
                slide,
                x,
                top + 1.85,
                w,
                1.45,
                [_Para(metric.label, label_size, color=c.muted, align=PP_ALIGN.CENTER)],
                inset=pad,
            )

    def _layout_quote(self, slide: Any, model: Slide) -> None:
        if self.family != "classic":
            self._scene_layout(slide, model, deck_layouts.quote, header=False)
            return
        c = self.theme.colors
        self._page_background(slide)
        self._decorate(slide, band=False)
        self._rect(slide, 0, 0, 0.35, SLIDE_H, c.accent, name=VISUAL_PREFIX + "quote_bar")
        if model.title:
            self._textbox(
                slide,
                1.2,
                0.6,
                SLIDE_W - 2.4,
                0.6,
                [_Para(model.title, 16, bold=True, color=c.muted)],
                inset=0.02,
            )
        inner_w = SLIDE_W - 2.4 - 2 * BOX_INSET
        size = self._fit(
            lambda s: [(plain_text(model.quote), s)],
            inner_w,
            3.3,
            36,
            18,
            what="quote",
        )
        self._textbox(
            slide,
            1.2,
            1.7,
            SLIDE_W - 2.4,
            3.5,
            [_Para(f"“{model.quote}”", size, italic=True, color=c.heading, heading_font=True)],
            anchor=MSO_ANCHOR.MIDDLE,
        )
        if model.by:
            self._textbox(
                slide,
                1.2,
                5.4,
                SLIDE_W - 2.4,
                0.6,
                [_Para(f"— {model.by}", 18, color=c.muted)],
                inset=0.02,
            )
        self._footer(slide)

    def _layout_table(self, slide: Any, model: Slide) -> None:
        assert model.table is not None
        c = self.theme.colors
        self._content_header(slide, model.title)
        table = model.table
        cols = len(table.headers)
        col_w = CONTENT_W / cols
        cell_pad_h, cell_pad_v = 0.08, 0.06
        inner_w = col_w - 2 * cell_pad_h

        def row_heights(size: float) -> list[float]:
            heights: list[float] = []
            for row_number, row in enumerate([table.headers, *table.rows]):
                tallest = max(
                    estimate_height_in([(plain_text(cell), size)], inner_w) for cell in row
                )
                heights.append(tallest + 2 * cell_pad_v + (0.04 if row_number == 0 else 0.0))
            return heights

        chosen = None
        size = 18.0
        while size >= 10.0:
            if sum(row_heights(size)) <= BODY_H:
                chosen = size
                break
            size -= 1.0
        if chosen is None:
            raise self._fail("table does not fit even at 10pt; reduce rows or text")
        heights = row_heights(chosen)
        total_h = sum(heights)
        frame = slide.shapes.add_table(
            len(heights), cols, _in(MARGIN_X), _in(BODY_TOP), _in(CONTENT_W), _in(total_h)
        )
        grid = frame.table
        for index in range(cols):
            grid.columns[index].width = _in(col_w)
        for index, height in enumerate(heights):
            grid.rows[index].height = _in(height)
        for row_number, row in enumerate([table.headers, *table.rows]):
            for col_number, text in enumerate(row):
                cell = grid.cell(row_number, col_number)
                cell.margin_left = cell.margin_right = _in(cell_pad_h)
                cell.margin_top = cell.margin_bottom = _in(cell_pad_v)
                cell.vertical_anchor = MSO_ANCHOR.MIDDLE
                cell.fill.solid()
                header = row_number == 0
                stripe = row_number % 2 == 0
                cell.fill.fore_color.rgb = _rgb(
                    c.table_header_bg if header else (c.table_stripe if stripe else c.card_bg)
                )
                paragraph = cell.text_frame.paragraphs[0]
                self._add_runs(
                    paragraph,
                    _Para(
                        text, chosen, bold=header, color=c.table_header_text if header else c.text
                    ),
                )

    def _layout_comparison(self, slide: Any, model: Slide) -> None:
        if self.family != "classic":
            self._scene_layout(slide, model, deck_layouts.comparison)
            return
        assert model.left is not None and model.right is not None
        c = self.theme.colors
        self._content_header(slide, model.title)
        gap = 0.4
        col_w = (CONTENT_W - gap) / 2
        pad = 0.2
        head_h = 0.7
        body_top = BODY_TOP + head_h
        body_h = BODY_H - head_h
        inner_w = col_w - 2 * pad - BULLET_INDENT_IN
        columns = (model.left, model.right)

        def tallest(size: float) -> list[tuple[str, float]]:
            worst: list[tuple[str, float]] = []
            worst_h = -1.0
            for col in columns:
                items = [(plain_text(b), size) for b in col.bullets]
                h = estimate_height_in(items, inner_w, 8)
                if h > worst_h:
                    worst, worst_h = items, h
            return worst

        size = self._fit(
            tallest, inner_w, body_h - 2 * pad, 20, 12, space_after=8, what="comparison"
        )
        for number, col in enumerate(columns):
            x = MARGIN_X + number * (col_w + gap)
            head_fill = c.primary if number == 0 else self.style.accent2
            self._panel(slide, x, BODY_TOP, col_w, BODY_H, name=VISUAL_PREFIX + "comparison_panel")
            self._rect(slide, x, BODY_TOP, col_w, head_h, head_fill, name=VISUAL_PREFIX + "head")
            heading = col.heading or ("Before" if number == 0 else "After")
            self._textbox(
                slide,
                x,
                BODY_TOP,
                col_w,
                head_h,
                [
                    _Para(
                        heading,
                        20,
                        bold=True,
                        color=best_on(head_fill),
                        heading_font=True,
                        align=PP_ALIGN.CENTER,
                    )
                ],
                anchor=MSO_ANCHOR.MIDDLE,
                inset=0.04,
            )
            paras = [_Para(b, size, color=c.text, space_after=8, bullet=True) for b in col.bullets]
            self._textbox(slide, x, body_top, col_w, body_h, paras, inset=pad)

    def _layout_timeline(self, slide: Any, model: Slide) -> None:
        if self.family != "classic":
            self._scene_layout(slide, model, deck_layouts.timeline)
            return
        c = self.theme.colors
        self._content_header(slide, model.title)
        count = len(model.steps)
        col_w = CONTENT_W / count
        pad = 0.1
        inner_w = col_w - 2 * pad - 0.1
        line_y = BODY_TOP + 0.55
        self._rect(
            slide,
            MARGIN_X + col_w / 2,
            line_y - 0.02,
            CONTENT_W - col_w,
            0.05,
            c.border,
            name=VISUAL_PREFIX + "timeline_line",
        )
        text_top = line_y + 0.55
        text_h = BODY_BOTTOM - text_top

        def tallest(size: float) -> list[tuple[str, float]]:
            worst: list[tuple[str, float]] = []
            worst_h = -1.0
            for step in model.steps:
                items = [(plain_text(step.title), size + 2)]
                if step.detail:
                    items.append((plain_text(step.detail), size))
                h = estimate_height_in(items, inner_w, 6)
                if h > worst_h:
                    worst, worst_h = items, h
            return worst

        size = self._fit(
            tallest, inner_w, text_h - 2 * pad, 18, 12, space_after=6, what="timeline text"
        )
        for number, step in enumerate(model.steps):
            x = MARGIN_X + number * col_w
            dot = self._oval(
                slide,
                x + col_w / 2 - 0.3,
                line_y - 0.3,
                0.6,
                c.accent,
                name=VISUAL_PREFIX + "step_dot",
            )
            self._style_frame(dot.text_frame, MSO_ANCHOR.MIDDLE, 0.0)
            self._write(
                dot.text_frame,
                [_Para(str(number + 1), 18, bold=True, color="#FFFFFF", align=PP_ALIGN.CENTER)],
            )
            paras = [
                _Para(
                    step.title,
                    size + 2,
                    bold=True,
                    color=c.heading,
                    heading_font=True,
                    space_after=6,
                    align=PP_ALIGN.CENTER,
                )
            ]
            if step.detail:
                paras.append(_Para(step.detail, size, color=c.text, align=PP_ALIGN.CENTER))
            self._textbox(slide, x + 0.05, text_top, col_w - 0.1, text_h, paras, inset=pad)

    def _layout_code(self, slide: Any, model: Slide) -> None:
        assert model.code is not None
        c = self.theme.colors
        self._content_header(slide, model.title)
        lines = model.code.text.split("\n")
        pad = 0.25
        label_h = 0.35 if model.code.language else 0.0
        panel_top, panel_h = BODY_TOP, BODY_H
        self._panel(
            slide,
            MARGIN_X,
            panel_top,
            CONTENT_W,
            panel_h,
            fill=c.code_bg,
            name=VISUAL_PREFIX + "code_panel",
        )
        if model.code.language:
            self._textbox(
                slide,
                MARGIN_X + CONTENT_W - 3.2,
                panel_top + 0.1,
                3.0,
                label_h,
                [_Para(model.code.language, 12, color=c.muted, align=PP_ALIGN.RIGHT)],
                inset=0.02,
            )
        size = self._fit(
            lambda s: [(line, s * CODE_WIDTH_FACTOR) for line in lines],
            CONTENT_W - 2 * pad,
            panel_h - 2 * pad - label_h,
            16,
            10,
            what="code",
        )
        paras = [_Para(line, size, color=c.code_text, mono=True, raw=True) for line in lines]
        self._textbox(
            slide,
            MARGIN_X,
            panel_top + label_h,
            CONTENT_W,
            panel_h - label_h,
            paras,
            inset=pad,
        )

    def _layout_image(self, slide: Any, model: Slide) -> None:
        assert model.image is not None
        c = self.theme.colors
        self._content_header(slide, model.title or "")
        caption_h = 0.5 if model.image.caption else 0.0
        box_h = BODY_H - caption_h
        placed = self._picture_fit(
            slide, model.image.src, model.image.alt, MARGIN_X, BODY_TOP, CONTENT_W, box_h
        )
        if not placed:
            self.warnings.append(
                f"Slide {self.index}: image '{model.image.src[:60]}' could not be loaded"
            )
            self._textbox(
                slide,
                MARGIN_X,
                BODY_TOP,
                CONTENT_W,
                box_h,
                [
                    _Para(
                        f"Image unavailable: {model.image.alt}",
                        18,
                        color=c.muted,
                        align=PP_ALIGN.CENTER,
                    )
                ],
                anchor=MSO_ANCHOR.MIDDLE,
            )
        if model.image.caption:
            self._textbox(
                slide,
                MARGIN_X,
                BODY_BOTTOM - caption_h + 0.05,
                CONTENT_W,
                caption_h,
                [_Para(model.image.caption, 14, color=c.muted, align=PP_ALIGN.CENTER)],
                inset=0.04,
            )


# --------------------------------------------------------------------------- public API
def create_deck(
    deck: Deck,
    output_path: Path | str,
    *,
    assets_root: Path | str | None = None,
) -> DeckRenderResult:
    """Render ``deck`` to a 16:9 .pptx using native shapes and the deck's design.

    Over-full slides are split first (see :mod:`app.automation.deck_checks`); every repair is
    returned in ``DeckRenderResult.repairs``.
    """
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    checked, split_notes = check_deck(deck)
    builder = _DeckBuilder(checked, make_image_resolver(assets_root))
    builder.build()
    builder.prs.save(str(path))
    return DeckRenderResult(
        path=path,
        renderer=RENDERER,
        theme=deck.theme,
        slide_count=len(builder.prs.slides),
        warnings=builder.warnings,
        repairs=[*deck.normalize_notes, *split_notes, *builder.repairs],
    )


def deck_from_legacy_slides(
    slides_data: list[dict[str, Any]],
    theme: str = "executive",
    title: str | None = None,
    *,
    lenient: bool = True,
    design: dict[str, Any] | None = None,
) -> Deck:
    """Map slide dicts (legacy title/cards/bullets/metrics or any new layout) to a :class:`Deck`.

    ``lenient`` (the legacy ``create_presentation`` behavior) fills a missing title with
    "Untitled" and an empty bullets slide with a placeholder instead of failing. The agent and
    the model-facing validation pass ``lenient=False`` so incomplete slides are reported.
    """
    slides: list[dict[str, Any]] = []
    for raw in slides_data:
        slide = dict(raw)
        slide["layout"] = str(slide.get("layout") or "bullets")
        if lenient:
            if (
                slide["layout"] not in ("image", "quote")
                and not str(slide.get("title", "")).strip()
            ):
                slide["title"] = "Untitled"
            if slide["layout"] == "bullets" and not slide.get("bullets"):
                slide["bullets"] = ["(no content)"]
        slides.append(slide)
    first = next((str(s.get("title")) for s in slides if s.get("title")), "Presentation")
    return deck_from_dict(
        {"theme": theme, "design": design or None, "title": title or first, "slides": slides}
    )


def create_presentation(
    slides_data: list[dict[str, Any]],
    output_path: Path | str,
    theme: str = "executive",
) -> Path:
    """Legacy entry point: generate a 16:9 PowerPoint (.pptx) from slide dicts.

    :param slides_data: List of slide dicts:
      - layout: any of ``model.SLIDE_LAYOUTS`` (legacy "title" and "metrics" still work)
      - title: Slide header string
      - subtitle: Optional subtitle string
      - bullets: List of strings (for bullets layout)
      - cards: List of dicts with 'title' and 'body' (for cards layout)
      - metrics: List of dicts with 'value', 'label' (for stat_callout layout)
    :param output_path: Destination path on disk.
    :param theme: Theme name (default ``executive``).
    :return: Resolved Path to generated .pptx file.
    """
    deck = deck_from_legacy_slides(slides_data, theme=theme)
    return create_deck(deck, output_path).path


__all__ = [
    "RENDERER",
    "DeckRenderResult",
    "DeckStyle",
    "SlideFitError",
    "create_deck",
    "create_presentation",
    "deck_from_legacy_slides",
]
