"""Reading and changing an existing spreadsheet without putting its rows into the model context.

* :func:`sheet_summary` is a read-only inspection: sheets, dimensions, header, the first 5 rows,
  the type of every column, empty counts and duplicate rows. Bounded output (a 50,000-row sheet
  gives the same few kilobytes as a 5-row one), so MAYA can answer questions about a sheet.
* :func:`apply_sheet_ops` applies named operations to a copy: add, rename or delete a column, add
  rows, sort, filter and copy to a new sheet, remove duplicates, fill a formula down, recalculate
  the totals row, find and replace, convert text numbers to numbers. Cell styles stay where they
  are (banding follows the position, number formats follow the column), formulas that move are
  translated, the table, filter, validation and conditional-format ranges follow the data, and
  every operation reports exactly how many cells or rows it changed.

The model only decides WHICH operation; the code applies it (same discipline as ``file_edit``).
Charts and images of the original are not kept by openpyxl; the caller warns about that.
"""

from __future__ import annotations

import re
from collections import Counter
from collections.abc import Mapping, Sequence
from copy import copy
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.formatting.formatting import ConditionalFormattingList
from openpyxl.formula.translate import Translator
from openpyxl.styles import Alignment
from openpyxl.styles.cell_style import StyleArray
from openpyxl.utils import get_column_letter

from app.automation.sheet_gen import MAX_COLUMNS, MAX_ROWS, eval_row_formula

SHEET_OPS: tuple[str, ...] = (
    "add_column",
    "rename_column",
    "delete_column",
    "add_rows",
    "sort",
    "filter_copy",
    "dedupe",
    "fill_formula_down",
    "recalc_totals",
    "find_replace",
    "text_to_number",
)
FILTER_OPERATORS: tuple[str, ...] = ("eq", "ne", "gt", "ge", "lt", "le", "contains")
MAX_SUMMARY_ROWS = 5
MAX_SUMMARY_COLUMNS = 30
MAX_SUMMARY_SHEETS = 12
MAX_SUMMARY_CELL_CHARS = 60
MAX_SUMMARY_CHARS = 8_000
MAX_OPS = 50
MAX_ADDED_ROWS = 1_000
_BAD_FORMULA = re.compile(
    r"(?i)\b(WEBSERVICE|HYPERLINK|CALL|REGISTER|EXEC|FILTERXML|RTD|DDE|SHELL)\s*\("
    r"|\[|\||\\\\|https?:|file:"
)
_NUMBERISH = re.compile(r"^\s*[+-]?(?:\d{1,3}(?:,\d{3})+|\d+)(?:\.\d+)?\s*$")
_REF = re.compile(
    r"(?<![A-Za-z0-9_.$])((?:'[^']+'|[A-Za-z0-9_]+)!)?(\$?)([A-Z]{1,3})(\$?)(\d+)(?![A-Za-z0-9_(])"
)


class SheetOpError(ValueError):
    """An operation cannot be applied; the message says why in plain words."""


@dataclass
class OpsOutcome:
    """What a run of operations changed."""

    lines: list[str] = field(default_factory=list)
    changes: int = 0
    warnings: list[str] = field(default_factory=list)
    counts: list[dict[str, Any]] = field(default_factory=list)


# ------------------------------------------------------------------------------------ layout
@dataclass
class _Layout:
    header_row: int
    first: int  # first data row
    last: int  # last data row (before any totals row)
    width: int
    totals_row: int | None
    table: Any | None


def _is_blank(value: Any) -> bool:
    return value is None or (isinstance(value, str) and not value.strip())


def _layout(ws: Any) -> _Layout:
    table = next(iter(ws.tables.values()), None)
    header_row = 1
    if table is not None:
        header_row = int(re.sub(r"\D", "", table.ref.split(":")[0]) or 1)
    width = ws.max_column or 0
    # Trim trailing empty columns of the header.
    while width > 0 and _is_blank(ws.cell(row=header_row, column=width).value):
        width -= 1
    last = ws.max_row or header_row
    totals_row: int | None = None
    while last > header_row and all(
        _is_blank(ws.cell(row=last, column=c).value) for c in range(1, width + 1)
    ):
        last -= 1
    if last > header_row:
        label = [
            ws.cell(row=last, column=c).value
            for c in range(1, width + 1)
            if isinstance(ws.cell(row=last, column=c).value, str)
        ]
        has_sum = any(
            isinstance(ws.cell(row=last, column=c).value, str)
            and re.match(
                r"(?i)=\s*(SUBTOTAL|SUM|AVERAGE)\(", str(ws.cell(row=last, column=c).value)
            )
            for c in range(1, width + 1)
        )
        if has_sum and any(re.fullmatch(r"(?i)\s*(grand\s+)?totals?\s*", str(v)) for v in label):
            totals_row = last
            last -= 1
    if table is not None:
        last = min(last, int(re.sub(r"\D", "", table.ref.split(":")[1]) or last)) or last
    return _Layout(header_row, header_row + 1, last, width, totals_row, table)


