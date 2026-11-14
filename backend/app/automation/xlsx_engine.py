"""XLSX spreadsheet generation engine.

The model sends sheets as text (``headers`` and rows of strings). This engine makes them a usable,
designed workbook without any further instruction:

* text that is a number, a currency amount, a percentage or an ISO date becomes a real cell value
  with a matching number format, per column (a column is typed only when nearly all of its cells
  agree; identifiers such as ids, codes, phone numbers and zip codes stay text);
* a styled header row (fill from the palette, contrast-safe text), borders, banded rows, column
  widths fitted to the content, wrapped long text, numbers right-aligned;
* the header row is frozen, an auto filter covers the data, the page prints landscape on one page
  width with the header repeated;
* one key numeric column gets conditional formatting (data bars, or a colour scale for
  percentages) and negative numbers are red;
* a small table with a text label column and numeric columns gets a chart beside it.

A real dataset (sheets built from generators, or from 10 or more rows) is also made a working
table: an Excel Table object with banded style (so filters and sorting work), a TOTAL row of
SUBTOTAL formulas under all the data, data validation (lists for category columns, number ranges),
red negatives, a "Summary" sheet of live formulas (COUNTIFS, SUMIFS, AVERAGEIFS, a top-N by
LARGE/INDEX/MATCH, per-month totals) with titled charts, and an "About this data" sheet when the
content is generated sample data. Formulas carry no cached values (openpyxl cannot write them), so
the workbook asks the viewer to recalculate on open; ``SheetBuild`` carries the values Python
expects so a test or a verifier can compare. Big tables are described, not listed: see
``app.automation.sheet_gen`` (row_count, seed, per-column generators).

``style`` may carry ``palette.primary`` / ``palette.accent`` (the design the file was made with);
without it the colours follow the kind of data (``auto_design.pick_tone``). Explicit
``number_formats`` and ``formulas`` of a sheet win over everything inferred.
"""

from __future__ import annotations

import math
import re
from collections.abc import Mapping
from copy import copy
from dataclasses import dataclass, field
from datetime import date, datetime
from pathlib import Path
from typing import Any

from openpyxl import Workbook, load_workbook
from openpyxl.chart import BarChart, LineChart, Reference
from openpyxl.formatting.rule import CellIsRule, ColorScaleRule, DataBarRule
from openpyxl.styles import Alignment, Border, Font, PatternFill, Side
from openpyxl.utils import get_column_letter
from openpyxl.worksheet.datavalidation import DataValidation
from openpyxl.worksheet.properties import PageSetupProperties
from openpyxl.worksheet.table import Table, TableStyleInfo

from app.automation.auto_design import pick_tone, xlsx_colors
from app.automation.design import best_on, mix
from app.automation.sheet_gen import (
    DEFAULT_SEED,
    MAX_CELLS,
    TOTAL_MODES,
    PreparedSheet,
    SheetSpecError,
    prepare_sheet,
)

BODY_FONT = "Calibri"
MAX_COL_WIDTH = 48
MIN_COL_WIDTH = 10
CHART_MIN_ROWS, CHART_MAX_ROWS = 3, 30
MAX_CHART_SERIES = 3
_BAD_TITLE = re.compile(r"[\[\]:*?/\\]")
_TEXT_ONLY_HEADER = re.compile(
    r"(?i)\b(id|ids|code|zip|pin|pincode|phone|mobile|contact|sku|isbn|ref|reference|account|"
    r"roll|no\.?|number|sr|s\.no|serial)\b"
)
_KEY_HEADER = re.compile(
    r"(?i)(total|revenue|sales|amount|profit|score|price|cost|value|marks|rating|income|"
    r"expense|salary|balance|quantity|qty)"
)
STRUCTURED_MIN_ROWS = 10
"""From this many rows (or with generators) a sheet becomes a table with totals and validation."""
MAX_CATEGORIES = 30
MAX_MONTHS = 36
TOP_N = 10
_NO_TOTAL_HEADER = re.compile(
    r"(?i)\b(price|rate|mrp|unit|year|id|rank|age|pin|zip|code|phone|serial|sr|no|number)\b"
)
_AVG_HEADER = re.compile(r"(?i)(rating|score|avg|average|margin|growth)")
_MONEY_HEADER = re.compile(
    r"(?i)(price|total|amount|revenue|cost|sales|salary|income|expense|profit|fee|balance|mrp|pay)"
)
_INT = re.compile(r"^[+-]?\d+$")
_INT_COMMA = re.compile(r"^[+-]?\d{1,3}(?:,\d{3})+$")
_FLOAT = re.compile(r"^[+-]?(?:\d+|\d{1,3}(?:,\d{3})+)\.\d+$")
_PERCENT = re.compile(r"^([+-]?\d+(?:\.\d+)?)\s?%$")
_CURRENCY = re.compile(
    r"^(?P<neg>-)?\s*(?P<sym>[$€£₹]|Rs\.?|INR|USD|EUR)\s?(?P<neg2>-)?(?P<num>\d[\d,]*(?:\.\d+)?)$"
)
_ISO_DATE = re.compile(r"^(\d{4})-(\d{2})-(\d{2})$")
_SYMBOLS = {"rs": "₹", "rs.": "₹", "inr": "₹", "usd": "$", "eur": "€"}

# openpyxl's rule factories are untyped; they are called through ``Any`` aliases.
_color_scale: Any = ColorScaleRule
_cell_is: Any = CellIsRule
_RED_FONT = Font(color="C00000")
_data_bar: Any = DataBarRule
_THIN = Side(style="thin", color="D9D9D9")
_BORDER = Border(left=_THIN, right=_THIN, top=_THIN, bottom=_THIN)


def _hex(color: str) -> str:
    return color.lstrip("#").upper()


