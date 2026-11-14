"""Draw :mod:`app.automation.deck_scene` primitives as native, editable python-pptx shapes.

Rounded rectangles carry their corner radius as the shape's own adjustment, fills may have alpha and
native gradients, shadows are ``a:effectLst/a:outerShdw`` (python-pptx has no API for them), icons
are custom-geometry freeforms (crisp, recolourable, editable) and charts are native charts. Text
boxes are not drawn here: the engine draws them so its fonts and overflow check apply.
"""

from __future__ import annotations

import io
from typing import Any

from lxml import etree
from pptx.chart.data import CategoryChartData
from pptx.dml.color import RGBColor
from pptx.enum.chart import XL_CHART_TYPE, XL_LEGEND_POSITION, XL_MARKER_STYLE
from pptx.enum.dml import MSO_LINE
from pptx.enum.shapes import MSO_CONNECTOR, MSO_SHAPE
from pptx.oxml.ns import qn
from pptx.util import Emu, Inches, Pt

from app.automation import deck_icons
from app.automation.deck_scene import Box, ChartShape, Gradient, IconShape, ImageShape, LineShape

_KINDS = {
    "rect": MSO_SHAPE.RECTANGLE,
    "round": MSO_SHAPE.ROUNDED_RECTANGLE,
    "oval": MSO_SHAPE.OVAL,
    "chevron": MSO_SHAPE.CHEVRON,
    "pentagon": MSO_SHAPE.PENTAGON,
}
# name -> (blur pt, distance pt, opacity); the glow shadow has no offset
_SHADOWS = {"soft": (18.0, 5.0, 0.24), "lift": (30.0, 10.0, 0.32), "glow": (26.0, 0.0, 0.42)}
_CHARTS = {
    "column": XL_CHART_TYPE.COLUMN_CLUSTERED,
    "bar": XL_CHART_TYPE.BAR_CLUSTERED,
    "line": XL_CHART_TYPE.LINE_MARKERS,
    "doughnut": XL_CHART_TYPE.DOUGHNUT,
}


def _in(value: float) -> Emu:
    return Emu(int(Inches(value)))


def _rgb(value: str) -> RGBColor:
    color: RGBColor = RGBColor.from_string(value.lstrip("#").upper())
    return color


def set_name(shape: Any, name: str) -> None:
    shape._element.xpath("./*[1]/p:cNvPr")[0].set("name", name)


def _alpha(color_parent: Any, opacity: float) -> None:
    """Add ``a:alpha`` to the colour element inside ``color_parent`` (a solidFill or a gs)."""
    if opacity >= 0.999 or not len(color_parent):
        return
    etree.SubElement(color_parent[0], qn("a:alpha")).set("val", str(int(round(opacity * 100000))))


def ooxml_angle(css_degrees: float) -> int:
    """OOXML ``a:lin`` angle (sixtieths of a degree, clockwise from left to right) for a CSS
    gradient angle (0 is bottom to top, 180 is top to bottom)."""
    return int(((css_degrees - 90.0) % 360.0) * 60000)


def write_gradient(grad_fill: Any, gradient: Gradient, opacity: float = 1.0) -> None:
    """Fill the ``a:gradFill`` element with a two-stop linear gradient."""
    for child in list(grad_fill):
        grad_fill.remove(child)
    grad_fill.set("rotWithShape", "1")
    stops = etree.SubElement(grad_fill, qn("a:gsLst"))
    for position, color in ((0, gradient.start), (100000, gradient.end)):
        stop = etree.SubElement(stops, qn("a:gs"), pos=str(position))
        etree.SubElement(stop, qn("a:srgbClr"), val=color.lstrip("#").upper())
        _alpha(stop, opacity)
    etree.SubElement(grad_fill, qn("a:lin"), ang=str(ooxml_angle(gradient.angle)), scaled="0")


def _shadow(shape: Any, name: str, glow: str | None) -> None:
    if name not in _SHADOWS:
        return
    blur, dist, opacity = _SHADOWS[name]
    spPr = shape._element.spPr
    for old in spPr.findall(qn("a:effectLst")):
        spPr.remove(old)
    effects = etree.SubElement(spPr, qn("a:effectLst"))
    shadow = etree.SubElement(
        effects,
        qn("a:outerShdw"),
        blurRad=str(int(Pt(blur))),
        dist=str(int(Pt(dist))),
        dir="5400000",
        algn="ctr" if name == "glow" else "t",
        rotWithShape="0",
    )
    color = etree.SubElement(
        shadow,
        qn("a:srgbClr"),
        val=(glow or "#000000").lstrip("#").upper() if name == "glow" else "000000",
    )
    etree.SubElement(color, qn("a:alpha"), val=str(int(opacity * 100000)))