def _headers(ws: Any, lay: _Layout) -> list[str]:
    return [str(ws.cell(row=lay.header_row, column=c).value or "") for c in range(1, lay.width + 1)]


def _column_index(ws: Any, lay: _Layout, ref: Any) -> int:
    """The 1-based column a header name or letter names."""
    text = str(ref or "").strip()
    headers = [h.strip().lower() for h in _headers(ws, lay)]
    if text.lower() in headers:
        return headers.index(text.lower()) + 1
    if re.fullmatch(r"[A-Za-z]{1,3}", text):
        from openpyxl.utils import column_index_from_string

        index = column_index_from_string(text.upper())
        if 1 <= index <= lay.width:
            return int(index)
    raise SheetOpError(
        f"There is no column '{text}'. The columns are: {', '.join(_headers(ws, lay))}."
    )


def _sheet(wb: Workbook, op: Mapping[str, Any]) -> Any:
    name = str(op.get("sheet") or "")
    if not name:
        return wb[wb.sheetnames[0]]  # no sheet named: the first one
    if name not in wb.sheetnames:
        raise SheetOpError(
            f"The sheet '{name}' does not exist. Sheets: {', '.join(wb.sheetnames)}."
        )
    return wb[name]


# ------------------------------------------------------------------------------- reference shifts
def _shift_formula(formula: str, here: str, target: str, at_col: int, delta: int) -> str:
    """``formula`` with references to columns ``>= at_col`` of the sheet ``target`` moved by
    ``delta`` (``here`` is the sheet that holds the formula; unqualified references mean it)."""

    def swap(match: re.Match[str]) -> str:
        prefix, d1, letters, d2, digits = match.groups()
        sheet = here if not prefix else prefix[:-1].strip("'").replace("''", "'")
        if sheet != target:
            return match.group(0)
        from openpyxl.utils import column_index_from_string

        col = column_index_from_string(letters)
        if col < at_col:
            return match.group(0)
        if delta < 0 and col == at_col:
            return "#REF!"
        return f"{prefix or ''}{d1}{get_column_letter(col + delta)}{d2}{digits}"

    return _REF.sub(swap, formula)


def _shift_workbook(wb: Workbook, target: Any, at_col: int, delta: int) -> int:
    changed = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                value = cell.value
                if isinstance(value, str) and value.startswith("="):
                    new = _shift_formula(value, ws.title, target.title, at_col, delta)
                    if new != value:
                        cell.value = new
                        changed += 1
    return changed


def _used_by_formulas(wb: Workbook, target: Any, col: int) -> int:
    from openpyxl.utils import column_index_from_string

    count = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                value = cell.value
                if not (isinstance(value, str) and value.startswith("=")):
                    continue
                for match in _REF.finditer(value):
                    prefix, _d1, letters, _d2, _digits = match.groups()
                    sheet = ws.title if not prefix else prefix[:-1].strip("'")
                    if sheet == target.title and column_index_from_string(letters) == col:
                        if not (ws is target and cell.column == col):
                            count += 1
    return count


# ------------------------------------------------------------------------- ranges after row changes
def _resize_ranges(ws: Any, lay: _Layout, new_last: int) -> None:
    """Make the table, filter, validation and conditional-format ranges that ended at the old
    last data row end at ``new_last``."""
    old_last = lay.last
    letter_end = get_column_letter(max(lay.width, 1))
    if lay.table is not None:
        lay.table.ref = f"A{lay.header_row}:{letter_end}{new_last}"
        if lay.table.autoFilter is not None:
            lay.table.autoFilter.ref = lay.table.ref
    elif ws.auto_filter.ref:
        ws.auto_filter.ref = f"A{lay.header_row}:{letter_end}{new_last}"

    def fix(ref: str) -> str:
        return re.sub(
            r"([A-Z]{1,3})(\d+):([A-Z]{1,3})(\d+)",
            lambda m: (
                f"{m.group(1)}{m.group(2)}:{m.group(3)}{new_last}"
                if int(m.group(4)) == old_last
                else m.group(0)
            ),
            ref,
        )

    for validation in ws.data_validations.dataValidation:
        validation.sqref = type(validation.sqref)(fix(str(validation.sqref)))
    rebuilt = ConditionalFormattingList()
    for cf in ws.conditional_formatting:
        for rule in cf.rules:
            rebuilt.add(fix(str(cf.sqref)), rule)
    ws.conditional_formatting = rebuilt