def _parse(raw: Any, header: str) -> tuple[Any, str, str] | None:
    """``(value, kind, number_format)`` for a cell that is a typed value, else ``None``.

    ``kind`` is ``int``, ``float``, ``currency``, ``percent`` or ``date``; a ``=`` formula is
    reported as kind ``formula`` (it fits any typed column)."""
    if isinstance(raw, bool):
        return None
    if isinstance(raw, int | float):
        kind = "int" if isinstance(raw, int) else "float"
        return raw, kind, "#,##0" if kind == "int" else "#,##0.00"
    if isinstance(raw, datetime | date):
        return raw, "date", "dd-mmm-yyyy"
    if not isinstance(raw, str):
        return None
    text = raw.strip()
    if not text:
        return None
    if text.startswith("="):
        return text, "formula", "General"
    if _TEXT_ONLY_HEADER.search(header):
        return None
    if _INT_COMMA.match(text):
        return int(text.replace(",", "")), "int", "#,##0"
    if _INT.match(text):
        # A 4-digit year is not a quantity: no thousands separator.
        plain = len(text.lstrip("+-")) == 4 and re.search(r"(?i)year|yr", header)
        return int(text), "int", "0" if plain else "#,##0"
    if _FLOAT.match(text):
        return float(text.replace(",", "")), "float", "#,##0.00"
    percent = _PERCENT.match(text)
    if percent:
        number = float(percent.group(1))
        return number / 100, "percent", "0.0%" if "." in percent.group(1) else "0%"
    money = _CURRENCY.match(text)
    if money:
        digits = money.group("num").replace(",", "")
        value = float(digits) if "." in digits else int(digits)
        if money.group("neg") or money.group("neg2"):
            value = -value
        symbol = _SYMBOLS.get(money.group("sym").lower(), money.group("sym"))
        decimals = "0.00" if isinstance(value, float) else "0"
        return value, "currency", f'"{symbol}"#,##{decimals};[Red]-"{symbol}"#,##{decimals}'
    iso = _ISO_DATE.match(text)
    if iso:
        try:
            return (
                date(int(iso.group(1)), int(iso.group(2)), int(iso.group(3))),
                "date",
                ("dd-mmm-yyyy"),
            )
        except ValueError:
            return None
    return None


def _column_plan(header: str, cells: list[Any]) -> tuple[str, str, list[Any]]:
    """``(kind, number_format, converted cells)`` for one column.

    The column is typed when at least 85 percent of its filled cells parse as one family; the
    cells that do not parse stay text (for example a "n/a")."""
    parsed = [_parse(c, header) for c in cells]
    filled = [p for c, p in zip(cells, parsed, strict=True) if c not in (None, "")]
    typed = [p for p in filled if p is not None and p[1] != "formula"]
    if not typed or len(typed) + sum(1 for p in filled if p and p[1] == "formula") < 0.85 * len(
        filled
    ):
        return "text", "General", list(cells)
    kinds = {p[1] for p in typed}
    numeric = {"int", "float", "currency"}
    if kinds <= numeric:
        kind = "currency" if "currency" in kinds else ("float" if "float" in kinds else "int")
    elif len(kinds) == 1:
        kind = next(iter(kinds))
    else:
        return "text", "General", list(cells)
    if kind == "currency":
        sample = next(p for p in typed if p[1] == "currency")
        fmt = sample[2]
        if any(p[1] == "float" for p in typed) and "0.00" not in fmt:
            fmt = fmt.replace("#,##0", "#,##0.00")
    elif kind == "int":
        fmt = "#,##0;[Red]-#,##0" if any(p[2] == "#,##0" for p in typed) else "0"
    elif kind == "float":
        fmt = "#,##0.00;[Red]-#,##0.00"
    elif kind == "percent":
        fmt = max((p[2] for p in typed), key=len)
    else:
        fmt = "dd-mmm-yyyy"
    out: list[Any] = []
    for cell, item in zip(cells, parsed, strict=True):
        if item is None:
            out.append(cell)
        elif item[1] == "formula":
            out.append(item[0])
        elif kind == "percent" and item[1] != "percent":
            out.append(cell)
        elif kind in numeric and item[1] not in numeric:
            out.append(cell)
        else:
            out.append(item[0])
    return kind, fmt, out


def _display_len(value: Any, kind: str) -> int:
    if value is None:
        return 0
    if isinstance(value, float):
        return len(f"{value:,.2f}") + 1
    if isinstance(value, int):
        return len(f"{value:,}") + 1
    if isinstance(value, datetime | date):
        return 11
    return max((len(part) for part in str(value).split("\n")), default=0)


def _safe_title(raw: Any, used: set[str], number: int) -> str:
    title = _BAD_TITLE.sub(" ", str(raw or "")).strip()[:31] or f"Sheet{number}"
    base, suffix = title, 2
    while title.lower() in used:
        tag = f" ({suffix})"
        title = base[: 31 - len(tag)] + tag
        suffix += 1
    used.add(title.lower())
    return title


def _chart_columns(kinds: list[str], formats: list[str], headers: list[str]) -> list[int]:
    """The 0-based numeric columns to chart: the key column first, then same-format neighbours."""
    numeric = [i for i, k in enumerate(kinds) if k in ("int", "float", "currency")]
    if not numeric:
        return []
    lead = next((i for i in numeric if _KEY_HEADER.search(headers[i])), numeric[0])
    same = [i for i in numeric if kinds[i] == kinds[lead] or (formats[i] == formats[lead])]
    ordered = [lead, *[i for i in same if i != lead]]
    return ordered[:MAX_CHART_SERIES]


@dataclass
class SheetFacts:
    """What was built for one sheet (data rows exclude the header and the totals row)."""

    title: str
    data_rows: int
    columns: int
    totals_row: int | None = None
    table: str | None = None
    structured: bool = False
    synthetic: bool = False
    generated: bool = False
    # Python's own value of every totals-row cell (header -> number): what a spreadsheet
    # application must compute for the formulas (openpyxl stores no cached values).
    expected_totals: dict[str, float] = field(default_factory=dict)
    notes: list[str] = field(default_factory=list)


