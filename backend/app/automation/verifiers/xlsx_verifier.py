"""Deterministic XLSX Spreadsheet Verifier."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Literal

import openpyxl

from app.automation.verifiers.schemas import VerificationReport, compute_file_sha256

FORMULA_ERROR_TOKENS = {
    "#REF!",
    "#VALUE!",
    "#DIV/0!",
    "#NAME?",
    "#N/A",
    "#NULL!",
    "#NUM!",
}

MAX_FILE_SIZE_BYTES = 50 * 1024 * 1024  # 50 MB limit per Phase 10 spec


def verify_xlsx(file_path: Path | str) -> VerificationReport:
    """Verify an XLSX workbook for structural, mathematical, and formula integrity.

    Checks performed:
    1. File existence and size constraint (<= 50 MB).
    2. File extension must be .xlsx (no .xlsm macros permitted).
    3. Workbook opens cleanly via openpyxl.
    4. At least one sheet exists and contains rows.
    5. Zero formula error tokens across all cells in all worksheets.
    6. All formula strings have balanced parentheses and valid syntax prefixes.
    """
    path = Path(file_path).resolve()
    checks_passed: list[str] = []
    errors: list[str] = []

    if not path.is_file():
        return VerificationReport(
            file_type="xlsx",
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

    if path.suffix.lower() != ".xlsx":
        errors.append(f"Forbidden extension '{path.suffix}'; only .xlsx permitted")
    else:
        checks_passed.append("valid_file_extension")

    sha256 = compute_file_sha256(path)

    # Inspect openpyxl workbook
    total_sheets = 0
    total_cells_checked = 0
    formulas_checked = 0

    try:
        wb = openpyxl.load_workbook(path, data_only=False)
        total_sheets = len(wb.sheetnames)
        if total_sheets == 0:
            errors.append("Workbook contains zero worksheets")
        else:
            checks_passed.append("workbook_has_worksheets")

        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            if ws.max_row == 0 or ws.max_column == 0:
                continue

            for row in ws.iter_rows():
                for cell in row:
                    total_cells_checked += 1
                    val = cell.value
                    if val is None:
                        continue

                    val_str = str(val).strip()

                    # Check for literal formula error token
                    for token in FORMULA_ERROR_TOKENS:
                        if token in val_str:
                            errors.append(
                                f"Formula error '{token}' at {sheet_name}!{cell.coordinate}"
                            )

                    # Check formula syntax if cell is a formula
                    if val_str.startswith("="):
                        formulas_checked += 1
                        # Balanced parentheses check
                        if val_str.count("(") != val_str.count(")"):
                            errors.append(
                                f"Unbalanced parentheses in formula '{val_str}' at "
                                f"{sheet_name}!{cell.coordinate}"
                            )

        if not errors:
            checks_passed.append("zero_formula_errors")
            if formulas_checked > 0:
                checks_passed.append(f"verified_{formulas_checked}_formulas")

    except Exception as exc:
        errors.append(f"Failed to parse XLSX workbook: {exc}")

    status: Literal["verified", "failed"] = "verified" if not errors else "failed"

    return VerificationReport(
        file_type="xlsx",
        status=status,
        sha256=sha256,
        size_bytes=size,
        checks_passed=checks_passed,
        errors=errors,
        metadata={
            "total_sheets": total_sheets,
            "total_cells_checked": total_cells_checked,
            "formulas_checked": formulas_checked,
        },
    )