def _rewrite_totals(ws: Any, lay: _Layout, new_last: int, totals_row: int) -> int:
    """Point the totals-row formulas at rows ``first`` to ``new_last``."""
    changed = 0
    for col in range(1, lay.width + 1):
        cell = ws.cell(row=totals_row, column=col)
        value = cell.value
        if isinstance(value, str) and re.match(r"(?i)=\s*(SUBTOTAL|SUM|AVERAGE)\(", value):
            letter = get_column_letter(col)
            new = re.sub(
                rf"\$?{letter}\$?\d+:\$?{letter}\$?\d+",
                f"{letter}{lay.first}:{letter}{new_last}",
                value,
            )
            if new != value:
                cell.value = new
                changed += 1
    return changed


# ------------------------------------------------------------------------------------ row access
def _read_rows(ws: Any, lay: _Layout) -> list[list[Any]]:
    return [
        [ws.cell(row=r, column=c).value for c in range(1, lay.width + 1)]
        for r in range(lay.first, lay.last + 1)
    ]


def _write_rows(ws: Any, lay: _Layout, rows: list[list[Any]]) -> int:
    """Write ``rows`` from the first data row; formulas move with their row (translated), the
    cell styles stay on their position. The totals row follows the data. Returns the new last
    data row."""
    for r in range(lay.first, lay.last + 1):
        for c in range(1, lay.width + 1):
            ws.cell(row=r, column=c).value = None
    for offset, row in enumerate(rows):
        for c, value in enumerate(row, start=1):
            ws.cell(row=lay.first + offset, column=c).value = value
    new_last = lay.first + len(rows) - 1 if rows else lay.header_row
    old_total = lay.totals_row
    if old_total is not None and old_total != new_last + 1:
        for c in range(1, lay.width + 1):
            src = ws.cell(row=old_total, column=c)
            dst = ws.cell(row=new_last + 1, column=c)
            dst.value = src.value
            dst._style = copy(src._style)
            src.value = None
    # Rows freed at the bottom lose their banding so no empty striped rows remain.
    for r in range(new_last + 2, (old_total or lay.last) + 1):
        for c in range(1, lay.width + 1):
            ws.cell(row=r, column=c)._style = StyleArray()
    return new_last


def _extend_formula_ranges(wb: Workbook, target: Any, old_last: int, new_last: int) -> int:
    """Formulas anywhere (the Summary sheet, the totals row) whose range ends at ``old_last`` of
    ``target`` now end at ``new_last``."""
    if old_last == new_last:
        return 0
    pattern = re.compile(
        r"((?:'[^']+'|[A-Za-z0-9_]+)!)?(\$?[A-Z]{1,3}\$?)(\d+):(\$?[A-Z]{1,3}\$?)(\d+)"
    )
    changed = 0
    for ws in wb.worksheets:
        for row in ws.iter_rows():
            for cell in row:
                value = cell.value
                if not (isinstance(value, str) and value.startswith("=")):
                    continue

                def swap(m: re.Match[str], here: str = ws.title) -> str:
                    sheet = here if not m.group(1) else m.group(1)[:-1].strip("'")
                    if sheet == target.title and int(m.group(5)) == old_last:
                        return f"{m.group(1) or ''}{m.group(2)}{m.group(3)}:{m.group(4)}{new_last}"
                    return m.group(0)

                new = pattern.sub(swap, value)
                if new != value:
                    cell.value = new
                    changed += 1
    return changed


def _translate_rows(rows: list[list[Any]], origins: list[int], first: int) -> list[list[Any]]:
    """Formulas written for row ``origins[i]`` moved to row ``first + i``."""
    out: list[list[Any]] = []
    for offset, (row, origin) in enumerate(zip(rows, origins, strict=True)):
        dest = first + offset
        new_row: list[Any] = []
        for c, value in enumerate(row, start=1):
            if isinstance(value, str) and value.startswith("=") and origin != dest:
                try:
                    value = Translator(
                        value, origin=f"{get_column_letter(c)}{origin}"
                    ).translate_formula(f"{get_column_letter(c)}{dest}")
                except Exception:
                    pass
            new_row.append(value)
        out.append(new_row)
    return out


# ---------------------------------------------------------------------------------- operations
def _need_formula(text: Any, what: str) -> str:
    formula = str(text or "")
    if not formula.startswith("=") or _BAD_FORMULA.search(formula) or len(formula) > 500:
        raise SheetOpError(f"{what}: that formula is not allowed (it must start with '=').")
    return formula


def _row_formula(template: str, row: int) -> str:
    return template.replace("{row}", str(row))