@dataclass
class SheetBuild:
    """The result of :func:`build_spreadsheet`: the file and the facts the tool result states."""

    path: Path
    sheets: list[SheetFacts]
    main: SheetFacts
    summary_sheet: str | None = None
    about_sheet: str | None = None
    # Cell address ("Summary!B5") -> the value Python computes for that formula cell.
    expected: dict[str, Any] = field(default_factory=dict)
    # Rows counted on the written file's main sheet (not taken from the spec).
    file_rows: int | None = None
    requested_rows: int | None = None
    # The lowest count the user accepts ("50-100 rows"); ``None``: exactly ``requested_rows``.
    rows_low: int | None = None

    @property
    def synthetic(self) -> bool:
        return any(s.synthetic for s in self.sheets)

    @property
    def shortfall(self) -> bool:
        floor = self.rows_low if self.rows_low is not None else self.requested_rows
        return floor is not None and (self.file_rows or 0) < floor

    @property
    def notes(self) -> list[str]:
        return [n for s in self.sheets for n in s.notes]


def _is_numeric(kind: str) -> bool:
    return kind in ("int", "float", "currency")


def _col_numbers(values: list[Any]) -> list[float | None]:
    return [
        float(v) if isinstance(v, int | float) and not isinstance(v, bool) else None for v in values
    ]


def _total_mode(header: str, kind: str, explicit: str | None) -> str:
    """``sum``, ``average`` or ``none`` for the totals row of a column."""
    if explicit in TOTAL_MODES:
        return explicit
    if kind == "percent":
        return "average"
    if not _is_numeric(kind):
        return "none"
    if _NO_TOTAL_HEADER.search(header):
        return "none"
    if _AVG_HEADER.search(header):
        return "average"
    return "sum"


def _quote_sheet(title: str) -> str:
    return "'" + title.replace("'", "''") + "'"


def _table_name(title: str, used: set[str]) -> str:
    base = re.sub(r"[^A-Za-z0-9_]", "_", title).strip("_") or "Data"
    if not re.match(r"[A-Za-z_]", base):
        base = f"T_{base}"
    name, n = f"tbl_{base}"[:60], 2
    while name.lower() in used:
        name = f"tbl_{base}"[:55] + f"_{n}"
        n += 1
    used.add(name.lower())
    return name


def _style_templates(
    ws: Any,
    start_row: int,
    last_row: int,
    kinds: list[str],
    formats: list[str],
    explicit: Mapping[int, str],
    band_fill: PatternFill,
    body_font: Font,
) -> None:
    """Style the data cells. The first plain and the first banded row of a column are styled
    through the API; every other cell copies that style array (about 40 times faster for big
    sheets and identical in the file)."""
    width = len(kinds)

    def full(cell: Any, col: int, banded: bool) -> None:
        cell.font = body_font
        cell.border = _BORDER
        if banded:
            cell.fill = band_fill
        kind = kinds[col - 1]
        if kind in ("int", "float", "currency", "percent"):
            cell.number_format = formats[col - 1]
            cell.alignment = Alignment(horizontal="right", vertical="top")
        elif kind == "date":
            cell.number_format = formats[col - 1]
            cell.alignment = Alignment(horizontal="center", vertical="top")
        else:
            cell.alignment = Alignment(vertical="top", wrap_text=True)
            if col in explicit:
                cell.number_format = explicit[col]

    templates: dict[tuple[int, bool], Any] = {}
    for offset, row in enumerate(ws.iter_rows(min_row=start_row, max_row=last_row, max_col=width)):
        banded = offset % 2 == 1
        for col, cell in enumerate(row, start=1):
            key = (col, banded)
            if key not in templates:
                full(cell, col, banded)
                templates[key] = cell
            else:
                cell._style = copy(templates[key]._style)


def _distinct(values: list[Any]) -> list[Any]:
    return list(dict.fromkeys(v for v in values if v not in (None, "")))


def _category_column(
    prepared: PreparedSheet, kinds: list[str], columns: list[list[Any]], n: int
) -> int | None:
    """The 0-based text column to group by: a closed list of 2 to 30 repeating values."""
    best: int | None = None
    for index, kind in enumerate(kinds):
        if kind != "text" or n < 6:
            continue
        info = prepared.columns[index]
        if info.kind == "sequence":
            continue
        values = [v for v in columns[index] if v not in (None, "")]
        if len(values) < 0.9 * n:
            continue
        distinct = _distinct(values)
        if not 2 <= len(distinct) <= MAX_CATEGORIES or len(distinct) * 2 > n:
            continue
        if any(not isinstance(v, str) or re.search(r"[*?~]", v) for v in distinct):
            continue
        if info.choices is not None or info.kind in ("name_pool", "choice"):
            return index
        if best is None:
            best = index
    return best


def _label_column(prepared: PreparedSheet, kinds: list[str], columns: list[list[Any]]) -> int:
    """The column that names a row in a top-N list: the first unique text column that is not a
    plain counter (a product name beats its id), else the first column."""
    unique_text = [
        i
        for i, kind in enumerate(kinds)
        if kind == "text"
        and len(_distinct(columns[i])) == len([v for v in columns[i] if v not in (None, "")])
        and any(v not in (None, "") for v in columns[i])
    ]
    for index in unique_text:
        if prepared.columns[index].kind != "sequence":
            return index
    return unique_text[0] if unique_text else 0


def _month_starts(values: list[Any]) -> list[date]:
    days = [v.date() if isinstance(v, datetime) else v for v in values if isinstance(v, date)]
    if not days:
        return []
    first, last = min(days), max(days)
    out: list[date] = []
    year, month = first.year, first.month
    while (year, month) <= (last.year, last.month):
        out.append(date(year, month, 1))
        month += 1
        if month > 12:
            year, month = year + 1, 1
    return out


