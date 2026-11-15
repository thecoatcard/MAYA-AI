"""Small chart renderer: bar, line and pie charts as PNG bytes, drawn with Pillow only.

There is no plotting library in this project (matplotlib is not a dependency), so the three chart
kinds are drawn directly. The rules it follows: a title, labelled axes with round tick values and
light grid lines, readable text sizes, a legend only when there is more than one series (a single
series is named by the title and the axis label), and the Okabe-Ito palette, which stays
distinguishable under the common forms of colour blindness. A pie is labelled on its slices
(category and share), so it needs no legend and does not depend on colour alone.

The output is deterministic for the same input. Nothing here touches the network or the disk.
Sending a chart to Notion is a separate matter: see ``UPLOAD_UNAVAILABLE`` in
``app/mcp/notion_write.py`` (file upload is not implemented; an image block takes a public URL).
"""

from __future__ import annotations

import io
import math
from collections.abc import Sequence
from dataclasses import dataclass
from typing import Any, Final, Literal

from PIL import Image, ImageDraw, ImageFont

ChartKind = Literal["bar", "line", "pie"]
CHART_KINDS: Final = ("bar", "line", "pie")

# Okabe-Ito, ordered so neighbours differ in lightness as well as hue. Yellow is left out: it is
# too light on a white background.
PALETTE: Final = ("#0072B2", "#E69F00", "#009E73", "#CC79A7", "#56B4E9", "#D55E00", "#000000")
MAX_SERIES: Final = len(PALETTE)
MAX_CATEGORIES: Final = 40
MAX_PIE_SLICES: Final = 12
MIN_SIZE: Final = (480, 320)
MAX_SIZE: Final = (2400, 1800)
_SCALE: Final = 2  # drawn at twice the size and reduced, which smooths lines and arcs
_INK: Final = "#1F2328"
_MUTED: Final = "#57606A"
_GRID: Final = "#D8DEE4"
_BACKGROUND: Final = "#FFFFFF"


class ChartError(ValueError):
    """The chart cannot be drawn from this input. ``str(error)`` is a plain sentence."""


@dataclass(frozen=True, slots=True)
class Series:
    """One named row of numbers, one value per category."""

    name: str
    values: Sequence[float]


def _font(size: int) -> Any:
    try:
        return ImageFont.load_default(size=size)
    except (TypeError, OSError):  # a Pillow built without FreeType: the fixed bitmap font
        return ImageFont.load_default()


def _width(draw: ImageDraw.ImageDraw, text: str, font: Any) -> int:
    return int(math.ceil(draw.textlength(text, font=font)))


def _fit(draw: ImageDraw.ImageDraw, text: str, font: Any, limit: int) -> str:
    """``text``, shortened with an ellipsis until it is at most ``limit`` pixels wide."""
    if _width(draw, text, font) <= limit:
        return text
    cut = text
    while len(cut) > 1 and _width(draw, cut + "...", font) > limit:
        cut = cut[:-1]
    return cut + "..."


def nice_ticks(low: float, high: float, target: int = 5) -> list[float]:
    """Round tick values (steps of 1, 2 or 5 times a power of ten) that cover ``low``..``high``."""
    if high <= low:
        high = low + 1.0
    raw = (high - low) / max(1, target)
    power = 10.0 ** math.floor(math.log10(raw))
    step = next(m * power for m in (1.0, 2.0, 5.0, 10.0) if m * power >= raw)
    first = math.floor(low / step) * step
    ticks: list[float] = []
    value = first
    while value < high + step * 0.5 and len(ticks) < 50:
        ticks.append(round(value, 10))
        value += step
    if ticks[-1] < high:
        ticks.append(round(ticks[-1] + step, 10))
    return ticks


def format_number(value: float) -> str:
    """A tick or value label: no trailing zeros, thousands separated, large values shortened."""
    magnitude = abs(value)
    for limit, suffix in ((1e9, "B"), (1e6, "M")):
        if magnitude >= limit:
            return f"{value / limit:.2f}".rstrip("0").rstrip(".") + suffix
    if float(value).is_integer():
        return f"{int(value):,}"
    return f"{value:,.2f}".rstrip("0").rstrip(".")


