"""Unit tests for PDF generation engine and multi-layer text verifier."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from app.automation.pdf_engine import create_pdf
from app.automation.verifiers.pdf_verifier import verify_pdf


def test_pdf_engine_generates_verified_multipage_report(tmp_path: Path) -> None:
    """Test generating a multi-page PDF report (Workflow 5) and verifying it passes."""
    file_path = tmp_path / "competitive_analysis.pdf"

    sections: list[dict[str, Any]] = [
        {"type": "heading1", "text": "1. Market Overview & Competitive Landscape"},
        {
            "type": "paragraph",
            "text": (
                "The enterprise AI workforce market is projected to reach $50B by 2030. "
                "MAYA AI pioneers multi-agent orchestration with deterministic verifiers."
            ),
        },
        {
            "type": "table",
            "headers": ["Competitor", "Market Share", "Key Differentiator"],
            "rows": [
                ["Legacy Automation", "45%", "Rule-based RPA"],
                ["Chatbot Wrappers", "30%", "Simple prompt chaining"],
                ["MAYA AI", "25%", "Deterministic verification & RLS"],
            ],
        },
        {"type": "page_break"},
        {"type": "heading1", "text": "2. Technical Architecture & Security Invariants"},
        {
            "type": "paragraph",
            "text": (
                "MAYA AI strictly enforces Decision D4 (Tool Governance) and D5 (RFC 8785). "
                "Every artifact is deterministically verified before user delivery."
            ),
        },
    ]

    out = create_pdf(
        title="Enterprise AI Competitive Analysis",
        subtitle="Confidential Briefing Document",
        sections=sections,
        output_path=file_path,
    )
    assert out.is_file()

    report = verify_pdf(out, min_pages=2)
    assert report.status == "verified"
    assert report.file_type == "pdf"
    assert report.metadata["total_pages"] >= 2
    assert report.metadata["total_characters_extracted"] > 100
    assert "verified_2_pages" in report.checks_passed or any(
        "verified_" in c and "_pages" in c for c in report.checks_passed
    )
    assert len(report.errors) == 0


def test_pdf_verifier_rejects_corrupted_pdf(tmp_path: Path) -> None:
    """Negative test: corrupted byte content fails verification."""
    corrupt_file = tmp_path / "corrupted.pdf"
    corrupt_file.write_bytes(
        b"%PDF-1.4\nCorrupted binary garbage without trailer or objects\n%%EOF"
    )

    report = verify_pdf(corrupt_file)
    assert report.status == "failed"
    assert len(report.errors) > 0


def test_pdf_verifier_enforces_minimum_page_count(tmp_path: Path) -> None:
    """Negative test: document with fewer pages than required fails verification."""
    file_path = tmp_path / "single_page.pdf"
    sections: list[dict[str, Any]] = [
        {"type": "heading1", "text": "Single Page Document"},
        {"type": "paragraph", "text": "This is only one page."},
    ]
    out = create_pdf("Short Note", sections, file_path)

    report = verify_pdf(out, min_pages=3)
    assert report.status == "failed"
    assert any("less than required minimum 3" in err for err in report.errors)