def _add_summary(
    wb: Workbook,
    main: Any,
    prepared: PreparedSheet,
    kinds: list[str],
    columns: list[list[Any]],
    formats: list[str],
    headers: list[str],
    last_row: int,
    used: set[str],
    primary: str,
    accent: str,
    header_font: Font,
    header_fill: PatternFill,
) -> tuple[str, dict[str, Any]] | None:
    """A "Summary" sheet of live formulas on the data sheet: key figures, totals per category,
    totals per month and a top-N list, each with a chart where it helps. Classic formulas only
    (SUM, COUNTIFS, SUMIFS, AVERAGEIFS, LARGE, INDEX, MATCH, EDATE) so LibreOffice and older Excel
    compute them. Returns the sheet title and the value Python expects in each formula cell."""
    n = last_row - 1
    sum_cols = [
        i
        for i, kind in enumerate(kinds)
        if _is_numeric(kind) and _total_mode(headers[i], kind, prepared.columns[i].total) == "sum"
    ]
    if not sum_cols or n < 5:
        return None
    key = next((i for i in sum_cols if _KEY_HEADER.search(headers[i])), sum_cols[-1])
    value_cols = [key, *[i for i in sum_cols if i != key]][:3]
    numbers = {i: _col_numbers(columns[i]) for i in value_cols}
    ref = _quote_sheet(main.title)

    def rng(index: int) -> str:
        letter = get_column_letter(index + 1)
        return f"{ref}!${letter}$2:${letter}${last_row}"

    title = _safe_title("Summary", used, 0)
    ws = wb.create_sheet(title=title)
    ws.sheet_properties.tabColor = _hex(accent)
    ws.views.sheetView[0].showGridLines = False
    expected: dict[str, Any] = {}
    title_font = Font(name=BODY_FONT, size=14, bold=True, color=_hex(primary))
    label_font = Font(name=BODY_FONT, size=10)
    bold = Font(name=BODY_FONT, size=10, bold=True)
    ws["A1"] = f"Summary of {main.title}"
    ws["A1"].font = title_font
    ws["A2"] = f"Every figure is a formula on the '{main.title}' sheet and updates with it."
    ws["A2"].font = Font(name=BODY_FONT, size=9, italic=True, color="595959")

    def head(row: int, labels: list[str]) -> None:
        for offset, label in enumerate(labels):
            cell = ws.cell(row=row, column=1 + offset, value=label)
            cell.font = header_font
            cell.fill = header_fill
            cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
            cell.border = _BORDER

    def put(coord: str, value: Any, fmt: str | None = None, *, python: Any = None) -> None:
        cell = ws[coord]
        cell.value = value
        cell.font = label_font
        cell.border = _BORDER
        if fmt:
            cell.number_format = fmt
            cell.alignment = Alignment(horizontal="right")
        if python is not None:
            expected[f"{title}!{coord}"] = python

    row = 4
    ws.cell(row=row, column=1, value="Key figures").font = bold
    row += 1
    head(row, ["Measure", "Value"])
    row += 1
    first_col_letter = get_column_letter(1)
    put(f"A{row}", "Rows")
    put(
        f"B{row}",
        f"=COUNTA({ref}!${first_col_letter}$2:${first_col_letter}${last_row})",
        "#,##0",
        python=n,
    )
    row += 1
    for index in value_cols:
        values = [v for v in numbers[index] if v is not None]
        fmt = formats[index]
        for label, func, python in (
            (f"Sum of {headers[index]}", "SUM", math.fsum(values)),
            (f"Average {headers[index]}", "AVERAGE", math.fsum(values) / len(values)),
        ):
            put(f"A{row}", label)
            put(f"B{row}", f"={func}({rng(index)})", fmt, python=python)
            row += 1
        if index == key:
            put(f"A{row}", f"Highest {headers[index]}")
            put(f"B{row}", f"=MAX({rng(index)})", fmt, python=max(values))
            row += 1
            put(f"A{row}", f"Lowest {headers[index]}")
            put(f"B{row}", f"=MIN({rng(index)})", fmt, python=min(values))
            row += 1
    row += 1
    category = _category_column(prepared, kinds, columns, n)
    chart_anchor_row = 4

    def chart_col(count: int) -> str:
        return str(get_column_letter(count + 3))

    if category is not None:
        names = _distinct(columns[category])
        totals_by = {
            name: math.fsum(
                v
                for c, v in zip(columns[category], numbers[key], strict=True)
                if c == name and v is not None
            )
            for name in names
        }
        names.sort(key=lambda name: (-totals_by[name], str(name)))
        ws.cell(row=row, column=1, value=f"By {headers[category]}").font = bold
        row += 1
        labels = [headers[category], "Rows"]
        for index in value_cols:
            labels += [f"Sum of {headers[index]}", f"Average {headers[index]}"]
        head(row, labels)
        block_head = row
        row += 1
        for name in names:
            put(f"A{row}", name)
            members = [i for i, c in enumerate(columns[category]) if c == name]
            put(
                f"B{row}",
                f"=COUNTIFS({rng(category)},$A{row})",
                "#,##0",
                python=len(members),
            )
            col = 3
            for index in value_cols:
                picked = [v for i in members if (v := numbers[index][i]) is not None]
                total_cell = f"{get_column_letter(col)}{row}"
                avg_cell = f"{get_column_letter(col + 1)}{row}"
                put(
                    total_cell,
                    f"=SUMIFS({rng(index)},{rng(category)},$A{row})",
                    formats[index],
                    python=math.fsum(picked),
                )
                put(
                    avg_cell,
                    f"=IFERROR(AVERAGEIFS({rng(index)},{rng(category)},$A{row}),0)",
                    formats[index],
                    python=(math.fsum(picked) / len(picked)) if picked else 0.0,
                )
                col += 2
            row += 1
        bar = BarChart()
        bar.type = "col"
        bar.title = f"Sum of {headers[key]} by {headers[category]}"
        bar.y_axis.title = f"Sum of {headers[key]}"
        bar.x_axis.title = headers[category]
        bar.x_axis.delete = False
        bar.y_axis.delete = False
        bar.legend = None
        bar.height, bar.width = 8.5, 17
        bar.add_data(
            Reference(ws, min_col=3, min_row=block_head, max_row=row - 1), titles_from_data=True
        )
        bar.set_categories(Reference(ws, min_col=1, min_row=block_head + 1, max_row=row - 1))
        bar.series[0].graphicalProperties.solidFill = _hex(primary)
        ws.add_chart(bar, f"{chart_col(len(labels))}{chart_anchor_row}")
        chart_anchor_row += 19
        row += 1
    date_col = next((i for i, k in enumerate(kinds) if k == "date"), None)
    if date_col is not None:
        months = _month_starts(columns[date_col])
        if 2 <= len(months) <= MAX_MONTHS:
            ws.cell(row=row, column=1, value=f"By month ({headers[date_col]})").font = bold
            row += 1
            head(row, ["Month", "Rows", f"Sum of {headers[key]}"])
            block_head = row
            row += 1
            for month in months:
                nxt = date(month.year + (month.month == 12), month.month % 12 + 1, 1)
                members = [
                    i
                    for i, v in enumerate(columns[date_col])
                    if isinstance(v, date)
                    and month <= (v.date() if isinstance(v, datetime) else v) < nxt
                ]
                put(f"A{row}", month, "mmm yyyy")
                dates = rng(date_col)
                put(
                    f"B{row}",
                    f'=COUNTIFS({dates},">="&$A{row},{dates},"<"&EDATE($A{row},1))',
                    "#,##0",
                    python=len(members),
                )
                put(
                    f"C{row}",
                    f'=SUMIFS({rng(key)},{dates},">="&$A{row},{dates},"<"&EDATE($A{row},1))',
                    formats[key],
                    python=math.fsum(v for i in members if (v := numbers[key][i]) is not None),
                )
                row += 1
            line = LineChart()
            line.title = f"{headers[key]} over time"
            line.y_axis.title = f"Sum of {headers[key]}"
            line.x_axis.title = headers[date_col]
            line.x_axis.number_format = "mmm yyyy"
            line.x_axis.delete = False
            line.y_axis.delete = False
            line.legend = None
            line.height, line.width = 8.5, 17
            line.add_data(
                Reference(ws, min_col=3, min_row=block_head, max_row=row - 1), titles_from_data=True
            )
            line.set_categories(Reference(ws, min_col=1, min_row=block_head + 1, max_row=row - 1))
            line.series[0].graphicalProperties.line.solidFill = _hex(accent)
            line.series[0].graphicalProperties.line.width = 28000
            ws.add_chart(line, f"{chart_col(3)}{chart_anchor_row}")
            chart_anchor_row += 19
            row += 1
    # Top N by the key column: only the leading values that no other row shares (a tie would
    # make INDEX/MATCH return the same row twice).
    label_index = _label_column(prepared, kinds, columns)
    ranked = sorted(
        ((v, i) for i, v in enumerate(numbers[key]) if v is not None), key=lambda p: (-p[0], p[1])
    )
    top: list[tuple[float, int]] = []
    for position, (value, index) in enumerate(ranked[:TOP_N]):
        following = ranked[position + 1][0] if position + 1 < len(ranked) else None
        previous = ranked[position - 1][0] if position else None
        if value == following or value == previous:
            break
        top.append((value, index))
    if len(top) >= 3 and label_index != key:
        ws.cell(row=row, column=1, value=f"Top {len(top)} by {headers[key]}").font = bold
        row += 1
        head(row, ["Rank", headers[label_index], headers[key]])
        row += 1
        for rank, (value, index) in enumerate(top, start=1):
            put(f"A{row}", rank, "0")
            put(
                f"C{row}",
                f"=LARGE({rng(key)},$A{row})",
                formats[key],
                python=value,
            )
            put(
                f"B{row}",
                f"=INDEX({rng(label_index)},MATCH($C{row},{rng(key)},0))",
                python=columns[label_index][index],
            )
            row += 1
    ws.column_dimensions["A"].width = 28
    for letter in "BCDEFGH":
        ws.column_dimensions[letter].width = 18
    ws.page_setup.orientation = "landscape"
    ws.page_setup.fitToWidth = 1
    ws.page_setup.fitToHeight = 0
    ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)
    return title, expected


