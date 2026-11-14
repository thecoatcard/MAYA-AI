"""Conservative text-fit estimates shared by the PPTX engine and the PPTX verifier.

Nothing here measures real glyphs. The estimates deliberately assume wide characters so a text
box that passes is very unlikely to overflow in PowerPoint, Keynote or LibreOffice.
"""

from __future__ import annotations

import math
from collections.abc import Callable

# Average advance width as a fraction of the font size. Latin text averages ~0.5 em; 0.55 is
# conservative. Devanagari and other wide scripts are estimated at 0.65 em.
LATIN_EM = 0.55
WIDE_EM = 0.65
LINE_SPACING = 1.2


def _char_em(text: str) -> float:
    return WIDE_EM if any(ord(ch) > 0x024F for ch in text) else LATIN_EM


def estimate_lines(text: str, font_pt: float, width_in: float) -> int:
    """Estimated wrapped line count of ``text`` (newlines start new lines)."""
    if font_pt <= 0 or width_in <= 0:
        return 10_000
    chars_per_line = max(1, int((width_in * 72.0) / (font_pt * _char_em(text))))
    total = 0
    for raw in text.split("\n") or [""]:
        total += max(1, math.ceil(len(raw) / chars_per_line))
    return total


def estimate_height_in(
    paragraphs: list[tuple[str, float]],
    width_in: float,
    space_after_pt: float = 0.0,
) -> float:
    """Estimated height in inches of ``(text, font_pt)`` paragraphs in a box ``width_in`` wide."""
    points = 0.0
    for text, font_pt in paragraphs:
        points += estimate_lines(text, font_pt, width_in) * font_pt * LINE_SPACING
        points += space_after_pt
    return points / 72.0


def fit_size(
    build: Callable[[float], list[tuple[str, float]]],
    *,
    width_in: float,
    height_in: float,
    start_pt: float,
    min_pt: float,
    space_after_pt: float = 0.0,
) -> float | None:
    """Largest whole-point size in ``[min_pt, start_pt]`` whose paragraphs fit, else ``None``.

    ``build(size)`` returns the ``(text, font_pt)`` paragraphs to measure at that base size.
    """
    size = float(start_pt)
    while size >= min_pt:
        if estimate_height_in(build(size), width_in, space_after_pt) <= height_in:
            return size
        size -= 1.0
    return None
