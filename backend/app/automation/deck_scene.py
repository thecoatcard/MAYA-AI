"""A small, renderer-neutral scene for slides: shapes, text, icons, lines, charts and pictures.

The layouts in :mod:`app.automation.deck_layouts` and the helpers in
:mod:`app.automation.deck_graphics` build a ``list`` of these primitives once. The PPTX engine
turns them into native, editable shapes (rounded rectangles with alpha and soft shadows, gradient
fills, freeform icons, native charts); the HTML mirror turns the same list into CSS and SVG. So a
layout is written once and both renderers draw the same geometry.

All coordinates are inches on the 13.333 x 7.5 slide; font sizes are points. Primitives are plain
frozen dataclasses with no behaviour.
"""

from __future__ import annotations

from dataclasses import dataclass, field


class SceneFitError(ValueError):
    """Text cannot be sized to fit its area at the smallest allowed size (the renderer splits the
    slide, exactly as for :class:`app.automation.pptx_engine.SlideFitError`)."""


@dataclass(frozen=True)
class Para:
    text: str
    size: float
    color: str
    bold: bool = False
    italic: bool = False
    font: str = "body"  # body | heading | mono
    align: str = "left"  # left | center | right
    space_after: float = 0.0
    bullet: bool = False


@dataclass(frozen=True)
class Gradient:
    start: str
    end: str
    angle: float = 180.0  # CSS degrees: 180 is top to bottom, 90 is left to right


@dataclass(frozen=True)
class Box:
    """A filled and/or outlined shape. ``radius`` is the corner radius in inches (``round``)."""

    x: float
    y: float
    w: float
    h: float
    kind: str = "round"  # rect | round | oval | chevron | pentagon
    fill: str | None = None
    alpha: float = 1.0
    gradient: Gradient | None = None
    radius: float = 0.0
    line: str | None = None
    line_w: float = 0.75
    line_alpha: float = 1.0
    shadow: str = "none"  # none | soft | lift | glow
    glow: str | None = None  # glow colour when ``shadow`` is glow
    name: str = "visual:box"


@dataclass(frozen=True)
class TextBox:
    x: float
    y: float
    w: float
    h: float
    paras: tuple[Para, ...]
    anchor: str = "top"  # top | middle | bottom
    inset: float = 0.12
    name: str = "text"


@dataclass(frozen=True)
class IconShape:
    """A native freeform icon (see :mod:`app.automation.deck_icons`), stroked in ``color``."""

    name: str
    x: float
    y: float
    size: float
    color: str
    alpha: float = 1.0


@dataclass(frozen=True)
class LineShape:
    x1: float
    y1: float
    x2: float
    y2: float
    color: str
    width: float = 1.0
    alpha: float = 1.0
    dash: bool = False
    name: str = "visual:line"


@dataclass(frozen=True)
class Series:
    name: str
    values: tuple[float, ...]


@dataclass(frozen=True)
class ChartShape:
    kind: str  # bar | column | line | doughnut
    x: float
    y: float
    w: float
    h: float
    categories: tuple[str, ...]
    series: tuple[Series, ...]
    colors: tuple[str, ...]
    text_color: str
    grid_color: str
    font: str
    font_pt: float = 14.0
    name: str = "visual:chart"


@dataclass(frozen=True)
class ImageShape:
    """A picture: ``src`` (resolved by the renderer) or raw PNG ``data`` (procedural art)."""

    x: float
    y: float
    w: float
    h: float
    src: str = ""
    data: bytes = b""
    alt: str = ""
    radius: float = 0.0  # rounded corners in inches (0 = square)
    fit: bool = True  # scale to fit and centre (False = stretch to the box, for backgrounds)
    name: str = "visual:image"


Prim = Box | TextBox | IconShape | LineShape | ChartShape | ImageShape


@dataclass
class Scene:
    """The primitives of one slide body, in drawing order (first is bottom-most)."""

    prims: list[Prim] = field(default_factory=list)

    def add(self, *items: Prim | list[Prim] | tuple[Prim, ...]) -> Scene:
        for item in items:
            if isinstance(item, list | tuple):
                self.prims.extend(item)
            else:
                self.prims.append(item)
        return self


__all__ = [
    "Box",
    "ChartShape",
    "Gradient",
    "IconShape",
    "ImageShape",
    "LineShape",
    "Para",
    "Prim",
    "Scene",
    "SceneFitError",
    "Series",
    "TextBox",
]
