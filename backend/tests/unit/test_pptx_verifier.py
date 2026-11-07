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


def test_pptx_verifier_detects_slide_count_mismatch(tmp_path: Path) -> None:
    """Negative test: slide count mismatch triggers verification failure."""
    file_path = tmp_path / "short_deck.pptx"
    slides = [{"layout": "title", "title": "Single Slide"}]
    out = create_presentation(slides, file_path)

    report = verify_pptx(out, expected_slides=8)
    assert report.status == "failed"
    assert any("Slide count mismatch: expected 8, got 1" in err for err in report.errors)


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
