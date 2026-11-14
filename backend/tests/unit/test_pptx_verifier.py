"""Unit tests for PPTX presentation generation engine and bounding-box verifier."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from pptx import Presentation
from pptx.util import Inches

from app.automation.pptx_engine import create_presentation
from app.automation.verifiers.pptx_verifier import verify_pptx


def test_pptx_engine_generates_verified_8_slide_deck(tmp_path: Path) -> None:
    """Test generating an 8-slide deck (Workflow 7) and verifying it passes."""
    file_path = tmp_path / "pitch_deck.pptx"

    slides: list[dict[str, Any]] = [
        {"layout": "title", "title": "MAYA AI Digital Employee", "subtitle": "Investor Briefing"},
        {
            "layout": "cards",
            "title": "Core Value Pillars",
            "cards": [
                {"title": "Multi-Agent System", "body": "Autonomous task DAG decomposition."},
                {"title": "Deterministic Quality", "body": "Zero unverified deliverables."},
                {"title": "Enterprise Security", "body": "Strict multi-tenant RLS boundaries."},
            ],
        },
        {
            "layout": "metrics",
            "title": "Platform Highlights",
            "metrics": [
                {"value": "100%", "label": "Passing Test Rate"},
                {"value": "19", "label": "RLS Partitioned Tables"},
                {"value": "<200ms", "label": "Routing Latency"},
            ],
        },
        {"layout": "bullets", "title": "Market Opportunity", "bullets": ["TAM $50B+", "CAGR 35%"]},
        {
            "layout": "bullets",
            "title": "Architecture",
            "bullets": ["LangGraph", "FastAPI", "Postgres 17"],
        },
        {"layout": "bullets", "title": "Traction", "bullets": ["Phase 01–09 certified complete"]},
        {"layout": "bullets", "title": "Financial Projections", "bullets": ["Break-even Year 2"]},
        {
            "layout": "bullets",
            "title": "Team & Next Steps",
            "bullets": ["Engineering rollout Phase 10"],
        },
    ]

    out = create_presentation(slides, file_path)
    assert out.is_file()

    report = verify_pptx(out, expected_slides=8)
    assert report.status == "verified"
    assert report.file_type == "pptx"
    assert report.metadata["total_slides"] == 8
    assert "verified_16_9_widescreen_aspect_ratio" in report.checks_passed
    assert "slide_count_matches_expected_8" in report.checks_passed
    assert len(report.errors) == 0


def test_pptx_engine_applies_brand_template_to_slides(tmp_path: Path) -> None:
    """Polished decks should include the branded accent bar and a solid background treatment."""
    file_path = tmp_path / "template_deck.pptx"
    slides: list[dict[str, Any]] = [
        {"layout": "title", "title": "Quarterly Strategy Review", "subtitle": "MAYA AI • FY2026"},
        {
            "layout": "bullets",
            "title": "Key priorities",
            "bullets": ["Scale execution", "Launch AI workflows", "Verify quality"],
        },
    ]

    out = create_presentation(slides, file_path)
    prs = Presentation(str(out))

    first_slide = prs.slides[0]
    colored_shapes = []
    for shape in first_slide.shapes:
        try:
            _ = shape.fill.fore_color
        except TypeError:
            continue
        colored_shapes.append(shape)

    assert any(colored_shapes)
    assert len(prs.slides) == 2


def test_pptx_verifier_reports_a_slide_count_mismatch_as_a_warning(tmp_path: Path) -> None:
    """Count policy (deliberate update): a slide count that differs is a WARNING, never a failed
    file. This used to be an error that made the whole verification (and the task step) fail."""
    file_path = tmp_path / "short_deck.pptx"
    slides = [{"layout": "title", "title": "Single Slide"}]
    out = create_presentation(slides, file_path)

    report = verify_pptx(out, expected_slides=8)
    assert report.status == "verified" and not report.errors
    assert any("Slide count mismatch: expected 8, got 1" in w for w in report.warnings)
    assert "slide_count_matches_expected_8" not in report.checks_passed


def test_pptx_verifier_detects_canvas_overflow(tmp_path: Path) -> None:
    """Negative test: shape protruding outside canvas boundaries triggers verification failure."""
    file_path = tmp_path / "overflowing_deck.pptx"

    prs = Presentation()
    prs.slide_width = Inches(13.333)
    prs.slide_height = Inches(7.5)
    slide = prs.slides.add_slide(prs.slide_layouts[6])

    # Add a shape that extends past the right edge (e.g. left=11, width=4 -> right=15 > 13.333)
    slide.shapes.add_textbox(Inches(11.0), Inches(2.0), Inches(4.0), Inches(2.0))
    prs.save(str(file_path))

    report = verify_pptx(file_path)
    assert report.status == "failed"
    assert any("horizontal overflow" in err for err in report.errors)