def _op_add_column(wb: Workbook, op: Mapping[str, Any], out: OpsOutcome) -> None:
    ws = _sheet(wb, op)
    lay = _layout(ws)
    name = str(op.get("name") or "").strip()
    if not name:
        raise SheetOpError("add_column needs a 'name'.")
    if name.lower() in [h.lower() for h in _headers(ws, lay)]:
        raise SheetOpError(f"A column named '{name}' already exists.")
    if lay.width + 1 > MAX_COLUMNS:
        raise SheetOpError(f"A sheet may have at most {MAX_COLUMNS} columns.")
    position = int(op.get("position") or lay.width + 1)
    position = max(1, min(position, lay.width + 1))
    if position <= lay.width:
        ws.insert_cols(position)
        _shift_workbook(wb, ws, position, 1)
        # openpyxl does not move column widths; shift them.
        for col in range(lay.width + 1, position, -1):
            prev = ws.column_dimensions[get_column_letter(col - 1)].width
            ws.column_dimensions[get_column_letter(col)].width = prev
    lay2 = _Layout(lay.header_row, lay.first, lay.last, lay.width + 1, lay.totals_row, lay.table)
    template_col = position - 1 if position > 1 else position + 1
    head = ws.cell(row=lay.header_row, column=position, value=name)
    head._style = copy(ws.cell(row=lay.header_row, column=template_col)._style)
    formula = op.get("formula")
    value = op.get("value")
    for r in range(lay.first, lay.last + 1):
        cell = ws.cell(row=r, column=position)
        cell._style = copy(ws.cell(row=r, column=template_col)._style)
        if formula:
            cell.value = _row_formula(_need_formula(formula, "add_column"), r)
        elif value is not None:
            cell.value = value
    if op.get("number_format"):
        for r in range(lay.first, lay.last + 1):
            ws.cell(row=r, column=position).number_format = str(op["number_format"])
    ws.column_dimensions[get_column_letter(position)].width = max(12, min(len(name) + 4, 40))
    if lay.table is not None:
        from openpyxl.worksheet.table import TableColumn

        lay.table.ref = f"A{lay.header_row}:{get_column_letter(lay2.width)}{lay.last}"
        lay.table.autoFilter.ref = lay.table.ref
        lay.table.tableColumns = [
            TableColumn(id=i + 1, name=h) for i, h in enumerate(_headers(ws, lay2))
        ]
    elif ws.auto_filter.ref:
        ws.auto_filter.ref = f"A{lay.header_row}:{get_column_letter(lay2.width)}{lay.last}"
    filled = (lay.last - lay.first + 1) if (formula or value is not None) else 0
    out.lines.append(
        f"add_column '{name}' at column {get_column_letter(position)}: {filled} cells filled"
    )
    out.changes += 1 + filled
    out.counts.append({"op": "add_column", "column": name, "cells": filled})


def _op_rename_column(wb: Workbook, op: Mapping[str, Any], out: OpsOutcome) -> None:
    ws = _sheet(wb, op)
    lay = _layout(ws)
    col = _column_index(ws, lay, op.get("column"))
    new = str(op.get("new_name") or "").strip()
    if not new:
        raise SheetOpError("rename_column needs a 'new_name'.")
    old = ws.cell(row=lay.header_row, column=col).value
    ws.cell(row=lay.header_row, column=col).value = new
    if lay.table is not None:
        for tc in lay.table.tableColumns:
            if tc.name == old:
                tc.name = new
    out.lines.append(f"rename_column: '{old}' -> '{new}'")
    out.changes += 1
    out.counts.append({"op": "rename_column", "cells": 1})


def _op_delete_column(wb: Workbook, op: Mapping[str, Any], out: OpsOutcome) -> None:
    ws = _sheet(wb, op)
    lay = _layout(ws)
    col = _column_index(ws, lay, op.get("column"))
    if lay.width <= 1:
        raise SheetOpError("The last column cannot be deleted.")
    used = _used_by_formulas(wb, ws, col)
    if used:
        raise SheetOpError(
            f"Column '{ws.cell(row=lay.header_row, column=col).value}' is used by {used} formula "
            "cell(s). Change or delete those formulas first; nothing was deleted."
        )
    name = ws.cell(row=lay.header_row, column=col).value
    widths = {c: ws.column_dimensions[get_column_letter(c)].width for c in range(1, lay.width + 1)}
    ws.delete_cols(col)
    shifted = _shift_workbook(wb, ws, col + 1, -1)
    for c in range(col, lay.width):
        ws.column_dimensions[get_column_letter(c)].width = widths[c + 1]
    new_width = lay.width - 1
    end = get_column_letter(new_width)
    if lay.table is not None:
        from openpyxl.worksheet.table import TableColumn

        lay.table.ref = f"A{lay.header_row}:{end}{lay.last}"
        lay.table.autoFilter.ref = lay.table.ref
        lay.table.tableColumns = [
            TableColumn(id=i + 1, name=str(ws.cell(row=lay.header_row, column=i + 1).value))
            for i in range(new_width)
        ]
    elif ws.auto_filter.ref:
        ws.auto_filter.ref = f"A{lay.header_row}:{end}{lay.last}"
    cells = lay.last - lay.first + 2
    out.lines.append(
        f"delete_column '{name}': {cells} cells removed, {shifted} formulas re-pointed"
    )
    out.changes += cells
    out.counts.append({"op": "delete_column", "column": name, "cells": cells})
    out.warnings.append(
        "Data validation and conditional formats of the columns after it may need a check."
    )


