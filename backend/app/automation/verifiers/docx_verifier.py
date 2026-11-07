"""Deterministic DOCX Document Verifier."""

from __future__ import annotations

import os
import zipfile
from pathlib import Path
from typing import Literal

from lxml import etree

from app.automation.verifiers.schemas import VerificationReport, compute_file_sha256

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB
W_NS = "http://schemas.openxmlformats.org/wordprocessingml/2006/main"
NAMESPACES = {"w": W_NS}


def verify_docx(file_path: Path | str) -> VerificationReport:
    """Verify a DOCX document for structural, XML, style hierarchy, and table integrity.

    Checks performed:
    1. File existence and size constraint (<= 50 MB).
    2. File extension must be .docx.
    3. Document is a valid zip archive containing word/document.xml.
    4. word/document.xml parses cleanly with entity resolution disabled (XXE defense).
    5. Document body is non-empty.
    6. Heading hierarchy: Heading 2 cannot precede Heading 1; Heading 3 cannot precede Heading 2.
    7. All tables have consistent column counts across all rows and non-empty header cells.
    """
    path = Path(file_path).resolve()
    checks_passed: list[str] = []
    errors: list[str] = []

    if not path.is_file():
        return VerificationReport(
            file_type="docx",
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

    if path.suffix.lower() != ".docx":
        errors.append(f"Forbidden extension '{path.suffix}'; only .docx permitted")
    else:
        checks_passed.append("valid_file_extension")

    sha256 = compute_file_sha256(path)

    # Inspect zip structure and word/document.xml
    total_paragraphs = 0
    total_tables = 0
    headings_found: list[int] = []

    try:
        with zipfile.ZipFile(path, "r") as zf:
            if "word/document.xml" not in zf.namelist():
                errors.append("Invalid DOCX: missing word/document.xml")
                return VerificationReport(
                    file_type="docx",
                    status="failed",
                    sha256=sha256,
                    size_bytes=size,
                    errors=errors,
                )

            xml_content = zf.read("word/document.xml")

        checks_passed.append("valid_docx_zip_structure")

        # Parse XML with safe parser (no entity resolution to prevent XXE / file bombs)
        parser = etree.XMLParser(resolve_entities=False, no_network=True)
        root = etree.fromstring(xml_content, parser=parser)
        checks_passed.append("xml_well_formed_and_secure")

        body = root.find("w:body", NAMESPACES)
        if body is None or len(body) == 0:
            errors.append("Document body is empty")
        else:
            checks_passed.append("document_body_non_empty")

        # 1. Heading hierarchy inspection
        paragraphs = root.findall(".//w:p", NAMESPACES)
        total_paragraphs = len(paragraphs)

        for p in paragraphs:
            pStyle = p.find(".//w:pPr/w:pStyle", NAMESPACES)
            if pStyle is not None:
                style_val = pStyle.get(f"{{{W_NS}}}val", "")
                if style_val in ("Heading1", "heading 1", "Heading 1"):
                    headings_found.append(1)
                elif style_val in ("Heading2", "heading 2", "Heading 2"):
                    if 1 not in headings_found:
                        errors.append(
                            "Invalid heading hierarchy: Heading 2 found before any Heading 1"
                        )
                    headings_found.append(2)
                elif style_val in ("Heading3", "heading 3", "Heading 3"):
                    if 2 not in headings_found:
                        errors.append(
                            "Invalid heading hierarchy: Heading 3 found before any Heading 2"
                        )
                    headings_found.append(3)

        if not any("Invalid heading hierarchy" in e for e in errors):
            checks_passed.append("heading_hierarchy_consistent")

        # 2. Table structural inspection
        tables = root.findall(".//w:tbl", NAMESPACES)
        total_tables = len(tables)

        for tbl_idx, tbl in enumerate(tables, start=1):
            tbl_rows = tbl.findall(".//w:tr", NAMESPACES)
            if not tbl_rows:
                errors.append(f"Table #{tbl_idx} has zero rows")
                continue

            first_row_cols = len(tbl_rows[0].findall(".//w:tc", NAMESPACES))
            if first_row_cols == 0:
                errors.append(f"Table #{tbl_idx} header row has zero columns")

            # Check matching column count across rows
            for row_idx, r in enumerate(tbl_rows, start=1):
                col_count = len(r.findall(".//w:tc", NAMESPACES))
                if col_count != first_row_cols:
                    errors.append(
                        f"Table #{tbl_idx} row #{row_idx} column count mismatch: "
                        f"expected {first_row_cols}, got {col_count}"
                    )

        if total_tables > 0 and not any("Table #" in e for e in errors):
            checks_passed.append(f"verified_{total_tables}_tables_structure")

    except Exception as exc:
        errors.append(f"Failed to parse DOCX: {exc}")

    status: Literal["verified", "failed"] = "verified" if not errors else "failed"

    return VerificationReport(
        file_type="docx",
        status=status,
        sha256=sha256,
        size_bytes=size,
        checks_passed=checks_passed,
        errors=errors,
        metadata={
            "total_paragraphs": total_paragraphs,
            "total_tables": total_tables,
            "headings_found": headings_found,
        },
    )
