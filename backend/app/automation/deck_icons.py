"""Native vector icons for slides.

Each icon is SVG path data on a 24 x 24 grid in the style of the Lucide icon set (stroked,
round caps and joins, no fill). :func:`parse_path` turns the path data (``M L H V C S Q A Z`` and
their relative forms) into contours of absolute points and cubic Bezier segments; arcs are
approximated by cubic Beziers. The PPTX renderer writes the contours as custom geometry (a native,
recolourable, editable freeform shape) and the HTML mirror writes the same data as an SVG path.

An unknown icon name is not an error here: :func:`has_icon` is ``False`` and the caller draws a
numbered badge instead.

Attribution: the geometry is hand-converted from, or drawn in the style of, the Lucide icons
(https://lucide.dev, ISC licence, with parts derived from Feather icons under the MIT licence).
See ``backend/THIRD_PARTY_NOTICES.md``.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass

GRID = 24.0
STROKE = 2.0  # Lucide's stroke width on the 24 grid

Point = tuple[float, float]


@dataclass(frozen=True)
class Segment:
    """One drawing step in absolute grid coordinates.

    ``op`` is ``M`` (move), ``L`` (line), ``C`` (cubic; ``points`` holds the two controls and the
    end) or ``Z`` (close).
    """

    op: str
    points: tuple[Point, ...] = ()


def _circle(cx: float, cy: float, r: float) -> str:
    return f"M{cx - r:g} {cy:g}a{r:g} {r:g} 0 1 0 {2 * r:g} 0a{r:g} {r:g} 0 1 0 {-2 * r:g} 0z"


def _rect(x: float, y: float, w: float, h: float, r: float) -> str:
    return (
        f"M{x + r:g} {y:g}h{w - 2 * r:g}a{r:g} {r:g} 0 0 1 {r:g} {r:g}v{h - 2 * r:g}"
        f"a{r:g} {r:g} 0 0 1 {-r:g} {r:g}h{-(w - 2 * r):g}a{r:g} {r:g} 0 0 1 {-r:g} {-r:g}"
        f"v{-(h - 2 * r):g}a{r:g} {r:g} 0 0 1 {r:g} {-r:g}z"
    )


# name -> tuple of path strings (each its own open or closed contour group).
_ICONS: dict[str, tuple[str, ...]] = {
    "check": ("M20 6 9 17l-5-5",),
    "x": ("M18 6 6 18", "m6 6 12 12"),
    "plus": ("M5 12h14", "M12 5v14"),
    "arrow-right": ("M5 12h14", "m12 5 7 7-7 7"),
    "trending-up": ("M22 7 13.5 15.5 8.5 10.5 2 17", "M16 7h6v6"),
    "trending-down": ("M22 17 13.5 8.5 8.5 13.5 2 7", "M16 17h6v-6"),
    "zap": (
        "M4 14a1 1 0 0 1-.78-1.63l9.9-10.2a.5.5 0 0 1 .86.46l-1.92 6.02A1 1 0 0 0 13 10h7"
        "a1 1 0 0 1 .78 1.63l-9.9 10.2a.5.5 0 0 1-.86-.46l1.92-6.02A1 1 0 0 0 11 14z",
    ),
    "heart": (
        "M19 14c1.49-1.46 3-3.21 3-5.5A5.5 5.5 0 0 0 16.5 3c-1.76 0-3 .5-4.5 2-1.5-1.5-2.74-2"
        "-4.5-2A5.5 5.5 0 0 0 2 8.5c0 2.3 1.5 4.05 3 5.5l7 7Z",
    ),
    "star": (
        "M11.525 2.295a.53.53 0 0 1 .95 0l2.31 4.679a2.123 2.123 0 0 0 1.595 1.16l5.166.756"
        "a.53.53 0 0 1 .294.904l-3.736 3.638a2.123 2.123 0 0 0-.611 1.878l.882 5.14a.53.53 0 0 1"
        "-.771.56l-4.618-2.428a2.122 2.122 0 0 0-1.973 0L6.396 21.01a.53.53 0 0 1-.77-.56l.881"
        "-5.139a2.122 2.122 0 0 0-.611-1.879L2.16 9.795a.53.53 0 0 1 .294-.906l5.165-.755"
        "a2.122 2.122 0 0 0 1.597-1.16z",
    ),
    "home": ("m3 9 9-7 9 7v11a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z", "M9 22V12h6v10"),
    "user": ("M19 21v-2a4 4 0 0 0-4-4H9a4 4 0 0 0-4 4v2", _circle(12, 7, 4)),
    "users": (
        "M16 21v-2a4 4 0 0 0-4-4H6a4 4 0 0 0-4 4v2",
        _circle(9, 7, 4),
        "M22 21v-2a4 4 0 0 0-3-3.87",
        "M16 3.13a4 4 0 0 1 0 7.75",
    ),
    "clock": (_circle(12, 12, 10), "M12 6v6l4 2"),
    "calendar": (_rect(3, 4, 18, 18, 2), "M16 2v4", "M8 2v4", "M3 10h18"),
    "mail": (_rect(2, 4, 20, 16, 2), "m22 7-8.97 5.7a1.94 1.94 0 0 1-2.06 0L2 7"),
    "lock": (_rect(3, 11, 18, 11, 2), "M7 11V7a5 5 0 0 1 10 0v4"),
    "shield": (
        "M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1"
        "c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z",
    ),
    "shield-check": (
        "M20 13c0 5-3.5 7.5-7.66 8.95a1 1 0 0 1-.67-.01C7.5 20.5 4 18 4 13V6a1 1 0 0 1 1-1"
        "c2 0 4.5-1.2 6.24-2.72a1.17 1.17 0 0 1 1.52 0C14.51 3.81 17 5 19 5a1 1 0 0 1 1 1z",
        "m9 12 2 2 4-4",
    ),
    "globe": (
        _circle(12, 12, 10),
        "M12 2a14.5 14.5 0 0 0 0 20 14.5 14.5 0 0 0 0-20",
        "M2 12h20",
    ),
    "map-pin": (
        "M20 10c0 4.993-5.539 10.193-7.399 11.799a1 1 0 0 1-1.202 0C9.539 20.193 4 14.993 4 10"
        "a8 8 0 0 1 16 0",
        _circle(12, 10, 3),
    ),
    "search": (_circle(11, 11, 8), "m21 21-4.3-4.3"),
    "sliders": (
        "M4 21v-7",
        "M4 10V3",
        "M12 21v-9",
        "M12 8V3",
        "M20 21v-5",
        "M20 12V3",
        "M2 14h4",
        "M10 8h4",
        "M18 16h4",
    ),
    "bell": ("M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9", "M10.3 21a1.94 1.94 0 0 0 3.4 0"),
    "bookmark": ("m19 21-7-4-7 4V5a2 2 0 0 1 2-2h10a2 2 0 0 1 2 2v16z",),
    "flag": (
        "M4 15s1-1 4-1 5 2 8 2 4-1 4-1V3s-1 1-4 1-5-2-8-2-4 1-4 1z",
        "M4 22v-7",
    ),
    "target": (_circle(12, 12, 10), _circle(12, 12, 6), _circle(12, 12, 2)),
    "rocket": (
        "M4.5 16.5c-1.5 1.26-2 5-2 5s3.74-.5 5-2c.71-.84.7-2.13-.09-2.91a2.18 2.18 0 0 0-2.91-.09z",
        "m12 15-3-3a22 22 0 0 1 2-3.95A12.88 12.88 0 0 1 22 2c0 2.72-.78 7.5-6 11a22.35 22.35"
        " 0 0 1-4 2z",
        "M9 12H4s.55-3.03 2-4c1.62-1.08 5 0 5 0",
        "M12 15v5s3.03-.55 4-2c1.08-1.62 0-5 0-5",
    ),
    "lightbulb": (
        "M15 14c.2-1 .7-1.7 1.5-2.5 1-.9 1.5-2.2 1.5-3.5A6 6 0 0 0 6 8c0 1 .2 2.2 1.5 3.5"
        ".7.7 1.3 1.5 1.5 2.5",
        "M9 18h6",
        "M10 22h4",
    ),
    "trophy": (
        "M6 9H4.5a2.5 2.5 0 0 1 0-5H6",
        "M18 9h1.5a2.5 2.5 0 0 0 0-5H18",
        "M4 22h16",
        "M10 14.66V17c0 .55-.47.98-1.04 1.21C7.85 18.75 7 20.24 7 22",
        "M14 14.66V17c0 .55.47.98 1.04 1.21C16.15 18.75 17 20.24 17 22",
        "M18 2H6v7a6 6 0 0 0 12 0V2Z",
    ),
    "bar-chart": ("M3 3v16a2 2 0 0 0 2 2h16", "M18 17V9", "M13 17V5", "M8 17v-3"),
    "pie-chart": ("M21.21 15.89A10 10 0 1 1 8 2.83", "M22 12A10 10 0 0 0 12 2v10z"),
    "dollar-sign": ("M12 2v20", "M17 5H9.5a3.5 3.5 0 0 0 0 7h5a3.5 3.5 0 0 1 0 7H6"),
    "cloud": ("M17.5 19H9a7 7 0 1 1 6.71-9h1.79a4.5 4.5 0 1 1 0 9Z",),
    "database": (
        "M3 5a9 3 0 0 0 18 0a9 3 0 0 0-18 0z",
        "M3 5v14a9 3 0 0 0 18 0V5",
        "M3 12a9 3 0 0 0 18 0",
    ),
    "code": ("m16 18 6-6-6-6", "m8 6-6 6 6 6"),
    "layers": (
        "m12.83 2.18a2 2 0 0 0-1.66 0L2.6 6.08a1 1 0 0 0 0 1.83l8.58 3.91a2 2 0 0 0 1.66 0"
        "l8.58-3.9a1 1 0 0 0 0-1.83Z",
        "m22 17.65-9.17 4.16a2 2 0 0 1-1.66 0L2 17.65",
        "m22 12.65-9.17 4.16a2 2 0 0 1-1.66 0L2 12.65",
    ),
    "book-open": (
        "M12 7v14",
        "M3 18a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1h5a4 4 0 0 1 4 4 4 4 0 0 1 4-4h5a1 1 0 0 1 1 1v13"
        "a1 1 0 0 1-1 1h-6a3 3 0 0 0-3 3 3 3 0 0 0-3-3z",
    ),
    "message-circle": ("M7.9 20A9 9 0 1 0 4 16.1L2 22Z",),
    "sun": (
        _circle(12, 12, 4),
        "M12 2v2",
        "M12 20v2",
        "m4.93 4.93 1.41 1.41",
        "m17.66 17.66 1.41 1.41",
        "M2 12h2",
        "M20 12h2",
        "m6.34 17.66-1.41 1.41",
        "m19.07 4.93-1.41 1.41",
    ),
    "leaf": (
        "M11 20A7 7 0 0 1 9.8 6.1C15.5 5 17 4.48 19 2c1 2 2 4.18 2 8 0 5.5-4.78 10-10 10Z",
        "M2 21c0-3 1.85-5.36 5.08-6C9.5 14.52 12 13 13 12",
    ),
    "eye": (
        "M2.062 12.348a1 1 0 0 1 0-.696 10.75 10.75 0 0 1 19.876 0 1 1 0 0 1 0 .696 10.75 10.75"
        " 0 0 1-19.876 0",
        _circle(12, 12, 3),
    ),
    "link": (
        "M10 13a5 5 0 0 0 7.54.54l3-3a5 5 0 0 0-7.07-7.07l-1.72 1.71",
        "M14 11a5 5 0 0 0-7.54-.54l-3 3a5 5 0 0 0 7.07 7.07l1.71-1.71",
    ),
    "cpu": (
        _rect(4, 4, 16, 16, 2),
        _rect(9, 9, 6, 6, 1),
        "M15 2v2",
        "M15 20v2",
        "M2 15h2",
        "M2 9h2",
        "M20 15h2",
        "M20 9h2",
        "M9 2v2",
        "M9 20v2",
    ),
    "smartphone": (_rect(5, 2, 14, 20, 2), "M12 18h.01"),
    "graduation-cap": (
        "M21.42 10.922a1 1 0 0 0-.019-1.838L12.83 5.18a2 2 0 0 0-1.66 0L2.6 9.08a1 1 0 0 0 0 1.832"
        "l8.57 3.908a2 2 0 0 0 1.66 0z",
        "M22 10v6",
        "M6 12.5V16a6 3 0 0 0 12 0v-3.5",
    ),
    "briefcase": (
        _rect(2, 7, 20, 14, 2),
        "M16 21V5a2 2 0 0 0-2-2h-4a2 2 0 0 0-2 2v16",
    ),
    "award": (
        _circle(12, 8, 6),
        "m15.477 12.89 1.515 8.526a.5.5 0 0 1-.81.47l-3.58-2.687a1 1 0 0 0-1.197 0l-3.586 2.686"
        "a.5.5 0 0 1-.81-.469l1.514-8.526",
    ),
}

ICON_NAMES: tuple[str, ...] = tuple(_ICONS)

_ALIASES: dict[str, str] = {
    "rocket-launch": "rocket",
    "idea": "lightbulb",
    "light-bulb": "lightbulb",
    "bulb": "lightbulb",
    "growth": "trending-up",
    "increase": "trending-up",
    "decline": "trending-down",
    "security": "shield",
    "safe": "shield-check",
    "money": "dollar-sign",
    "finance": "dollar-sign",
    "cost": "dollar-sign",
    "chart": "bar-chart",
    "analytics": "bar-chart",
    "stats": "bar-chart",
    "team": "users",
    "people": "users",
    "customer": "user",
    "person": "user",
    "time": "clock",
    "schedule": "calendar",
    "email": "mail",
    "message": "message-circle",
    "chat": "message-circle",
    "world": "globe",
    "location": "map-pin",
    "pin": "map-pin",
    "goal": "target",
    "win": "trophy",
    "speed": "zap",
    "fast": "zap",
    "energy": "zap",
    "storage": "database",
    "data": "database",
    "tech": "cpu",
    "chip": "cpu",
    "mobile": "smartphone",
    "phone": "smartphone",
    "learn": "graduation-cap",
    "education": "graduation-cap",
    "book": "book-open",
    "business": "briefcase",
    "quality": "award",
    "nature": "leaf",
    "green": "leaf",
    "view": "eye",
    "vision": "eye",
    "tick": "check",
    "done": "check",
    "close": "x",
    "add": "plus",
    "next": "arrow-right",
    "settings": "sliders",
    "like": "heart",
    "favorite": "star",
    "house": "home",
}


def canonical_icon(name: str) -> str | None:
    """The canonical icon name for ``name`` (case, ``_``/space and common aliases folded), or
    ``None`` when there is no such icon."""
    key = re.sub(r"[\s_]+", "-", (name or "").strip().lower())
    key = _ALIASES.get(key, key)
    return key if key in _ICONS else None


def has_icon(name: str) -> bool:
    return canonical_icon(name) is not None


# ----------------------------------------------------------------------------- path parser
_TOKEN = re.compile(r"[MmLlHhVvCcSsQqAaZz]|[-+]?(?:\d+\.?\d*|\.\d+)(?:[eE][-+]?\d+)?")
_ARITY = {"M": 2, "L": 2, "H": 1, "V": 1, "C": 6, "S": 4, "Q": 4, "A": 7, "Z": 0}


def _numbers_for_arc(tokens: list[str], index: int) -> tuple[list[float], int]:
    """Seven arc parameters. The two flags may be written without a separator ("a1 1 0 011 1"),
    so a flag token is split into single digits."""
    values: list[float] = []
    while len(values) < 7 and index < len(tokens):
        token = tokens[index]
        position = len(values)
        if position in (3, 4) and len(token) > 1 and token[0] in "01" and token[1] in "01.":
            values.append(float(token[0]))
            tokens[index] = token[1:]
            continue
        values.append(float(token))
        index += 1
    return values, index


def _arc_to_cubics(
    start: Point,
    rx: float,
    ry: float,
    rotation: float,
    large: bool,
    sweep: bool,
    end: Point,
) -> list[tuple[Point, Point, Point]]:
    """SVG elliptical arc (endpoint form) as cubic Bezier segments (W3C implementation notes)."""
    x1, y1 = start
    x2, y2 = end
    if (x1, y1) == (x2, y2):
        return []
    rx, ry = abs(rx), abs(ry)
    if rx == 0 or ry == 0:
        return [((x1, y1), (x2, y2), (x2, y2))]
    phi = math.radians(rotation % 360)
    cos_p, sin_p = math.cos(phi), math.sin(phi)
    dx, dy = (x1 - x2) / 2, (y1 - y2) / 2
    x1p = cos_p * dx + sin_p * dy
    y1p = -sin_p * dx + cos_p * dy
    lam = (x1p**2) / (rx**2) + (y1p**2) / (ry**2)
    if lam > 1:
        scale = math.sqrt(lam)
        rx, ry = rx * scale, ry * scale
    num = rx**2 * ry**2 - rx**2 * y1p**2 - ry**2 * x1p**2
    den = rx**2 * y1p**2 + ry**2 * x1p**2
    coef = math.sqrt(max(0.0, num / den)) if den else 0.0
    if large == sweep:
        coef = -coef
    cxp = coef * rx * y1p / ry
    cyp = -coef * ry * x1p / rx
    cx = cos_p * cxp - sin_p * cyp + (x1 + x2) / 2
    cy = sin_p * cxp + cos_p * cyp + (y1 + y2) / 2

    def angle(ux: float, uy: float, vx: float, vy: float) -> float:
        dot = ux * vx + uy * vy
        norm = math.hypot(ux, uy) * math.hypot(vx, vy)
        value = math.acos(max(-1.0, min(1.0, dot / norm)))
        return -value if ux * vy - uy * vx < 0 else value

    theta = angle(1, 0, (x1p - cxp) / rx, (y1p - cyp) / ry)
    delta = angle((x1p - cxp) / rx, (y1p - cyp) / ry, (-x1p - cxp) / rx, (-y1p - cyp) / ry)
    if not sweep and delta > 0:
        delta -= 2 * math.pi
    elif sweep and delta < 0:
        delta += 2 * math.pi
    count = max(1, math.ceil(abs(delta) / (math.pi / 2) - 1e-9))
    step = delta / count
    alpha = 4 / 3 * math.tan(step / 4)
    out: list[tuple[Point, Point, Point]] = []
    for i in range(count):
        a1 = theta + i * step
        a2 = a1 + step
        cos1, sin1, cos2, sin2 = math.cos(a1), math.sin(a1), math.cos(a2), math.sin(a2)
        p1 = (cos1 - alpha * sin1, sin1 + alpha * cos1)
        p2 = (cos2 + alpha * sin2, sin2 - alpha * cos2)
        p3 = (cos2, sin2)

        def place(point: Point) -> Point:
            px, py = point[0] * rx, point[1] * ry
            return (cos_p * px - sin_p * py + cx, sin_p * px + cos_p * py + cy)

        out.append((place(p1), place(p2), place(p3)))
    return out


def parse_path(data: str) -> list[Segment]:
    """Absolute ``M``, ``L``, ``C`` and ``Z`` segments for SVG path ``data``.

    Supports ``M L H V C S Q A Z`` in both cases. ``Q`` becomes a cubic, ``A`` becomes cubics.
    A value the path string cannot supply raises :class:`ValueError`.
    """
    tokens = _TOKEN.findall(data)
    segments: list[Segment] = []
    index = 0
    current: Point = (0.0, 0.0)
    start: Point = (0.0, 0.0)
    last_control: Point | None = None
    command = ""
    while index < len(tokens):
        token = tokens[index]
        if token.isalpha():
            command = token
            index += 1
            if command in "Zz":
                segments.append(Segment("Z"))
                current = start
                last_control = None
                continue
        elif not command:
            raise ValueError(f"path data must start with a command: {data[:30]!r}")
        elif command in "Zz":
            raise ValueError("numbers after Z")
        upper = command.upper()
        relative = command.islower()
        if upper == "A":
            values, index = _numbers_for_arc(tokens, index)
        else:
            need = _ARITY[upper]
            chunk = tokens[index : index + need]
            if len(chunk) < need or any(t.isalpha() for t in chunk):
                raise ValueError(f"path command {command} needs {need} numbers")
            values = [float(t) for t in chunk]
            index += need
        ox, oy = current if relative else (0.0, 0.0)
        if upper == "M":
            current = (values[0] + ox, values[1] + oy)
            start = current
            segments.append(Segment("M", (current,)))
            command = "l" if relative else "L"  # extra pairs after M are line-tos
            last_control = None
        elif upper == "L":
            current = (values[0] + ox, values[1] + oy)
            segments.append(Segment("L", (current,)))
            last_control = None
        elif upper == "H":
            current = (values[0] + (current[0] if relative else 0.0), current[1])
            segments.append(Segment("L", (current,)))
            last_control = None
        elif upper == "V":
            current = (current[0], values[0] + (current[1] if relative else 0.0))
            segments.append(Segment("L", (current,)))
            last_control = None
        elif upper == "C":
            p1 = (values[0] + ox, values[1] + oy)
            p2 = (values[2] + ox, values[3] + oy)
            end = (values[4] + ox, values[5] + oy)
            segments.append(Segment("C", (p1, p2, end)))
            current, last_control = end, p2
        elif upper == "S":
            p1 = (
                (2 * current[0] - last_control[0], 2 * current[1] - last_control[1])
                if last_control
                else current
            )
            p2 = (values[0] + ox, values[1] + oy)
            end = (values[2] + ox, values[3] + oy)
            segments.append(Segment("C", (p1, p2, end)))
            current, last_control = end, p2
        elif upper == "Q":
            q = (values[0] + ox, values[1] + oy)
            end = (values[2] + ox, values[3] + oy)
            c1 = (
                current[0] + 2 / 3 * (q[0] - current[0]),
                current[1] + 2 / 3 * (q[1] - current[1]),
            )
            c2 = (end[0] + 2 / 3 * (q[0] - end[0]), end[1] + 2 / 3 * (q[1] - end[1]))
            segments.append(Segment("C", (c1, c2, end)))
            current, last_control = end, None
        else:  # A
            end = (values[5] + ox, values[6] + oy)
            for c1, c2, c3 in _arc_to_cubics(
                current, values[0], values[1], values[2], values[3] != 0, values[4] != 0, end
            ):
                segments.append(Segment("C", (c1, c2, c3)))
            if current == end:
                segments.append(Segment("L", (end,)))
            current, last_control = end, None
    return segments


def icon_segments(name: str) -> list[Segment] | None:
    """All contours of icon ``name`` (concatenated, each starting with ``M``); ``None`` when the
    name is unknown."""
    canonical = canonical_icon(name)
    if canonical is None:
        return None
    out: list[Segment] = []
    for path in _ICONS[canonical]:
        out.extend(parse_path(path))
    return out


def icon_svg_path(name: str) -> str | None:
    """The icon as one absolute SVG path string on the 24 grid (for the HTML mirror)."""
    segments = icon_segments(name)
    if segments is None:
        return None
    parts: list[str] = []
    for seg in segments:
        if seg.op == "Z":
            parts.append("Z")
        else:
            coords = " ".join(f"{x:.3f} {y:.3f}" for x, y in seg.points)
            parts.append(f"{seg.op}{coords}")
    return "".join(parts)


__all__ = [
    "GRID",
    "ICON_NAMES",
    "STROKE",
    "Segment",
    "canonical_icon",
    "has_icon",
    "icon_segments",
    "icon_svg_path",
    "parse_path",
]