def draw_box(slide: Any, box: Box) -> Any:
    kind = box.kind if box.kind in _KINDS else "rect"
    if kind == "round" and box.radius <= 0:
        kind = "rect"
    shape = slide.shapes.add_shape(_KINDS[kind], _in(box.x), _in(box.y), _in(box.w), _in(box.h))
    shape.shadow.inherit = False
    set_name(shape, box.name)
    if kind == "round":
        shape.adjustments[0] = min(0.5, box.radius / max(0.01, min(box.w, box.h)))
    elif kind in ("chevron", "pentagon"):
        shape.adjustments[0] = 0.32
    if box.gradient is not None:
        shape.fill.gradient()
        write_gradient(shape._element.spPr.find(qn("a:gradFill")), box.gradient, box.alpha)
    elif box.fill is not None:
        shape.fill.solid()
        shape.fill.fore_color.rgb = _rgb(box.fill)
        _alpha(shape._element.spPr.find(qn("a:solidFill")), box.alpha)
    else:
        shape.fill.background()
    if box.line is not None:
        shape.line.color.rgb = _rgb(box.line)
        shape.line.width = Pt(box.line_w)
        _alpha(shape._element.spPr.find(qn("a:ln")).find(qn("a:solidFill")), box.line_alpha)
    else:
        shape.line.fill.background()
    _shadow(shape, box.shadow, box.glow)
    return shape


def draw_line(slide: Any, line: LineShape) -> Any:
    connector = slide.shapes.add_connector(
        MSO_CONNECTOR.STRAIGHT, _in(line.x1), _in(line.y1), _in(line.x2), _in(line.y2)
    )
    set_name(connector, line.name)
    connector.line.color.rgb = _rgb(line.color)
    connector.line.width = Pt(line.width)
    if line.dash:
        connector.line.dash_style = MSO_LINE.DASH
    ln = connector._element.spPr.find(qn("a:ln"))
    ln.set("cap", "rnd")
    _alpha(ln.find(qn("a:solidFill")), line.alpha)
    return connector


def _custom_geometry(name: str) -> Any | None:
    segments = deck_icons.icon_segments(name)
    if segments is None:
        return None
    scale = 1000
    geom = etree.Element(qn("a:custGeom"))
    for tag in ("a:avLst", "a:gdLst", "a:ahLst", "a:cxnLst"):
        etree.SubElement(geom, qn(tag))
    etree.SubElement(geom, qn("a:rect"), l="0", t="0", r="r", b="b")
    paths = etree.SubElement(geom, qn("a:pathLst"))
    side = str(int(deck_icons.GRID * scale))
    path = etree.SubElement(paths, qn("a:path"), w=side, h=side, fill="none")

    def point(parent: Any, xy: tuple[float, float]) -> None:
        etree.SubElement(
            parent, qn("a:pt"), x=str(round(xy[0] * scale)), y=str(round(xy[1] * scale))
        )

    for seg in segments:
        if seg.op == "M":
            point(etree.SubElement(path, qn("a:moveTo")), seg.points[0])
        elif seg.op == "L":
            point(etree.SubElement(path, qn("a:lnTo")), seg.points[0])
        elif seg.op == "C":
            bez = etree.SubElement(path, qn("a:cubicBezTo"))
            for xy in seg.points:
                point(bez, xy)
        else:
            etree.SubElement(path, qn("a:close"))
    return geom


def draw_icon(slide: Any, icon: IconShape) -> Any | None:
    """A freeform icon: custom geometry, no fill, a round-capped stroke in ``icon.color``."""
    geom = _custom_geometry(icon.name)
    if geom is None:
        return None
    shape = slide.shapes.add_shape(
        MSO_SHAPE.RECTANGLE, _in(icon.x), _in(icon.y), _in(icon.size), _in(icon.size)
    )
    shape.shadow.inherit = False
    canonical = deck_icons.canonical_icon(icon.name) or icon.name
    set_name(shape, f"visual:icon-{canonical}")
    shape._element.xpath("./*[1]/p:cNvPr")[0].set("descr", f"{canonical} icon")
    spPr = shape._element.spPr
    prst = spPr.find(qn("a:prstGeom"))
    prst.addprevious(geom)
    spPr.remove(prst)
    shape.fill.background()
    shape.line.color.rgb = _rgb(icon.color)
    stroke_pt = max(1.0, icon.size * 72.0 * deck_icons.STROKE / deck_icons.GRID)
    shape.line.width = Pt(stroke_pt)
    ln = spPr.find(qn("a:ln"))
    ln.set("cap", "rnd")
    _alpha(ln.find(qn("a:solidFill")), icon.alpha)
    etree.SubElement(ln, qn("a:round"))
    return shape