def _coerce(value: Any) -> Any:
    return value


def _op_add_rows(wb: Workbook, op: Mapping[str, Any], out: OpsOutcome) -> None:
    ws = _sheet(wb, op)
    lay = _layout(ws)
    raw = op.get("rows")
    if not isinstance(raw, list) or not raw:
        raise SheetOpError("add_rows needs 'rows' (a list of rows).")
    if len(raw) > MAX_ADDED_ROWS:
        raise SheetOpError(f"add_rows takes at most {MAX_ADDED_ROWS} rows per call.")
    if (lay.last - lay.first + 1) + len(raw) > MAX_ROWS:
        raise SheetOpError(f"A sheet may have at most {MAX_ROWS:,} rows.")
    added: list[list[Any]] = []
    template_row = lay.last if lay.last >= lay.first else lay.first
    for offset, item in enumerate(raw):
        if not isinstance(item, list):
            raise SheetOpError("Every row in add_rows must be a list of cells.")
        row = [_coerce(v) for v in item[: lay.width]] + [None] * max(0, lay.width - len(item))
        dest = lay.last + 1 + offset
        for c in range(lay.width):
            tv = ws.cell(row=template_row, column=c + 1).value
            if isinstance(tv, str) and tv.startswith("=") and _is_blank(row[c]):
                try:
                    row[c] = Translator(
                        tv, origin=f"{get_column_letter(c + 1)}{template_row}"
                    ).translate_formula(f"{get_column_letter(c + 1)}{dest}")
                except Exception:
                    pass
        added.append(row)
    new_last = lay.last + len(added)
    # Make room for the totals row, then style the new rows like the data above (banding).
    if lay.totals_row is not None:
        ws.insert_rows(lay.last + 1, amount=len(added))
    for offset, row in enumerate(added):
        r = lay.last + 1 + offset
        for c in range(1, lay.width + 1):
            cell = ws.cell(row=r, column=c)
            cell.value = row[c - 1]
            cell._style = copy(ws.cell(row=lay.first + ((r - lay.first) % 2), column=c)._style)
    if lay.totals_row is not None:
        _rewrite_totals(ws, lay, new_last, lay.totals_row + len(added))
    _resize_ranges(ws, lay, new_last)
    _extend_formula_ranges(wb, ws, lay.last, new_last)
    out.lines.append(f"add_rows: {len(added)} rows added after row {lay.last}")
    out.changes += len(added)
    out.counts.append({"op": "add_rows", "rows": len(added)})


def _sort_key(value: Any) -> tuple[int, Any]:
    if _is_blank(value):
        return (2, 0)
    if isinstance(value, bool):
        return (1, str(value))
    if isinstance(value, int | float):
        return (0, float(value))
    if isinstance(value, datetime | date):
        return (
            0,
            datetime.combine(value, datetime.min.time()).timestamp()
            if isinstance(value, date) and not isinstance(value, datetime)
            else value.timestamp(),
        )
    return (1, str(value).lower())


def _effective(row: list[Any], origin: int, col: int) -> Any:
    """The value a cell shows: a simple row formula is computed from the numbers of its row."""
    value = row[col - 1]
    if isinstance(value, str) and value.startswith("="):
        cells = {
            get_column_letter(i + 1): v
            for i, v in enumerate(row)
            if isinstance(v, int | float) and not isinstance(v, bool)
        }
        result = eval_row_formula(value, origin, cells)
        if result is None:
            raise SheetOpError(
                "That column holds formulas that cannot be evaluated here, so it cannot be "
                "sorted or filtered by value. Nothing was changed."
            )
        return result
    return value


def _op_sort(wb: Workbook, op: Mapping[str, Any], out: OpsOutcome) -> None:
    ws = _sheet(wb, op)
    lay = _layout(ws)
    col = _column_index(ws, lay, op.get("by"))
    descending = bool(op.get("descending"))
    rows = _read_rows(ws, lay)
    origins = list(range(lay.first, lay.last + 1))
    # A formula cell is sorted by the value Python computes for it (simple row formulas only);
    # blanks always last.
    values = [_effective(row, origin, col) for row, origin in zip(rows, origins, strict=True)]
    keyed = [
        (row, origin, v)
        for (row, origin), v in zip(zip(rows, origins, strict=True), values, strict=True)
    ]
    present = [x for x in keyed if not _is_blank(x[2])]
    blank = [x for x in keyed if _is_blank(x[2])]
    kinds = {_sort_key(x[2])[0] for x in present}
    if len(kinds) > 1:
        raise SheetOpError(
            f"Column '{ws.cell(row=lay.header_row, column=col).value}' mixes numbers and text, so "
            "it cannot be sorted safely. Convert it first (text_to_number). Nothing was changed."
        )
    present.sort(key=lambda x: _sort_key(x[2]), reverse=descending)
    ordered = present + blank
    new_rows = _translate_rows([x[0] for x in ordered], [x[1] for x in ordered], lay.first)
    _write_rows(ws, lay, new_rows)
    if lay.totals_row is not None:
        _rewrite_totals(ws, lay, lay.last, lay.totals_row)
    moved = sum(1 for i, x in enumerate(ordered) if x[1] != lay.first + i)
    out.lines.append(
        f"sort by '{ws.cell(row=lay.header_row, column=col).value}' "
        f"{'descending' if descending else 'ascending'}: {len(ordered)} rows ordered, {moved} moved"
    )
    out.changes += moved
    out.counts.append({"op": "sort", "rows": len(ordered), "moved": moved})


