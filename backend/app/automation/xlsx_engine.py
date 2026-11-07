"""XLSX Spreadsheet Generation Engine."""

from __future__ import annotations

from pathlib import Path
from typing import Any

from openpyxl import Workbook
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter


def create_spreadsheet(
    sheets: list[dict[str, Any]],
    output_path: Path | str,
) -> Path:
    """Generate a multi-tab formatted Excel spreadsheet (.xlsx).

    :param sheets: List of sheet definitions:
      - title: Sheet name (str)
      - headers: List of column names (list[str])
      - rows: List of row data lists (list[list[Any]])
      - formulas: Dict mapping cell coordinates (e.g. "B11") to formula string
      - number_formats: Dict mapping column indices (1-based) to format string
    :param output_path: Destination path on disk.
    :return: Resolved Path to generated .xlsx file.
    """
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    wb = Workbook()
    if wb.active is not None:
        wb.remove(wb.active)  # Remove default sheet

    header_font = Font(name="Calibri", size=11, bold=True, color="FFFFFF")
    header_fill = PatternFill(start_color="1F4E79", end_color="1F4E79", fill_type="solid")
    header_alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)

    thin_border = Border(
        left=Side(style="thin", color="D9D9D9"),
        right=Side(style="thin", color="D9D9D9"),
        top=Side(style="thin", color="D9D9D9"),
        bottom=Side(style="thin", color="D9D9D9"),
    )

    for sheet_def in sheets:
        title = sheet_def.get("title", "Sheet1")[:31]  # Excel 31-char limit
        ws = wb.create_sheet(title=title)
        ws.views.sheetView[0].showGridLines = True

        headers = sheet_def.get("headers", [])
        if headers:
            ws.append(headers)
            for col_idx, _ in enumerate(headers, start=1):
                cell = ws.cell(row=1, column=col_idx)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = header_alignment
                cell.border = thin_border
            ws.row_dimensions[1].height = 26

        rows = sheet_def.get("rows", [])
        for row_data in rows:
            ws.append(row_data)

        # Apply row borders and styling
        start_row = 2 if headers else 1
        for row_idx in range(start_row, ws.max_row + 1):
            for col_idx in range(1, ws.max_column + 1):
                cell = ws.cell(row=row_idx, column=col_idx)
                cell.border = thin_border
                cell.font = Font(name="Calibri", size=10)

        # Apply specific formulas
        formulas = sheet_def.get("formulas", {})
        for coord, formula in formulas.items():
            ws[coord] = formula
            ws[coord].font = Font(name="Calibri", size=10, bold=True)

        # Apply number formatting by column
        col_formats = sheet_def.get("number_formats", {})
        for col_idx, fmt in col_formats.items():
            for row_idx in range(start_row, ws.max_row + 1):
                ws.cell(row=row_idx, column=col_idx).number_format = fmt

        # Auto-adjust column widths
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                val_str = str(cell.value or "")
                max_len = max(max_len, len(val_str))
            ws.column_dimensions[col_letter].width = max(max_len + 4, 12)

        # Freeze top header row if headers exist
        if headers:
            ws.freeze_panes = "A2"

    wb.save(path)
    return path
