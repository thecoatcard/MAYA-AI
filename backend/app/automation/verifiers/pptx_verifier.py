"""Deterministic PPTX Presentation Verifier."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

from pptx import Presentation

from app.automation.verifiers.schemas import VerificationReport, compute_file_sha256

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
# Margin tolerance: allow small sub-millimeter rounding (approx 0.05 inches in EMUs)
TOLERANCE_EMU = 45720  # 0.05 inches


def verify_pptx(
    file_path: Path | str,
    expected_slides: int | None = None,
) -> VerificationReport:
    """Verify a PowerPoint presentation for slide count, aspect ratio, and bounding boxes.

    Checks performed:
    1. File existence and size constraint (<= 50 MB).
    2. File extension must be .pptx.
    3. Presentation opens cleanly via python-pptx.
    4. Slide count assertion (if expected_slides provided).
    5. 16:9 widescreen canvas aspect ratio confirmation.
    6. All shape bounding boxes (left, top, right, bottom) remain strictly inside canvas boundaries.
    """
    path = Path(file_path).resolve()
    checks_passed: list[str] = []
    errors: list[str] = []

    if not path.is_file():
        return VerificationReport(
            file_type="pptx",
            status="failed",
            sha256="",
            size_bytes=0,
            errors=[f"File not found: {path}"],
        )

    size = os.path.getsize(path)
    if size > MAX_FILE_SIZE_BYTES:
        errors.append(f"File size {size} bytes exceeds 50 MB limit")
    else:
        checks_passed.append("file_size_within_budget")

    if path.suffix.lower() != ".pptx":
        errors.append(f"Forbidden extension '{path.suffix}'; only .pptx permitted")
    else:
        checks_passed.append("valid_file_extension")

    sha256 = compute_file_sha256(path)

    total_slides = 0
    total_shapes_checked = 0

    try:
        prs = Presentation(str(path))
        total_slides = len(prs.slides)

        if total_slides == 0:
            errors.append("Presentation contains zero slides")
        else:
            checks_passed.append("presentation_has_slides")

        if expected_slides is not None:
            if total_slides != expected_slides:
                errors.append(
                    f"Slide count mismatch: expected {expected_slides}, got {total_slides}"
                )
            else:
                checks_passed.append(f"slide_count_matches_expected_{expected_slides}")

        # Check 16:9 aspect ratio
        canvas_w = int(prs.slide_width) if prs.slide_width is not None else 0
        canvas_h = int(prs.slide_height) if prs.slide_height is not None else 0
        if canvas_w == 0 or canvas_h == 0:
            errors.append("Invalid canvas dimensions on presentation")
            return VerificationReport(
                file_type="pptx",
                status="failed",
                sha256=sha256,
                size_bytes=size,
                errors=errors,
            )
        ratio = canvas_w / canvas_h
        expected_16_9 = 16.0 / 9.0
        if abs(ratio - expected_16_9) < 0.05:
            checks_passed.append("verified_16_9_widescreen_aspect_ratio")
        else:
            errors.append(f"Non-widescreen aspect ratio: {ratio:.3f} (expected ~1.777 for 16:9)")

        # Inspect all shape bounding boxes across all slides
        for s_idx, slide in enumerate(prs.slides, start=1):
            for shp_idx, shape in enumerate(slide.shapes, start=1):
                if (
                    shape.left is None
                    or shape.top is None
                    or shape.width is None
                    or shape.height is None
                ):
                    continue
                total_shapes_checked += 1
                s_left = int(shape.left)
                s_top = int(shape.top)
                s_width = int(shape.width)
                s_height = int(shape.height)

                s_right = s_left + s_width
                s_bottom = s_top + s_height

                # Check left / top underflow
                if s_left < -TOLERANCE_EMU or s_top < -TOLERANCE_EMU:
                    errors.append(
                        f"Slide #{s_idx} Shape #{shp_idx} position negative "
                        f"(left={s_left}, top={s_top})"
                    )

                # Check right / bottom overflow
                if s_right > (canvas_w + TOLERANCE_EMU):
                    errors.append(
                        f"Slide #{s_idx} Shape #{shp_idx} horizontal overflow: "
                        f"right edge {s_right} exceeds canvas width {canvas_w}"
                    )

                if s_bottom > (canvas_h + TOLERANCE_EMU):
                    errors.append(
                        f"Slide #{s_idx} Shape #{shp_idx} vertical overflow: "
                        f"bottom edge {s_bottom} exceeds canvas height {canvas_h}"
                    )

        if not any("overflow" in e or "position negative" in e for e in errors):
            checks_passed.append(f"verified_{total_shapes_checked}_shapes_within_canvas_bounds")

    except Exception as exc:
        errors.append(f"Failed to parse PPTX presentation: {exc}")

    status: Literal["verified", "failed"] = "verified" if not errors else "failed"

    return VerificationReport(
        file_type="pptx",
        status=status,
        sha256=sha256,
        size_bytes=size,
        checks_passed=checks_passed,
        errors=errors,
        metadata={
            "total_slides": total_slides,
            "total_shapes_checked": total_shapes_checked,
            "slide_width_emu": canvas_w if "canvas_w" in locals() else 0,
            "slide_height_emu": canvas_h if "canvas_h" in locals() else 0,
        },
    )