def _validated(
    kind: str, categories: Sequence[str], series: Sequence[Series]
) -> tuple[list[str], list[tuple[str, list[float]]]]:
    if kind not in CHART_KINDS:
        raise ChartError(f"Unknown chart kind '{kind}'. Use bar, line or pie.")
    names = [str(c).strip() or "(blank)" for c in categories]
    if not names:
        raise ChartError("The chart needs at least one category.")
    if len(names) > MAX_CATEGORIES:
        raise ChartError(f"The chart can show at most {MAX_CATEGORIES} categories.")
    if not series:
        raise ChartError("The chart needs at least one series of numbers.")
    if len(series) > MAX_SERIES:
        raise ChartError(f"The chart can show at most {MAX_SERIES} series.")
    rows: list[tuple[str, list[float]]] = []
    for item in series:
        try:
            values = [float(v) for v in item.values]
        except (TypeError, ValueError):
            raise ChartError(
                f"The series '{item.name}' has a value that is not a number."
            ) from None
        if len(values) != len(names):
            raise ChartError(
                f"The series '{item.name}' has {len(values)} values for {len(names)} categories."
            )
        if not all(math.isfinite(v) for v in values):
            raise ChartError(f"The series '{item.name}' has a value that is not a finite number.")
        rows.append((str(item.name).strip() or "Series", values))
    if kind == "pie":
        if len(rows) != 1:
            raise ChartError("A pie chart shows exactly one series.")
        if len(names) > MAX_PIE_SLICES:
            raise ChartError(f"A pie chart can show at most {MAX_PIE_SLICES} slices.")
        if any(v < 0 for v in rows[0][1]) or sum(rows[0][1]) <= 0:
            raise ChartError(
                "A pie chart needs values that are zero or more, with a total above 0."
            )
    return names, rows


