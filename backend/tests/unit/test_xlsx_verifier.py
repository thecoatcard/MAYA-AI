"""Unit tests for XLSX generation engine and deterministic verifier."""

from __future__ import annotations

from pathlib import Path

from openpyxl import Workbook

from app.automation.verifiers.xlsx_verifier import verify_xlsx
from app.automation.xlsx_engine import create_spreadsheet


def test_xlsx_engine_generates_verified_workbook(tmp_path: Path) -> None:
    """Test generating a clean multi-tab spreadsheet and verifying it passes."""
    file_path = tmp_path / "invoice_summary.xlsx"

    sheets = [
        {
            "title": "Invoices Q3",
            "headers": ["Invoice ID", "Client", "Amount", "Status"],
            "rows": [
                ["INV-001", "Acme Corp", 12500.50, "Paid"],
                ["INV-002", "Globex", 8400.00, "Pending"],
                ["INV-003", "Stark Ind", 19500.00, "Paid"],
            ],
            "formulas": {
                "C5": "=SUM(C2:C4)",
            },
            "number_formats": {
                3: "$#,##0.00",
            },
        }
    ]

    out = create_spreadsheet(sheets, file_path)
    assert out.is_file()

    report = verify_xlsx(out)
    assert report.status == "verified"
    assert report.file_type == "xlsx"
    assert report.size_bytes > 0
    assert len(report.sha256) == 64
    assert "zero_formula_errors" in report.checks_passed
    assert report.metadata["formulas_checked"] == 1
    assert len(report.errors) == 0


def test_xlsx_verifier_detects_formula_errors(tmp_path: Path) -> None:
    """Negative test: verify that #REF! or #VALUE! is detected with exact coordinate."""
    corrupt_file = tmp_path / "corrupt_formulas.xlsx"

    wb = Workbook()
    assert wb.active is not None
    ws = wb.active
    ws.title = "Broken"
    ws["A1"] = "Total"
    ws["B1"] = "#REF!"
    ws["A2"] = "Subtotal"
    ws["B2"] = "=SUM(B3:B10"  # Unbalanced parentheses
    wb.save(corrupt_file)

    report = verify_xlsx(corrupt_file)
    assert report.status == "failed"
    assert any("Formula error '#REF!' at Broken!B1" in err for err in report.errors)
    assert any("Unbalanced parentheses in formula '=SUM(B3:B10'" in err for err in report.errors)


def test_xlsx_verifier_rejects_forbidden_extensions(tmp_path: Path) -> None:
    """Security test: reject .xlsm macro-enabled workbooks."""
    xlsm_file = tmp_path / "macro_workbook.xlsm"
    xlsm_file.write_bytes(b"dummy")

    report = verify_xlsx(xlsm_file)
    assert report.status == "failed"
    assert any("Forbidden extension '.xlsm'" in err for err in report.errors)
