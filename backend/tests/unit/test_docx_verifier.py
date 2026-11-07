"""Unit tests for DOCX generation engine and deterministic structural XML verifier."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from docx import Document

from app.automation.docx_engine import create_document
from app.automation.verifiers.docx_verifier import verify_docx


def test_docx_engine_generates_verified_document(tmp_path: Path) -> None:
    """Test generating a structured Word document and verifying it passes."""
    file_path = tmp_path / "project_brief.docx"

    sections: list[dict[str, Any]] = [
        {"type": "heading1", "text": "1. Executive Summary"},
        {"type": "paragraph", "text": "This document outlines the Q4 technical roadmap."},
        {"type": "heading2", "text": "1.1 Goals and Objectives"},
        {"type": "bullet", "items": ["Ship Phase 10", "Zero defects", "Fast response"]},
        {"type": "heading3", "text": "1.1.1 Key Results"},
        {"type": "callout", "text": "Milestone targeted for mid-November."},
        {
            "type": "table",
            "headers": ["Deliverable", "Owner", "Status"],
            "rows": [
                ["XLSX Engine", "Backend Lead", "Completed"],
                ["DOCX Engine", "Backend Lead", "Completed"],
                ["PPTX Engine", "Backend Lead", "Completed"],
            ],
        },
    ]

    out = create_document(
        sections=sections,
        output_path=file_path,
        title="Engineering Roadmap",
        subtitle="Version 1.0",
    )
    assert out.is_file()

    report = verify_docx(out)
    assert report.status == "verified"
    assert report.file_type == "docx"
    assert report.size_bytes > 0
    assert len(report.sha256) == 64
    assert "heading_hierarchy_consistent" in report.checks_passed
    assert "verified_1_tables_structure" in report.checks_passed
    assert len(report.errors) == 0


def test_docx_verifier_detects_inverted_heading_hierarchy(tmp_path: Path) -> None:
    """Negative test: Heading 2 preceding Heading 1 fails verification."""
    corrupt_file = tmp_path / "inverted_headings.docx"

    doc = Document()
    doc.add_heading("Section 1.1 Premature Subheading", level=2)
    doc.add_heading("Section 1 Main Heading", level=1)
    doc.save(str(corrupt_file))

    report = verify_docx(corrupt_file)
    assert report.status == "failed"
    assert any("Invalid heading hierarchy" in err for err in report.errors)


def test_docx_verifier_detects_malformed_tables(tmp_path: Path) -> None:
    """Negative test: table with mismatched column counts fails verification."""
    corrupt_file = tmp_path / "broken_table.docx"

    from docx.oxml import OxmlElement

    doc = Document()
    doc.add_heading("Section 1", level=1)
    table = doc.add_table(rows=2, cols=3)
    # Add a row with 4 cells instead of 3
    extra_row = table.add_row()
    extra_cell = OxmlElement("w:tc")
    extra_row._tr.append(extra_cell)  # now has 4 cells
    doc.save(str(corrupt_file))

    report = verify_docx(corrupt_file)
    assert report.status == "failed"
    assert any("column count mismatch" in err for err in report.errors)