def _matches(value: Any, operator: str, target: Any) -> bool:
    if operator == "contains":
        return str(target).lower() in str(value if value is not None else "").lower()
    if _is_blank(value):
        return operator == "ne"
    try:
        left: Any = float(value) if not isinstance(value, str) else float(value.replace(",", ""))
        right: Any = (
            float(target) if not isinstance(target, str) else float(str(target).replace(",", ""))
        )
    except (TypeError, ValueError):
        left, right = str(value).lower(), str(target).lower()
    verdicts: dict[str, bool] = {
        "eq": left == right,
        "ne": left != right,
        "gt": left > right,
        "ge": left >= right,
        "lt": left < right,
        "le": left <= right,
    }
    return verdicts[operator]


def _op_filter_copy(wb: Workbook, op: Mapping[str, Any], out: OpsOutcome) -> None:
    ws = _sheet(wb, op)
    lay = _layout(ws)
    col = _column_index(ws, lay, op.get("column"))
    operator = str(op.get("operator") or "eq")
    if operator not in FILTER_OPERATORS:
        raise SheetOpError(f"operator must be one of: {', '.join(FILTER_OPERATORS)}.")
    new_name = (
        re.sub(r"[\[\]:*?/\\]", " ", str(op.get("new_sheet") or "Filtered")).strip()[:31]
        or "Filtered"
    )
    base, n = new_name, 2
    while new_name in wb.sheetnames:
        new_name = f"{base[:27]} ({n})"
        n += 1
    target = wb.create_sheet(new_name)
    for c in range(1, lay.width + 1):
        src = ws.cell(row=lay.header_row, column=c)
        dst = target.cell(row=1, column=c, value=src.value)
        dst._style = copy(src._style)
        target.column_dimensions[get_column_letter(c)].width = ws.column_dimensions[
            get_column_letter(c)
        ].width
    kept = 0
    for r in range(lay.first, lay.last + 1):
        row_values = [ws.cell(row=r, column=c).value for c in range(1, lay.width + 1)]
        value = _effective(row_values, r, col)
        if not _matches(value, operator, op.get("value")):
            continue
        kept += 1
        for c in range(1, lay.width + 1):
            src = ws.cell(row=r, column=c)
            cell_value = src.value
            if isinstance(cell_value, str) and cell_value.startswith("="):
                try:
                    cell_value = Translator(cell_value, origin=src.coordinate).translate_formula(
                        f"{get_column_letter(c)}{kept + 1}"
                    )
                except Exception:
                    pass
            dst = target.cell(row=kept + 1, column=c, value=cell_value)
            dst._style = copy(src._style)
    target.freeze_panes = "A2"
    out.lines.append(
        f"filter_copy: {kept} of {lay.last - lay.first + 1} rows where "
        f"'{ws.cell(row=lay.header_row, column=col).value}' {operator} {op.get('value')!r} "
        f"copied to the new sheet '{new_name}' (the original sheet is unchanged)"
    )
    out.changes += kept
    out.counts.append({"op": "filter_copy", "rows": kept, "sheet": new_name})


def _op_dedupe(wb: Workbook, op: Mapping[str, Any], out: OpsOutcome) -> None:
    ws = _sheet(wb, op)
    lay = _layout(ws)
    cols_raw = op.get("columns")
    cols = (
        [_column_index(ws, lay, c) for c in cols_raw]
        if isinstance(cols_raw, list) and cols_raw
        else list(range(1, lay.width + 1))
    )
    rows = _read_rows(ws, lay)
    origins = list(range(lay.first, lay.last + 1))
    seen: set[tuple[str, ...]] = set()
    keep: list[tuple[list[Any], int]] = []
    for row, origin in zip(rows, origins, strict=True):
        key = tuple(
            str(row[c - 1]).strip().lower() if not _is_blank(row[c - 1]) else "" for c in cols
        )
        if key in seen:
            continue
        seen.add(key)
        keep.append((row, origin))
    removed = len(rows) - len(keep)
    if removed == 0:
        out.lines.append("dedupe: no duplicate rows found, nothing changed")
        out.counts.append({"op": "dedupe", "removed": 0})
        return
    new_rows = _translate_rows([k[0] for k in keep], [k[1] for k in keep], lay.first)
    new_last = _write_rows(ws, lay, new_rows)
    if lay.totals_row is not None:
        _rewrite_totals(ws, lay, new_last, new_last + 1)
    _resize_ranges(ws, lay, new_last)
    _extend_formula_ranges(wb, ws, lay.last, new_last)
    out.lines.append(f"dedupe: {removed} duplicate rows removed, {len(keep)} kept")
    out.changes += removed
    out.counts.append({"op": "dedupe", "removed": removed, "kept": len(keep)})