class _Canvas:
    """The drawing surface at ``_SCALE`` times the requested size, with the shared text sizes."""

    def __init__(self, width: int, height: int) -> None:
        self.w, self.h = width * _SCALE, height * _SCALE
        self.image = Image.new("RGB", (self.w, self.h), _BACKGROUND)
        self.draw = ImageDraw.Draw(self.image)
        base = max(11, min(width, height) // 34)
        self.title_font = _font(int(base * 1.5) * _SCALE)
        self.label_font = _font(base * _SCALE)
        self.tick_font = _font(int(base * 0.9) * _SCALE)
        self.pad = base * _SCALE
        self.line = max(2, base // 6) * _SCALE

    def text_height(self, font: Any) -> int:
        box = self.draw.textbbox((0, 0), "Ag", font=font)
        return int(box[3] - box[1])

    def vertical_text(self, text: str, x: int, top: int, bottom: int) -> None:
        """``text`` rotated to read bottom-to-top, centred between ``top`` and ``bottom``."""
        width = _width(self.draw, text, self.label_font)
        height = self.text_height(self.label_font) + self.pad // 2
        tile = Image.new("RGB", (max(1, width), height), _BACKGROUND)
        ImageDraw.Draw(tile).text((0, 0), text, font=self.label_font, fill=_MUTED)
        turned = tile.rotate(90, expand=True)
        self.image.paste(turned, (x, top + max(0, (bottom - top - turned.height) // 2)))

    def png(self, width: int, height: int) -> bytes:
        final = self.image.resize((width, height), Image.Resampling.LANCZOS)
        out = io.BytesIO()
        final.save(out, format="PNG", optimize=True)
        return out.getvalue()


def _draw_title(canvas: _Canvas, title: str) -> int:
    """Draw the title and return the y coordinate below it."""
    if not title.strip():
        return canvas.pad
    text = _fit(canvas.draw, title.strip(), canvas.title_font, canvas.w - 2 * canvas.pad)
    canvas.draw.text((canvas.pad, canvas.pad), text, font=canvas.title_font, fill=_INK)
    return canvas.pad + canvas.text_height(canvas.title_font) + canvas.pad


def _draw_legend(canvas: _Canvas, names: list[str], top: int) -> int:
    """A one-row legend (colour swatch plus name) for several series; returns the y below it."""
    x = canvas.pad
    swatch = canvas.text_height(canvas.label_font)
    limit = (canvas.w - 2 * canvas.pad) // max(1, len(names)) - swatch - canvas.pad
    for index, name in enumerate(names):
        colour = PALETTE[index % len(PALETTE)]
        canvas.draw.rectangle((x, top, x + swatch, top + swatch), fill=colour)
        label = _fit(canvas.draw, name, canvas.label_font, max(swatch, limit))
        canvas.draw.text(
            (x + swatch + canvas.pad // 2, top), label, font=canvas.label_font, fill=_INK
        )
        x += swatch + canvas.pad // 2 + _width(canvas.draw, label, canvas.label_font) + canvas.pad
    return top + swatch + canvas.pad


def _draw_axes(
    canvas: _Canvas,
    names: list[str],
    rows: list[tuple[str, list[float]]],
    top: int,
    x_label: str,
    y_label: str,
    *,
    from_zero: bool,
) -> tuple[tuple[int, int, int, int], list[float]]:
    """Grid, tick labels, axis lines and axis titles. Returns the plot box and the tick values."""
    values = [v for _name, row in rows for v in row]
    low, high = min(values), max(values)
    if from_zero or low >= 0:
        low = min(0.0, low)
    if from_zero and high < 0:
        high = 0.0
    ticks = nice_ticks(low, high)
    labels = [format_number(t) for t in ticks]
    tick_width = max(_width(canvas.draw, label, canvas.tick_font) for label in labels)
    label_height = canvas.text_height(canvas.label_font)
    tick_height = canvas.text_height(canvas.tick_font)
    left = canvas.pad + (label_height + canvas.pad if y_label.strip() else 0) + tick_width
    left += canvas.pad // 2
    bottom = canvas.h - canvas.pad - tick_height - canvas.pad // 2
    if x_label.strip():
        bottom -= label_height + canvas.pad // 2
    right = canvas.w - canvas.pad
    span = ticks[-1] - ticks[0]
    for tick, label in zip(ticks, labels, strict=True):
        y = bottom - int((tick - ticks[0]) / span * (bottom - top))
        canvas.draw.line((left, y, right, y), fill=_GRID, width=max(1, _SCALE))
        canvas.draw.text(
            (
                left - canvas.pad // 2 - _width(canvas.draw, label, canvas.tick_font),
                y - tick_height // 2,
            ),
            label,
            font=canvas.tick_font,
            fill=_MUTED,
        )
    canvas.draw.line((left, top, left, bottom), fill=_MUTED, width=_SCALE)
    zero_y = (
        bottom - int((0 - ticks[0]) / span * (bottom - top))
        if ticks[0] <= 0 <= ticks[-1]
        else bottom
    )
    canvas.draw.line((left, zero_y, right, zero_y), fill=_MUTED, width=_SCALE)
    slot = (right - left) / len(names)
    every = max(1, math.ceil(len(names) * (tick_height * 3) / max(1, right - left)))
    for index, name in enumerate(names):
        if index % every:
            continue
        text = _fit(
            canvas.draw, name, canvas.tick_font, max(tick_height, int(slot * every) - _SCALE * 4)
        )
        centre = left + slot * (index + 0.5)
        canvas.draw.text(
            (
                int(centre - _width(canvas.draw, text, canvas.tick_font) / 2),
                bottom + canvas.pad // 2,
            ),
            text,
            font=canvas.tick_font,
            fill=_MUTED,
        )
    if x_label.strip():
        text = _fit(canvas.draw, x_label.strip(), canvas.label_font, right - left)
        canvas.draw.text(
            (
                left + (right - left - _width(canvas.draw, text, canvas.label_font)) // 2,
                canvas.h - canvas.pad - label_height,
            ),
            text,
            font=canvas.label_font,
            fill=_MUTED,
        )
    if y_label.strip():
        text = _fit(canvas.draw, y_label.strip(), canvas.label_font, bottom - top)
        canvas.vertical_text(text, canvas.pad // 2, top, bottom)
    return (left, top, right, bottom), ticks


def _draw_bars(
    canvas: _Canvas,
    box: tuple[int, int, int, int],
    ticks: list[float],
    rows: list[tuple[str, list[float]]],
) -> None:
    left, top, right, bottom = box
    span = ticks[-1] - ticks[0]
    count = len(rows[0][1])
    slot = (right - left) / count
    group = slot * 0.72
    bar = group / len(rows)
    zero = bottom - (0 - ticks[0]) / span * (bottom - top)
    for series_index, (_name, values) in enumerate(rows):
        colour = PALETTE[series_index % len(PALETTE)]
        for index, value in enumerate(values):
            x0 = left + slot * index + (slot - group) / 2 + bar * series_index
            y = bottom - (value - ticks[0]) / span * (bottom - top)
            gap = _SCALE if len(rows) > 1 else 0
            canvas.draw.rectangle(
                (int(x0) + gap, int(min(y, zero)), int(x0 + bar) - gap, int(max(y, zero))),
                fill=colour,
            )


def _draw_lines(
    canvas: _Canvas,
    box: tuple[int, int, int, int],
    ticks: list[float],
    rows: list[tuple[str, list[float]]],
) -> None:
    left, top, right, bottom = box
    span = ticks[-1] - ticks[0]
    count = len(rows[0][1])
    slot = (right - left) / count
    radius = canvas.line * 1.6
    for series_index, (_name, values) in enumerate(rows):
        colour = PALETTE[series_index % len(PALETTE)]
        points = [
            (left + slot * (index + 0.5), bottom - (value - ticks[0]) / span * (bottom - top))
            for index, value in enumerate(values)
        ]
        if len(points) > 1:
            canvas.draw.line(points, fill=colour, width=canvas.line, joint="curve")
        for x, y in points:
            canvas.draw.ellipse((x - radius, y - radius, x + radius, y + radius), fill=colour)


def _draw_pie(canvas: _Canvas, names: list[str], values: list[float], top: int) -> None:
    total = sum(values)
    label_room = canvas.w // 4
    radius = min((canvas.w - 2 * label_room) // 2, (canvas.h - top - 3 * canvas.pad) // 2)
    cx, cy = canvas.w // 2, top + (canvas.h - top) // 2
    box = (cx - radius, cy - radius, cx + radius, cy + radius)
    start = -90.0
    height = canvas.text_height(canvas.tick_font)
    for index, (name, value) in enumerate(zip(names, values, strict=True)):
        if value <= 0:
            continue
        sweep = value / total * 360.0
        colour = PALETTE[index % len(PALETTE)]
        canvas.draw.pieslice(
            box, start, start + sweep, fill=colour, outline=_BACKGROUND, width=_SCALE * 2
        )
        middle = math.radians(start + sweep / 2)
        edge = (cx + math.cos(middle) * radius, cy + math.sin(middle) * radius)
        out = (
            cx + math.cos(middle) * (radius + canvas.pad),
            cy + math.sin(middle) * (radius + canvas.pad),
        )
        canvas.draw.line((edge, out), fill=_MUTED, width=_SCALE)
        share = f"{value / total * 100:.1f}".rstrip("0").rstrip(".")
        on_right = math.cos(middle) >= 0
        room = int(canvas.w - out[0] - canvas.pad) if on_right else int(out[0] - canvas.pad)
        label = _fit(
            canvas.draw, f"{name} ({share}%)", canvas.tick_font, max(height, room - canvas.pad // 2)
        )
        x = (
            out[0] + canvas.pad // 3
            if on_right
            else out[0] - canvas.pad // 3 - _width(canvas.draw, label, canvas.tick_font)
        )
        canvas.draw.text(
            (int(x), int(out[1] - height / 2)), label, font=canvas.tick_font, fill=_INK
        )
        start += sweep


def render_chart(
    kind: ChartKind,
    *,
    title: str,
    categories: Sequence[str],
    series: Sequence[Series],
    x_label: str = "",
    y_label: str = "",
    width: int = 960,
    height: int = 600,
) -> bytes:
    """A bar, line or pie chart as PNG bytes. Raises :class:`ChartError` for input it cannot draw.

    ``categories`` label the x axis (or the pie slices); every series has one value per category.
    Bars are grouped per category and start at zero. A pie takes exactly one series.
    """
    names, rows = _validated(kind, categories, series)
    if not (MIN_SIZE[0] <= width <= MAX_SIZE[0] and MIN_SIZE[1] <= height <= MAX_SIZE[1]):
        raise ChartError(
            f"The chart size must be between {MIN_SIZE[0]}x{MIN_SIZE[1]} and "
            f"{MAX_SIZE[0]}x{MAX_SIZE[1]} pixels."
        )
    canvas = _Canvas(width, height)
    top = _draw_title(canvas, title)
    if kind == "pie":
        _draw_pie(canvas, names, rows[0][1], top)
        return canvas.png(width, height)
    if len(rows) > 1:
        top = _draw_legend(canvas, [name for name, _values in rows], top)
    box, ticks = _draw_axes(canvas, names, rows, top, x_label, y_label, from_zero=kind == "bar")
    if kind == "bar":
        _draw_bars(canvas, box, ticks, rows)
    else:
        _draw_lines(canvas, box, ticks, rows)
    return canvas.png(width, height)


__all__ = [
    "CHART_KINDS",
    "MAX_CATEGORIES",
    "MAX_SERIES",
    "PALETTE",
    "ChartError",
    "ChartKind",
    "Series",
    "format_number",
    "nice_ticks",
    "render_chart",
]