def _add_about(
    wb: Workbook, prepared: list[PreparedSheet], used: set[str], primary: str, seed_note: str
) -> str:
    """The "About this data" sheet: says plainly that the content is generated sample data."""
    title = _safe_title("About this data", used, 0)
    ws = wb.create_sheet(title=title)
    ws.sheet_properties.tabColor = "7F7F7F"
    ws.views.sheetView[0].showGridLines = False
    ws["A1"] = "About this data"
    ws["A1"].font = Font(name=BODY_FONT, size=14, bold=True, color=_hex(primary))
    lines = [
        "This workbook contains generated SAMPLE data. It is not real data.",
        "Names, quantities, prices and dates were produced by rules (random values from a fixed "
        "seed), not taken from your records.",
        "Do not use the figures for decisions, reports or accounts. Replace them with your real "
        "data first.",
        seed_note,
    ]
    for prep in prepared:
        made = [f"{c.header} ({c.kind})" for c in prep.columns if c.kind != "given" and c.header]
        if made:
            lines.append(f"Sheet '{prep.title}': generated columns: {', '.join(made)}.")
    for offset, text in enumerate(lines, start=3):
        cell = ws.cell(row=offset, column=1, value=text)
        cell.font = Font(name=BODY_FONT, size=11, bold=offset == 3)
        cell.alignment = Alignment(wrap_text=True, vertical="top")
    ws.column_dimensions["A"].width = 100
    return title