def _op_fill_formula_down(wb: Workbook, op: Mapping[str, Any], out: OpsOutcome) -> None:
    ws = _sheet(wb, op)
    lay = _layout(ws)
    col = _column_index(ws, lay, op.get("column"))
    template = _need_formula(op.get("formula"), "fill_formula_down")
    count = 0
    for r in range(lay.first, lay.last + 1):
        ws.cell(row=r, column=col).value = _row_formula(template, r)
        count += 1
    out.lines.append(
        f"fill_formula_down: '{ws.cell(row=lay.header_row, column=col).value}' = {template} "
        f"in {count} rows"
    )
    out.changes += count
    out.counts.append({"op": "fill_formula_down", "cells": count})


def _op_recalc_totals(wb: Workbook, op: Mapping[str, Any], out: OpsOutcome) -> None:
    ws = _sheet(wb, op)
    lay = _layout(ws)
    if lay.totals_row is None:
        raise SheetOpError("This sheet has no totals row to recalculate.")
    changed = _rewrite_totals(ws, lay, lay.last, lay.totals_row)
    _resize_ranges(ws, lay, lay.last)
    out.lines.append(
        f"recalc_totals: {changed} totals formulas now cover rows {lay.first} to {lay.last}"
    )
    out.changes += changed
    out.counts.append({"op": "recalc_totals", "cells": changed})


def _op_find_replace(wb: Workbook, op: Mapping[str, Any], out: OpsOutcome) -> None:
    find = str(op.get("find") or "")
    if not find:
        raise SheetOpError("find_replace needs 'find'.")
    replace = str(op.get("replace") if op.get("replace") is not None else "")
    match_case = bool(op.get("match_case"))
    whole = bool(op.get("whole_cell"))
    sheets = [_sheet(wb, op)] if op.get("sheet") else list(wb.worksheets)
    pattern = re.compile(
        re.escape(find) if not whole else rf"^{re.escape(find)}$",
        0 if match_case else re.IGNORECASE,
    )
    count = 0
    for ws in sheets:
        lay = _layout(ws)
        columns: list[int] | None = None
        if op.get("column"):
            columns = [_column_index(ws, lay, op["column"])]
        for row in ws.iter_rows():
            for cell in row:
                if columns and cell.column not in columns:
                    continue
                value = cell.value
                if not isinstance(value, str) or value.startswith("="):
                    continue
                new = pattern.sub(lambda _m: replace, value)
                if new != value:
                    cell.value = new
                    count += 1
    out.lines.append(f"find_replace: '{find}' -> '{replace}' in {count} cells")
    out.changes += count
    out.counts.append({"op": "find_replace", "cells": count})


def _op_text_to_number(wb: Workbook, op: Mapping[str, Any], out: OpsOutcome) -> None:
    ws = _sheet(wb, op)
    lay = _layout(ws)
    cols = (
        [_column_index(ws, lay, op["column"])]
        if op.get("column")
        else list(range(1, lay.width + 1))
    )
    count = 0
    for col in cols:
        for r in range(lay.first, lay.last + 1):
            cell = ws.cell(row=r, column=col)
            value = cell.value
            if isinstance(value, str) and _NUMBERISH.match(value):
                text = value.strip().replace(",", "")
                cell.value = float(text) if "." in text else int(text)
                if cell.number_format == "General":
                    cell.number_format = "#,##0.00" if "." in text else "#,##0"
                cell.alignment = Alignment(horizontal="right", vertical=cell.alignment.vertical)
                count += 1
    out.lines.append(f"text_to_number: {count} text cells converted to numbers")
    out.changes += count
    out.counts.append({"op": "text_to_number", "cells": count})


_HANDLERS = {
    "add_column": _op_add_column,
    "rename_column": _op_rename_column,
    "delete_column": _op_delete_column,
    "add_rows": _op_add_rows,
    "sort": _op_sort,
    "filter_copy": _op_filter_copy,
    "dedupe": _op_dedupe,
    "fill_formula_down": _op_fill_formula_down,
    "recalc_totals": _op_recalc_totals,
    "find_replace": _op_find_replace,
    "text_to_number": _op_text_to_number,
}


