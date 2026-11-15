"""Conservative text-overflow estimate for a python-pptx shape (engine safety net + verifier)."""

from __future__ import annotations

from typing import Any

from app.automation.textfit import estimate_height_in

EMU_PER_INCH = 914_400
DEFAULT_FONT_PT = 18.0
# PowerPoint's default text-frame insets when none are set on the shape.
DEFAULT_MARGIN_LR_IN = 0.1
DEFAULT_MARGIN_TB_IN = 0.05
# Allow a small tolerance so rounding does not flag a box that visibly fits.
OVERFLOW_TOLERANCE = 1.05


def _inches(value: Any, default: float) -> float:
    return default if value is None else int(value) / EMU_PER_INCH


def _paragraph_font_pt(paragraph: Any) -> float:
    sizes = [r.font.size.pt for r in paragraph.runs if r.font.size is not None]
    if sizes:
        return float(max(sizes))
    if paragraph.font.size is not None:
        return float(paragraph.font.size.pt)
    return DEFAULT_FONT_PT


def estimate_shape_text(shape: Any) -> tuple[float, float] | None:
    """``(estimated_height_in, available_height_in)`` for a text-bearing shape, else ``None``.

    Tables, pictures and empty shapes are skipped. The estimate assumes wide glyphs, so a pass
    here means the text very probably fits in a real PowerPoint.
    """
    if not getattr(shape, "has_text_frame", False) or shape.width is None or shape.height is None:
        return None
    frame = shape.text_frame
    if not frame.text.strip():
        return None
    margin_l = _inches(frame.margin_left, DEFAULT_MARGIN_LR_IN)
    margin_r = _inches(frame.margin_right, DEFAULT_MARGIN_LR_IN)
    margin_t = _inches(frame.margin_top, DEFAULT_MARGIN_TB_IN)
    margin_b = _inches(frame.margin_bottom, DEFAULT_MARGIN_TB_IN)
    width = int(shape.width) / EMU_PER_INCH - margin_l - margin_r
    available = int(shape.height) / EMU_PER_INCH - margin_t - margin_b
    needed = 0.0
    for paragraph in frame.paragraphs:
        pPr = paragraph._p.pPr
        indent_in = 0.0
        if pPr is not None and pPr.get("marL") is not None:
            indent_in = int(pPr.get("marL")) / EMU_PER_INCH
        space_after = paragraph.space_after.pt if paragraph.space_after is not None else 0.0
        text = paragraph.text.replace(chr(11), chr(10))  # a:br reads back as a vertical tab
        needed += estimate_height_in(
            [(text, _paragraph_font_pt(paragraph))],
            max(0.1, width - indent_in),
            space_after,
        )
    return needed, available


def overflows(shape: Any) -> bool:
    estimate = estimate_shape_text(shape)
    if estimate is None:
        return False
    needed, available = estimate
    return needed > available * OVERFLOW_TOLERANCE