def _validations(
    ws: Any,
    prepared: PreparedSheet,
    kinds: list[str],
    columns: list[list[Any]],
    last_row: int,
) -> int:
    """List validation for closed text columns and number validation for typed ones."""
    added = 0
    for index, kind in enumerate(kinds):
        info = prepared.columns[index]
        letter = get_column_letter(index + 1)
        ref = f"{letter}2:{letter}{last_row}"
        values = columns[index]
        if kind == "text":
            options = info.choices or (
                _distinct(values)
                if 2 <= len(_distinct(values)) <= 12 and len(values) >= 6
                else None
            )
            if not options or any(not isinstance(o, str) or "," in o or '"' in o for o in options):
                continue
            joined = ",".join(options)
            if len(joined) > 250:
                continue
            check = DataValidation(
                type="list",
                formula1=f'"{joined}"',
                allow_blank=True,
                showErrorMessage=True,
                errorTitle="Not in the list",
                error="Choose one of the values in the list.",
            )
        elif kind in ("int", "float") and info.kind != "formula" and info.kind != "sequence":
            numbers = [v for v in _col_numbers(values) if v is not None]
            if not numbers:
                continue
            low = info.low if info.low is not None else (0 if min(numbers) >= 0 else None)
            if low is None:
                continue
            if info.high is not None:
                check = DataValidation(
                    type="whole" if kind == "int" else "decimal",
                    operator="between",
                    formula1=str(low),
                    formula2=str(info.high),
                    allow_blank=True,
                    showErrorMessage=True,
                    errorTitle="Out of range",
                    error=f"Enter a number from {low:g} to {info.high:g}.",
                )
            else:
                check = DataValidation(
                    type="whole" if kind == "int" else "decimal",
                    operator="greaterThanOrEqual",
                    formula1=str(low),
                    allow_blank=True,
                    showErrorMessage=True,
                    errorTitle="Out of range",
                    error=f"Enter a number that is {low:g} or more.",
                )
        else:
            continue
        ws.add_data_validation(check)
        check.add(ref)
        added += 1
    return added


def create_spreadsheet(
    sheets: list[dict[str, Any]],
    output_path: Path | str,
    *,
    style: Mapping[str, Any] | None = None,
    requested_rows: int | None = None,
) -> Path:
    """Generate a multi-tab formatted Excel spreadsheet (.xlsx); see :func:`build_spreadsheet`."""
    return build_spreadsheet(sheets, output_path, style=style, requested_rows=requested_rows).path


def _main_sheet_index(sheets: list[dict[str, Any]]) -> int:
    """The sheet that holds the data table: the one with the most rows (written or described)."""

    def size(sheet: dict[str, Any]) -> int:
        try:
            described = int(sheet.get("row_count") or 0)
        except (TypeError, ValueError):
            described = 0
        return max(described, len(sheet.get("rows") or []))

    return max(range(len(sheets)), key=lambda i: size(sheets[i]), default=0)