def apply_sheet_ops(src: Path, ops: Sequence[Mapping[str, Any]], dest: Path) -> OpsOutcome:
    """Apply ``ops`` (each ``{"op": <SHEET_OPS>, "sheet": ..., ...}``) to a copy of ``src`` saved
    as ``dest``. Raises :class:`SheetOpError` (nothing is saved) when one cannot be applied."""
    if not ops:
        raise SheetOpError("There is nothing to change.")
    if len(ops) > MAX_OPS:
        raise SheetOpError(f"At most {MAX_OPS} operations per call.")
    wb = load_workbook(str(src))
    out = OpsOutcome()
    try:
        if any(getattr(ws, "_charts", None) or getattr(ws, "_images", None) for ws in wb):
            out.warnings.append(
                "The original has charts or images; the new version may not keep them."
            )
        for item in ops:
            name = str(item.get("op") or "")
            handler = _HANDLERS.get(name)
            if handler is None:
                raise SheetOpError(
                    f"Unknown operation '{name}'. Use one of: {', '.join(SHEET_OPS)}."
                )
            handler(wb, item, out)
        wb.calculation.fullCalcOnLoad = True
        wb.save(str(dest))
    finally:
        wb.close()
    return out


# ------------------------------------------------------------------------------------ summary
def _kind_of(values: list[Any]) -> str:
    present = [v for v in values if not _is_blank(v)]
    if not present:
        return "empty"
    kinds: Counter[str] = Counter()
    for v in present:
        if isinstance(v, bool):
            kinds["boolean"] += 1
        elif isinstance(v, int | float):
            kinds["number"] += 1
        elif isinstance(v, datetime | date):
            kinds["date"] += 1
        elif isinstance(v, str) and v.startswith("="):
            kinds["formula"] += 1
        elif isinstance(v, str) and _NUMBERISH.match(v):
            kinds["number_as_text"] += 1
        else:
            kinds["text"] += 1
    top, n = kinds.most_common(1)[0]
    return top if n == len(present) else f"mixed ({', '.join(sorted(kinds))})"


def _clip(value: Any) -> Any:
    if isinstance(value, datetime):
        return (
            value.date().isoformat()
            if value.time().isoformat() == "00:00:00"
            else value.isoformat()
        )
    if isinstance(value, date):
        return value.isoformat()
    if isinstance(value, str) and len(value) > MAX_SUMMARY_CELL_CHARS:
        return value[:MAX_SUMMARY_CELL_CHARS] + "..."
    return value


def sheet_summary(path: Path) -> dict[str, Any]:
    """A bounded, read-only description of a workbook (see the module docstring)."""
    try:
        wb = load_workbook(str(path))
    except Exception as error:
        raise SheetOpError(
            f"The file could not be opened as a spreadsheet: {type(error).__name__}."
        ) from None
    try:
        sheets: list[dict[str, Any]] = []
        for ws in wb.worksheets[:MAX_SUMMARY_SHEETS]:
            lay = _layout(ws)
            shown = min(lay.width, MAX_SUMMARY_COLUMNS)
            headers = _headers(ws, lay)[:shown]
            rows = _read_rows(ws, lay) if lay.last >= lay.first else []
            columns: list[dict[str, Any]] = []
            for c in range(shown):
                col_values = [r[c] for r in rows]
                nulls = sum(1 for v in col_values if _is_blank(v))
                columns.append(
                    {
                        "name": headers[c],
                        "type": _kind_of(col_values),
                        "empty": nulls,
                        "format": ws.cell(row=lay.first, column=c + 1).number_format
                        if rows
                        else None,
                    }
                )
            counted = Counter(
                tuple(str(v) for v in r[:shown]) for r in rows if any(not _is_blank(v) for v in r)
            )
            sheets.append(
                {
                    "name": ws.title,
                    "dimensions": ws.dimensions,
                    "header_row": lay.header_row,
                    "data_rows": len(rows),
                    "columns_total": lay.width,
                    "columns_shown": shown,
                    "totals_row": lay.totals_row,
                    "table": lay.table.displayName if lay.table is not None else None,
                    "freeze_panes": ws.freeze_panes,
                    "charts": len(getattr(ws, "_charts", []) or []),
                    "header": headers,
                    "first_rows": [[_clip(v) for v in r[:shown]] for r in rows[:MAX_SUMMARY_ROWS]],
                    "columns": columns,
                    "duplicate_rows": sum(n - 1 for n in counted.values() if n > 1),
                }
            )
        result: dict[str, Any] = {
            "sheets": sheets,
            "sheet_count": len(wb.sheetnames),
            "sheet_names": wb.sheetnames[:MAX_SUMMARY_SHEETS],
        }
        if len(wb.sheetnames) > MAX_SUMMARY_SHEETS:
            result["note"] = f"Only the first {MAX_SUMMARY_SHEETS} sheets are described."
        return result
    finally:
        wb.close()