def draw_image(slide: Any, image: ImageShape, blob: bytes, size: tuple[int, int]) -> Any:
    """A picture in ``image``'s box: stretched (backgrounds) or scaled to fit and centred."""
    x, y, w, h = image.x, image.y, image.w, image.h
    if image.fit:
        scale = min(w / size[0], h / size[1])
        pw, ph = size[0] * scale, size[1] * scale
        x, y, w, h = x + (w - pw) / 2, y + (h - ph) / 2, pw, ph
    picture = slide.shapes.add_picture(io.BytesIO(blob), _in(x), _in(y), _in(w), _in(h))
    set_name(picture, image.name)
    picture._element.nvPicPr.cNvPr.set("descr", image.alt)
    if image.radius > 0:
        picture.auto_shape_type = MSO_SHAPE.ROUNDED_RECTANGLE
        prst = picture._element.spPr.find(qn("a:prstGeom"))
        av = prst.find(qn("a:avLst"))
        if av is None:
            av = etree.SubElement(prst, qn("a:avLst"))
        adj = int(min(0.5, image.radius / max(0.01, min(w, h))) * 100000)
        etree.SubElement(av, qn("a:gd"), name="adj", fmla=f"val {adj}")
    return picture


def draw_chart(slide: Any, spec: ChartShape) -> Any:
    """A native chart drawn with the palette: transparent background, theme text and grid colours,
    series colours in order (doughnut slices take them per category)."""
    data = CategoryChartData()
    data.categories = list(spec.categories)
    for index, series in enumerate(spec.series, start=1):
        data.add_series(series.name or f"Series {index}", list(series.values))
    frame = slide.shapes.add_chart(
        _CHARTS[spec.kind], _in(spec.x), _in(spec.y), _in(spec.w), _in(spec.h), data
    )
    set_name(frame, spec.name)
    chart = frame.chart
    chart.has_title = False
    chart.font.size = Pt(spec.font_pt)
    chart.font.name = spec.font
    chart.font.color.rgb = _rgb(spec.text_color)
    chart_space = chart._chartSpace
    no_fill = etree.fromstring(
        '<c:spPr xmlns:c="http://schemas.openxmlformats.org/drawingml/2006/chart" '
        'xmlns:a="http://schemas.openxmlformats.org/drawingml/2006/main">'
        "<a:noFill/><a:ln><a:noFill/></a:ln></c:spPr>"
    )
    chart_space.find(qn("c:chart")).addnext(no_fill)
    many = len(spec.series) > 1
    chart.has_legend = many or spec.kind == "doughnut"
    if chart.has_legend:
        chart.legend.position = XL_LEGEND_POSITION.BOTTOM
        chart.legend.include_in_layout = False
        chart.legend.font.size = Pt(spec.font_pt)
        chart.legend.font.color.rgb = _rgb(spec.text_color)
    plot = chart.plots[0]
    if spec.kind == "doughnut":
        series = plot.series[0]
        for index in range(len(spec.categories)):
            point = series.points[index]
            fill = spec.colors[index % len(spec.colors)]
            point.format.fill.solid()
            point.format.fill.fore_color.rgb = _rgb(fill)
            point.format.line.color.rgb = _rgb(spec.grid_color)
            point.format.line.width = Pt(1)
        hole = chart_space.xpath(".//c:holeSize")
        if hole:
            hole[0].set("val", "62")
        return frame
    plot.vary_by_categories = False
    if spec.kind in ("column", "bar"):
        plot.gap_width = 70
    for index, series in enumerate(plot.series):
        color = spec.colors[index % len(spec.colors)]
        if spec.kind == "line":
            series.smooth = False
            series.format.line.color.rgb = _rgb(color)
            series.format.line.width = Pt(2.75)
            series.marker.style = XL_MARKER_STYLE.CIRCLE
            series.marker.size = 8
            series.marker.format.fill.solid()
            series.marker.format.fill.fore_color.rgb = _rgb(color)
            series.marker.format.line.color.rgb = _rgb(color)
        else:
            series.format.fill.solid()
            series.format.fill.fore_color.rgb = _rgb(color)
    value_axis = chart.value_axis
    value_axis.has_major_gridlines = True
    value_axis.major_gridlines.format.line.color.rgb = _rgb(spec.grid_color)
    value_axis.major_gridlines.format.line.width = Pt(0.75)
    value_axis.format.line.fill.background()
    value_axis.tick_labels.font.color.rgb = _rgb(spec.text_color)
    category_axis = chart.category_axis
    category_axis.format.line.color.rgb = _rgb(spec.grid_color)
    category_axis.has_major_gridlines = False
    category_axis.tick_labels.font.color.rgb = _rgb(spec.text_color)
    return frame


__all__ = [
    "draw_box",
    "draw_chart",
    "draw_icon",
    "draw_image",
    "draw_line",
    "ooxml_angle",
    "set_name",
    "write_gradient",
]