def build_spreadsheet(
    sheets: list[dict[str, Any]],
    output_path: Path | str,
    *,
    style: Mapping[str, Any] | None = None,
    requested_rows: int | None = None,
    rows_range: tuple[int, int] | None = None,
) -> SheetBuild:
    """Generate a multi-tab formatted Excel spreadsheet (.xlsx) and report what was built.

    :param sheets: List of sheet definitions:
      - title: Sheet name (str)
      - headers: List of column names (list[str])
      - rows: List of row data lists (list[list[Any]]; text that is a number, amount, percentage
        or ISO date is stored as a real value with a number format). With generators these are
        hand-written anchor rows that stay first
      - row_count, seed, columns: describe a big table with generators instead of listing rows
        (see ``app.automation.sheet_gen``); ``currency`` ($, ₹, € or £) only when the user named one
      - formulas: Dict mapping cell coordinates (e.g. "B11") to formula string
      - number_formats: Dict mapping column indices (1-based) to format string (wins over
        the inferred format)
    :param output_path: Destination path on disk.
    :param style: Optional design (``palette.primary``, ``palette.accent``, ``base``) the header,
      banding and charts take their colours from.
    :param requested_rows: The row count the USER stated; a sheet whose every column is generated
      is extended to it, the main sheet is cut to it when it has more (a row count mismatch never
      rejects the file), and :attr:`SheetBuild.shortfall` says when the file still has fewer.
    :param rows_range: ``(low, high)`` the user accepts ("50-100 rows"): inside it nothing changes.
    :raises SheetSpecError: when a spec cannot be built (the message says what to change).
    :return: A :class:`SheetBuild` with the resolved path and the facts of every sheet.
    """
    path = Path(output_path).resolve()
    path.parent.mkdir(parents=True, exist_ok=True)

    prepared_sheets: list[PreparedSheet] = []
    extend_done = False
    main_index = _main_sheet_index(sheets)
    for position, sheet_def in enumerate(sheets):
        wants = requested_rows if not extend_done else None
        prep = prepare_sheet(
            sheet_def,
            requested_rows=wants,
            rows_range=rows_range,
            allow_reduce=bool(requested_rows) and position == main_index,
        )
        if wants and prep.generated:
            extend_done = True
        prepared_sheets.append(prep)
    total_cells = sum(len(p.rows) * len(p.headers) for p in prepared_sheets)
    if total_cells > MAX_CELLS:
        raise SheetSpecError(
            f"The workbook would have {total_cells:,} cells; at most {MAX_CELLS:,} are allowed."
        )

    hint = " ".join(
        [str(s.get("title") or "") for s in sheets]
        + [str(h) for s in sheets[:2] for h in (s.get("headers") or [])]
    )
    primary, accent = xlsx_colors(style, pick_tone(hint))
    header_fill = PatternFill(start_color=_hex(primary), end_color=_hex(primary), fill_type="solid")
    header_font = Font(name=BODY_FONT, size=11, bold=True, color=_hex(best_on(primary)))
    band_fill = PatternFill(
        start_color=_hex(mix("#FFFFFF", primary, 0.07)),
        end_color=_hex(mix("#FFFFFF", primary, 0.07)),
        fill_type="solid",
    )
    total_tint = _hex(mix("#FFFFFF", primary, 0.16))
    total_fill = PatternFill(start_color=total_tint, end_color=total_tint, fill_type="solid")
    body_font = Font(name=BODY_FONT, size=10)

    wb = Workbook()
    if wb.active is not None:
        wb.remove(wb.active)  # Remove default sheet
    used: set[str] = set()
    table_names: set[str] = set()
    facts: list[SheetFacts] = []
    expected_all: dict[str, Any] = {}
    summary_title: str | None = None
    any_formula = False
    # What the first structured sheet offers the Summary sheet (built after the data sheets).
    summary_input: (
        tuple[Any, PreparedSheet, list[str], list[list[Any]], list[str], list[str], int] | None
    ) = None

    for number, prep in enumerate(prepared_sheets, start=1):
        ws = wb.create_sheet(title=_safe_title(prep.title, used, number))
        ws.sheet_properties.tabColor = _hex(primary)
        ws.views.sheetView[0].showGridLines = False

        headers = prep.headers
        rows = prep.rows
        width = len(headers)
        explicit = dict(prep.formats)
        kinds: list[str] = []
        formats: list[str] = []
        columns: list[list[Any]] = []  # what Python expects in each cell
        written: list[list[Any]] = []  # what is written (a formula column holds the formulas)
        for col in range(width):
            info = prep.columns[col]
            raw = [r[col] for r in rows]
            if info.kind == "formula":
                guess = list(info.expected or [None] * len(rows))
                kind, fmt, _ignored = _column_plan(headers[col], guess)
                if kind == "text":
                    known = [v for v in guess if v is not None]
                    whole = bool(known) and all(float(v).is_integer() for v in known)
                    kind, fmt = (
                        ("int", "#,##0;[Red]-#,##0")
                        if whole
                        else ("float", "#,##0.00;[Red]-#,##0.00")
                    )
                columns.append(guess)
                written.append(raw)
                any_formula = True
            else:
                kind, fmt, cells = _column_plan(headers[col], raw)
                columns.append(list(cells))
                written.append(cells)
            if prep.currency and _MONEY_HEADER.search(headers[col]) and _is_numeric(kind):
                symbol = prep.currency
                fmt = f'"{symbol}"#,##0.00;[Red]-"{symbol}"#,##0.00'
            kinds.append(kind)
            formats.append(explicit.get(col + 1, fmt))

        has_header = bool(headers) and any(headers)
        start_row = 1
        if has_header:
            ws.append(headers)
            start_row = 2
            for col in range(1, width + 1):
                cell = ws.cell(row=1, column=col)
                cell.font = header_font
                cell.fill = header_fill
                cell.alignment = Alignment(horizontal="center", vertical="center", wrap_text=True)
                cell.border = _BORDER
            ws.row_dimensions[1].height = 28
        for r in range(len(rows)):
            ws.append([written[c][r] for c in range(width)])

        last_row = ws.max_row
        if last_row >= start_row and width:
            _style_templates(
                ws, start_row, last_row, kinds, formats, explicit, band_fill, body_font
            )

        for coord, formula in (prep_formulas(sheets, number - 1)).items():
            ws[coord] = formula
            ws[coord].font = Font(name=BODY_FONT, size=10, bold=True)
            any_formula = True

        for col in range(1, width + 1):
            sample = written[col - 1] if len(rows) <= 5_000 else written[col - 1][:5_000]
            longest = max(
                [_display_len(headers[col - 1], "text") + 2]
                + [_display_len(v, kinds[col - 1]) for v in sample],
                default=MIN_COL_WIDTH,
            )
            ws.column_dimensions[get_column_letter(col)].width = max(
                MIN_COL_WIDTH, min(MAX_COL_WIDTH, longest + 3)
            )

        unique_headers = len({h.strip().lower() for h in headers}) == width and all(
            h.strip() for h in headers
        )
        structured = (
            start_row == 2
            and width > 0
            and bool(rows)
            and (prep.generated or len(rows) >= STRUCTURED_MIN_ROWS)
        )
        sheet_facts = SheetFacts(
            title=ws.title,
            data_rows=len(rows),
            columns=width,
            structured=structured,
            synthetic=prep.synthetic,
            generated=prep.generated,
            notes=list(prep.notes),
        )
        if start_row == 2 and width:
            ws.freeze_panes = "B2" if width >= 5 and kinds[0] == "text" else "A2"
            ws.print_title_rows = "1:1"
        if structured and unique_headers:
            ref = f"A1:{get_column_letter(width)}{last_row}"
            name = _table_name(ws.title, table_names)
            table = Table(displayName=name, ref=ref)
            table.tableStyleInfo = TableStyleInfo(
                name="TableStyleLight1", showRowStripes=True, showColumnStripes=False
            )
            ws.add_table(table)
            sheet_facts.table = name
        elif start_row == 2 and width and rows:
            ws.auto_filter.ref = f"A1:{get_column_letter(width)}{last_row}"
        ws.page_setup.orientation = "landscape" if width > 6 else "portrait"
        ws.page_setup.fitToWidth = 1
        ws.page_setup.fitToHeight = 0
        ws.sheet_properties.pageSetUpPr = PageSetupProperties(fitToPage=True)

        if start_row == 2 and len(rows) >= 3:
            _emphasise(ws, kinds, headers, len(rows), accent)
        if structured:
            sheet_facts.totals_row = _add_totals(
                ws,
                prep,
                kinds,
                formats,
                headers,
                columns,
                last_row,
                total_fill,
                sheet_facts,
            )
            for index, kind in enumerate(kinds):
                if _is_numeric(kind) or kind == "percent":
                    letter = get_column_letter(index + 1)
                    ws.conditional_formatting.add(
                        f"{letter}2:{letter}{last_row}",
                        _cell_is(operator="lessThan", formula=["0"], font=_RED_FONT),
                    )
            _validations(ws, prep, kinds, columns, last_row)
            if summary_input is None:
                summary_input = (ws, prep, kinds, columns, formats, headers, last_row)
        elif start_row == 2 and CHART_MIN_ROWS <= len(rows) <= CHART_MAX_ROWS:
            _add_chart(ws, kinds, formats, headers, len(rows), width, primary, accent)
        facts.append(sheet_facts)

    main = max(facts, key=lambda f: f.data_rows)
    build = SheetBuild(
        path=path,
        sheets=facts,
        main=main,
        requested_rows=requested_rows,
        rows_low=rows_range[0] if rows_range else None,
    )
    if summary_input is not None:
        ws0, prep0, kinds0, columns0, formats0, headers0, last0 = summary_input
        made = _add_summary(
            wb,
            ws0,
            prep0,
            kinds0,
            columns0,
            formats0,
            headers0,
            last0,
            used,
            primary,
            accent,
            header_font,
            header_fill,
        )
        if made is not None:
            summary_title, expected_all = made
            any_formula = True
    if any(p.synthetic for p in prepared_sheets):
        seeds = sorted({str(s.get("seed", DEFAULT_SEED)) for s in sheets if s.get("columns")})
        seed_note = (
            f"Seed: {', '.join(seeds) or DEFAULT_SEED}. The same request gives the same data."
        )
        build.about_sheet = _add_about(wb, prepared_sheets, used, primary, seed_note)
    build.summary_sheet = summary_title
    build.expected = expected_all
    wb.properties.creator = "MAYA"
    wb.properties.title = (str(sheets[0].get("title") or "Spreadsheet") if sheets else "")[:120]
    wb.properties.lastModifiedBy = "MAYA"
    if build.synthetic:
        wb.properties.description = "Generated sample data, not real records."
    if any_formula:
        # openpyxl stores formulas without cached values: ask the viewer to recalculate on open.
        wb.calculation.fullCalcOnLoad = True
    wb.save(path)
    build.file_rows = count_data_rows(path, main)
    return build


def prep_formulas(sheets: list[dict[str, Any]], index: int) -> Mapping[str, str]:
    raw = sheets[index].get("formulas") if index < len(sheets) else None
    return raw if isinstance(raw, Mapping) else {}


def count_data_rows(path: Path, facts: SheetFacts) -> int | None:
    """The data rows of ``facts``'s sheet counted from the written FILE (header and totals row
    excluded), so a delivery claim never rests on the spec alone."""
    try:
        workbook = load_workbook(str(path), read_only=True)
    except Exception:
        return None
    try:
        sheet = workbook[facts.title]
        used_rows = int(sheet.max_row or 0)
    except Exception:
        return None
    finally:
        workbook.close()
    return max(0, used_rows - 1 - (1 if facts.totals_row else 0))


def _add_totals(
    ws: Any,
    prep: PreparedSheet,
    kinds: list[str],
    formats: list[str],
    headers: list[str],
    columns: list[list[Any]],
    last_row: int,
    fill: PatternFill,
    facts: SheetFacts,
) -> int | None:
    """A TOTAL row under the data: SUBTOTAL formulas over every data row (they follow a filter).
    Returns the row number, or ``None`` when no column can be totalled."""
    plan: dict[int, str] = {}
    for index, kind in enumerate(kinds):
        mode = _total_mode(headers[index], kind, prep.columns[index].total)
        if mode != "none":
            plan[index] = mode
    if not plan:
        return None
    row = last_row + 1
    label_col = next((i for i, k in enumerate(kinds) if k == "text" and i not in plan), 0)
    bold = Font(name=BODY_FONT, size=10, bold=True)
    top = Border(
        top=Side(style="medium", color="404040"), bottom=Side(style="thin", color="404040")
    )
    for index in range(len(kinds)):
        cell = ws.cell(row=row, column=index + 1)
        cell.fill = fill
        cell.font = bold
        cell.border = top
    ws.cell(row=row, column=label_col + 1, value="Total")
    for index, mode in plan.items():
        letter = get_column_letter(index + 1)
        code = 109 if mode == "sum" else 101
        cell = ws.cell(
            row=row, column=index + 1, value=f"=SUBTOTAL({code},{letter}2:{letter}{last_row})"
        )
        cell.number_format = formats[index]
        cell.alignment = Alignment(horizontal="right")
        numbers = [v for v in _col_numbers(columns[index]) if v is not None]
        if numbers and len(numbers) == len(columns[index]):
            facts.expected_totals[headers[index]] = (
                math.fsum(numbers) if mode == "sum" else math.fsum(numbers) / len(numbers)
            )
    return row


def _emphasise(ws: Any, kinds: list[str], headers: list[str], n_rows: int, accent: str) -> None:
    """Conditional formatting on one key numeric column (data bars; a colour scale for percent)."""
    typed = [i for i, k in enumerate(kinds) if k in ("int", "float", "currency", "percent")]
    if not typed:
        return
    key = next((i for i in typed if _KEY_HEADER.search(headers[i])), typed[-1])
    letter = get_column_letter(key + 1)
    ref = f"{letter}2:{letter}{n_rows + 1}"
    if kinds[key] == "percent":
        ws.conditional_formatting.add(
            ref,
            _color_scale(
                start_type="min", start_color="F8CBAD", end_type="max", end_color="C6E0B4"
            ),
        )
    else:
        ws.conditional_formatting.add(
            ref,
            _data_bar(start_type="min", end_type="max", color=_hex(accent), showValue=True),
        )


def _add_chart(
    ws: Any,
    kinds: list[str],
    formats: list[str],
    headers: list[str],
    n_rows: int,
    width: int,
    primary: str,
    accent: str,
) -> None:
    """A bar (or line, for dates) chart beside a small label + numbers table."""
    if width < 2 or kinds[0] not in ("text", "date"):
        return
    columns = _chart_columns(kinds, formats, headers)
    if not columns:
        return
    chart: BarChart | LineChart
    chart = LineChart() if kinds[0] == "date" else BarChart()
    if isinstance(chart, BarChart):
        chart.type = "col"
    chart.title = headers[columns[0]] if len(columns) == 1 else None
    chart.x_axis.title = headers[0]
    chart.y_axis.title = headers[columns[0]]
    chart.x_axis.delete = False
    chart.y_axis.delete = False
    chart.height, chart.width = 8, 16
    colours = [_hex(primary), _hex(accent), "7F7F7F"]
    for slot, col in enumerate(columns):
        data = Reference(ws, min_col=col + 1, min_row=1, max_row=n_rows + 1)
        chart.add_data(data, titles_from_data=True)
        series = chart.series[slot]
        series.graphicalProperties.solidFill = colours[slot % len(colours)]
        series.graphicalProperties.line.solidFill = colours[slot % len(colours)]
    chart.set_categories(Reference(ws, min_col=1, min_row=2, max_row=n_rows + 1))
    chart.legend = None if len(columns) == 1 else chart.legend
    ws.add_chart(chart, f"{get_column_letter(width + 2)}2")
