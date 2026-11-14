"""Sheet engine: generators, row-count honesty, totals, table, summary, caps, editing, summary tool.

No network, no model: a fake model is just the spec dict a model would send. Formula results are
computed by ``tests.support.sheet_eval`` (no spreadsheet application is available here) and
compared with the values Python expects.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import openpyxl
import pytest

from app.automation.acceptance import check_acceptance, extract_acceptance
from app.automation.sheet_gen import (
    GENERATOR_KINDS,
    MAX_COLUMNS,
    MAX_ROWS,
    POOL_NAMES,
    PRODUCT_POOL_SIZE,
    SheetSpecError,
    eval_row_formula,
    prepare_sheet,
)
from app.automation.sheet_ops import SheetOpError, apply_sheet_ops, sheet_summary
from app.automation.xlsx_engine import build_spreadsheet
from app.services import document_tools as dt
from app.services.document_tools import DocumentToolError, build_document
from app.services.sheet_tools import run_edit, summary_result
from tests.support.sheet_eval import Evaluator

USER_REQUEST = "sales ka sheet banao: 500 products, units, price, total"


def generated_sheet(rows: int = 500, **extra: Any) -> dict[str, Any]:
    return {
        "title": "Sales Report",
        "headers": ["ID", "Product", "Category", "Units", "Price", "Total", "Date"],
        "row_count": rows,
        "columns": [
            {"header": "ID", "generate": {"kind": "sequence", "prefix": "P-", "start": 1001}},
            {"header": "Product", "generate": {"kind": "name_pool", "pool": "products"}},
            {
                "header": "Category",
                "generate": {"kind": "name_pool", "pool": "product_categories", "of": "Product"},
            },
            {"header": "Units", "generate": {"kind": "range_int", "min": 1, "max": 500}},
            {
                "header": "Price",
                "generate": {"kind": "range_float", "min": 99, "max": 4999, "decimals": 2},
            },
            {"header": "Total", "generate": {"kind": "formula", "template": "=D{row}*E{row}"}},
            {
                "header": "Date",
                "generate": {"kind": "date_range", "start": "2025-01-01", "end": "2025-12-31"},
            },
        ],
        **extra,
    }


@pytest.fixture
def built500(tmp_path: Path) -> Any:
    return build_spreadsheet([generated_sheet()], tmp_path / "s.xlsx", requested_rows=500)


# ------------------------------------------------------------------------------ acceptance
@pytest.mark.parametrize(
    ("text", "rows"),
    [
        (USER_REQUEST, 500),
        ("500 products ka sheet", 500),
        ("100 rows ka excel", 100),
        ("50 entries in a table", 50),
        ("1,000 employees excel", 1000),
        ("500 student records", 500),
        ("5 tips on sleep as a pdf", None),
        ("top 10 products sheet", None),
        ("add 100 more rows", None),
        ("notes on Java, 50 pages", None),
    ],
)
def test_the_row_count_is_only_what_the_user_stated(text: str, rows: int | None) -> None:
    assert extract_acceptance(text).get("rows") == rows


def test_rows_are_checked_exactly_and_a_big_gap_is_severe() -> None:
    short = check_acceptance({"rows": 500}, {"rows": 5})
    assert not short.met and short.severe and "Requested 500 rows, produced 5" in short.message
    assert check_acceptance({"rows": 500}, {"rows": 500}).met


# ------------------------------------------------------------------------------- generation
def test_500_products_are_generated_exactly_unique_and_deterministic(
    tmp_path: Path, built500: Any
) -> None:
    ws = openpyxl.load_workbook(built500.path)["Sales Report"]
    ids = [ws.cell(row=r, column=1).value for r in range(2, 502)]
    names = [ws.cell(row=r, column=2).value for r in range(2, 502)]
    assert built500.file_rows == 500 and not built500.shortfall
    assert len(set(ids)) == 500 and ids[0] == "P-1001" and ids[-1] == "P-1500"
    assert len(set(names)) == 500  # no awkward repeats
    again = build_spreadsheet([generated_sheet()], tmp_path / "t.xlsx", requested_rows=500)
    ws2 = openpyxl.load_workbook(again.path)["Sales Report"]
    assert [[c.value for c in r] for r in ws.iter_rows()] == [
        [c.value for c in r] for r in ws2.iter_rows()
    ]
    other = build_spreadsheet([generated_sheet(seed=7)], tmp_path / "u.xlsx")
    ws3 = openpyxl.load_workbook(other.path)["Sales Report"]
    assert [c.value for c in ws3["D"]] != [c.value for c in ws["D"]]
    assert PRODUCT_POOL_SIZE >= 200


def test_every_generator_kind_works_and_pools_are_listed() -> None:
    columns = [
        {"header": "A", "generate": {"kind": "sequence", "start": 5, "step": 5}},
        {"header": "B", "generate": {"kind": "choice", "values": ["x", "y"], "weights": [9, 1]}},
        {"header": "C", "generate": {"kind": "range_int", "min": 3, "max": 4}},
        {"header": "D", "generate": {"kind": "range_float", "min": 0.5, "max": 0.6, "decimals": 1}},
        {
            "header": "E",
            "generate": {
                "kind": "date_range",
                "start": "2025-01-01",
                "end": "2025-01-10",
                "step_days": 3,
            },
        },
        {"header": "F", "generate": {"kind": "name_pool", "pool": "cities"}},
        {"header": "G", "generate": {"kind": "formula", "template": "=C{row}+D{row}"}},
        {"header": "H", "generate": {"kind": "copy_of", "column": "B"}},
    ]
    sheet = {
        "title": "T",
        "headers": [c["header"] for c in columns],
        "columns": columns,
        "row_count": 12,
    }
    prepared = prepare_sheet(sheet)
    assert {c.kind for c in prepared.columns} == set(GENERATOR_KINDS)
    rows = prepared.rows
    assert [r[0] for r in rows[:3]] == [5, 10, 15]
    assert all(r[2] in (3, 4) and r[3] in (0.5, 0.6) for r in rows)
    assert str(rows[1][4]) == "2025-01-04" and rows[1][7] == rows[1][1]
    assert rows[0][6] == "=C2+D2" and prepared.columns[6].expected is not None
    for pool in POOL_NAMES:
        one = {
            "title": "P",
            "headers": ["N"],
            "row_count": 30,
            "columns": [{"header": "N", "generate": {"kind": "name_pool", "pool": pool}}],
        }
        assert len(prepare_sheet(one).rows) == 30


def test_hand_written_rows_stay_first_and_continue_the_sequence() -> None:
    sheet = generated_sheet(10)
    sheet["rows"] = [["P-1001", "My Product", "Office", 3, 10.0, None, None]]
    prepared = prepare_sheet(sheet)
    assert prepared.rows[0][1] == "My Product" and prepared.rows[0][5] == "=D2*E2"
    assert prepared.rows[1][0] == "P-1002" and prepared.anchors == 1 and len(prepared.rows) == 10
    assert prepared.columns[5].expected is not None and prepared.columns[5].expected[0] == 30.0


def test_caps_are_enforced() -> None:
    with pytest.raises(SheetSpecError, match="at most 50,000"):
        prepare_sheet(generated_sheet(MAX_ROWS + 1))
    wide = {"title": "W", "headers": [f"c{i}" for i in range(MAX_COLUMNS + 1)], "rows": []}
    with pytest.raises(SheetSpecError, match="columns"):
        prepare_sheet(wide)
    big = {
        "title": "B",
        "headers": [f"c{i}" for i in range(50)],
        "row_count": 30_000,
        "columns": [
            {"header": f"c{i}", "generate": {"kind": "range_int", "min": 1, "max": 2}}
            for i in range(50)
        ],
    }
    with pytest.raises(SheetSpecError, match="cells"):
        prepare_sheet(big)
    missing = {
        "title": "M",
        "headers": ["A", "B"],
        "row_count": 50,
        "columns": [{"header": "A", "generate": {"kind": "sequence"}}],
    }
    with pytest.raises(SheetSpecError, match="no 'generate'"):
        prepare_sheet(missing)
    with pytest.raises(SheetSpecError, match="unknown"):
        prepare_sheet(
            {
                "title": "X",
                "headers": ["A"],
                "row_count": 3,
                "columns": [{"header": "A", "generate": {"kind": "magic"}}],
            }
        )
    with pytest.raises(SheetSpecError, match="not allowed"):
        prepare_sheet(
            {
                "title": "X",
                "headers": ["A"],
                "row_count": 3,
                "columns": [
                    {"header": "A", "generate": {"kind": "formula", "template": '=HYPERLINK("x")'}}
                ],
            }
        )


def test_the_evaluator_for_row_formulas() -> None:
    assert eval_row_formula("=D2*E2", 2, {"D": 3, "E": 2.5}) == 7.5
    assert eval_row_formula("=ROUND(D2*1.18,2)", 2, {"D": 10}) == 11.8
    assert eval_row_formula("=D3*E2", 2, {"D": 3, "E": 2.5}) is None
    assert eval_row_formula("=D2/E2", 2, {"D": 3, "E": 0}) is None


# ----------------------------------------------------------------- quality: formulas vs Python
def test_totals_table_validation_conditional_formats_and_charts(built500: Any) -> None:
    wb = openpyxl.load_workbook(built500.path)
    ws = wb["Sales Report"]
    main = built500.main
    assert main.totals_row == 502 and ws["A502"].value == "Total"
    assert ws["D502"].value == "=SUBTOTAL(109,D2:D501)"  # covers every data row
    assert ws["E502"].value is None  # a unit price is never summed
    ev = Evaluator(wb)
    for header, coord in (("Units", "D502"), ("Total", "F502")):
        assert ev.cell("Sales Report", coord) == pytest.approx(main.expected_totals[header])
    # The Total column equals units x price for each row (Python's own product).
    for r in (2, 77, 501):
        assert ev.cell("Sales Report", f"F{r}") == pytest.approx(
            ws[f"D{r}"].value * ws[f"E{r}"].value
        )
    table = ws.tables[main.table]
    assert table.ref == "A1:G501" and table.tableStyleInfo.showRowStripes
    assert ws.auto_filter.ref in (None, "")  # the table owns the filter
    kinds = {v.type for v in ws.data_validations.dataValidation}
    assert {"list", "whole", "decimal"} <= kinds
    assert ws.conditional_formatting
    assert ws.freeze_panes == "B2" and ws.page_setup.orientation == "landscape"
    assert ws.print_title_rows == "$1:$1"
    assert ws["E2"].number_format.startswith("#,##0.00") and "$" not in ws["E2"].number_format
    assert wb.calculation.fullCalcOnLoad is True
    assert wb.properties.creator == "MAYA" and wb.properties.title == "Sales Report"


def test_the_summary_sheet_formulas_equal_python(built500: Any) -> None:
    wb = openpyxl.load_workbook(built500.path)
    assert built500.summary_sheet == "Summary" and len(built500.expected) > 40
    ev = Evaluator(wb)
    for address, expected in built500.expected.items():
        sheet, coord = address.split("!")
        got = ev.cell(sheet, coord)
        if isinstance(expected, int | float):
            assert got == pytest.approx(expected), address
        else:
            assert got == expected, address
    charts: Any = wb["Summary"]
    assert len(charts._charts) == 2
    for chart in charts._charts:
        assert chart.title is not None and chart.x_axis.title is not None
        assert chart.y_axis.title is not None


def test_synthetic_data_is_labelled_in_the_file(built500: Any) -> None:
    wb = openpyxl.load_workbook(built500.path)
    assert built500.synthetic and built500.about_sheet == "About this data"
    text = " ".join(
        str(c.value) for row in wb["About this data"].iter_rows() for c in row if c.value
    )
    assert "SAMPLE data" in text and "not real" in text
    assert wb.properties.description == "Generated sample data, not real records."


def test_currency_is_never_guessed_but_used_when_stated(tmp_path: Path) -> None:
    plain = build_spreadsheet([generated_sheet(20)], tmp_path / "a.xlsx")
    ws = openpyxl.load_workbook(plain.path)["Sales Report"]
    assert not any(s in ws["E2"].number_format for s in "$₹€£")
    rupee = build_spreadsheet([generated_sheet(20, currency="₹")], tmp_path / "b.xlsx")
    ws = openpyxl.load_workbook(rupee.path)["Sales Report"]
    assert "₹" in ws["E2"].number_format and "₹" in ws["F2"].number_format


def test_a_small_hand_written_sheet_keeps_the_classic_layout(tmp_path: Path) -> None:
    sheet = {"title": "Items", "headers": ["Item", "Cost"], "rows": [["a", "1"], ["b", "2"]]}
    built = build_spreadsheet([sheet], tmp_path / "c.xlsx")
    ws = openpyxl.load_workbook(built.path)["Items"]
    assert not ws.tables and ws.auto_filter.ref == "A1:B3" and built.main.totals_row is None


# --------------------------------------------------------------- tool path and honesty
def tool(
    tmp_path: Path, spec: dict[str, Any], *, retry: bool = False, request: str = USER_REQUEST
) -> Any:
    return build_document(
        file_format="xlsx",
        title="Sales Sheet",
        spec=spec,
        request=request,
        workdir=tmp_path,
        length_retry=retry,
    )


def test_a_fake_model_with_generators_delivers_all_500_rows(tmp_path: Path) -> None:
    built = tool(tmp_path, {"sheets": [generated_sheet(500)]})
    facts = dt.sheet_result_fields(built.sheet)
    assert (facts["rows"], facts["rows_requested"]) == (500, 500)
    assert facts["synthetic"] is True and facts["delivery"] == "complete"
    assert "sample data generated, not real" in built.warnings
    assert "generated sample data" in facts["message"]


def test_a_model_row_count_below_the_stated_one_is_extended(tmp_path: Path) -> None:
    built = tool(tmp_path, {"sheets": [generated_sheet(5)]})
    assert built.sheet is not None and built.sheet["rows"] == 500


def test_five_hand_written_rows_for_a_stated_500_are_partial(tmp_path: Path) -> None:
    sheet = {
        "title": "Sales Report",
        "headers": ["ID", "Units", "Price"],
        "rows": [[f"P-{i}", str(i), "10"] for i in range(1001, 1006)],
    }
    # Count policy (deliberate update): the first attempt used to raise a repairable "row_count"
    # error and discard the sheet. A row shortfall is now delivered with the true count, on the
    # first attempt too (the model may extend the file afterwards on its own).
    built = tool(tmp_path, {"sheets": [sheet]}, retry=True)
    facts = dt.sheet_result_fields(built.sheet)
    assert facts["delivery"] == "partial" and (facts["rows"], facts["rows_requested"]) == (5, 500)
    assert "5 of 500" in facts["message"] and "Do not" not in "" and "SHORT" in facts["message"]
    assert any("SHORT" in w for w in built.warnings)
    assert "You asked for 500 rows; the file has 5" in facts["message"]


def test_the_models_own_plan_is_never_a_row_requirement(tmp_path: Path) -> None:
    sheet = {"title": "T", "headers": ["A"], "rows": [["x"], ["y"]], "row_count": 2}
    built = tool(tmp_path, {"sheets": [sheet]}, request="make me a small sheet")
    assert built.sheet is not None
    assert built.sheet["rows_requested"] is None and built.sheet["delivery"] == "complete"


def test_a_bad_generator_is_a_repairable_error(tmp_path: Path) -> None:
    sheet = generated_sheet(10)
    sheet["columns"][3]["generate"]["kind"] = "bogus"
    with pytest.raises(DocumentToolError) as info:
        tool(tmp_path, {"sheets": [sheet]}, request="a sheet")
    assert info.value.repairable and "bogus" in str(info.value)


def test_the_schema_and_prompt_come_from_the_engine() -> None:
    spec = dt.CREATE_DOCUMENT_TOOL.parameters["properties"]["spec"]["properties"]["sheets"]
    props = spec["items"]["properties"]
    generate = props["columns"]["items"]["properties"]["generate"]["properties"]
    assert generate["kind"]["enum"] == list(GENERATOR_KINDS)
    assert generate["pool"]["enum"] == list(POOL_NAMES)
    assert props["row_count"]["maximum"] == MAX_ROWS
    assert props["currency"]["enum"] == ["$", "₹", "€", "£"]
    prompt = dt.TOOL_LOOP_ADDENDUM
    for kind in GENERATOR_KINDS:
        assert kind in prompt
    for field in ("row_count", "rows_requested", "synthetic", "delivery", "sample data"):
        assert field in prompt


# -------------------------------------------------------------------------------- editing
@pytest.fixture
def source(tmp_path: Path) -> Path:
    return build_spreadsheet([generated_sheet(40)], tmp_path / "src.xlsx").path


def edit(source: Path, tmp_path: Path, ops: list[dict[str, Any]]) -> tuple[Any, Any]:
    dest = tmp_path / "out.xlsx"
    outcome = apply_sheet_ops(source, ops, dest)
    return outcome, openpyxl.load_workbook(dest)


def test_sort_dedupe_and_totals_follow_the_data(source: Path, tmp_path: Path) -> None:
    outcome, wb = edit(
        source,
        tmp_path,
        [
            {"op": "sort", "by": "Total", "descending": True},
            {"op": "dedupe", "columns": ["Category"]},
        ],
    )
    ws = wb["Sales Report"]
    kept = int(outcome.counts[1]["kept"])
    assert outcome.counts[1]["removed"] == 40 - kept and kept == 8
    assert ws.max_row == kept + 2 and ws.tables["tbl_Sales_Report"].ref == f"A1:G{kept + 1}"
    assert ws.cell(row=kept + 2, column=6).value == f"=SUBTOTAL(109,F2:F{kept + 1})"
    assert ws["F2"].value == "=D2*E2"  # formulas moved with their rows
    assert wb["Summary"]["B6"].value == f"=COUNTA('Sales Report'!$A$2:$A${kept + 1})"
    ev = Evaluator(wb)
    totals = [ev.cell("Sales Report", f"F{r}") for r in range(2, kept + 2)]
    assert ev.cell("Sales Report", f"F{kept + 2}") == pytest.approx(sum(totals))
    assert ws["A2"].fill.start_color.rgb != ws["A3"].fill.start_color.rgb  # banding kept
    assert ws["A1"].fill.fill_type == "solid" and ws.freeze_panes == "B2"


def test_sort_orders_by_the_computed_value(source: Path, tmp_path: Path) -> None:
    _outcome, wb = edit(source, tmp_path, [{"op": "sort", "by": "Total", "descending": True}])
    ev = Evaluator(wb)
    totals = [ev.cell("Sales Report", f"F{r}") for r in range(2, 42)]
    assert totals == sorted(totals, reverse=True)


def test_columns_rows_find_replace_and_text_numbers(source: Path, tmp_path: Path) -> None:
    outcome, wb = edit(
        source,
        tmp_path,
        [
            {"op": "add_column", "name": "Tax", "formula": "=F{row}*0.18"},
            {"op": "rename_column", "column": "Tax", "new_name": "GST"},
            {"op": "add_rows", "rows": [["P-9", "Thing", "Office", "3", "2.5", "", ""]]},
            {"op": "find_replace", "find": "Office", "replace": "Workspace"},
            {"op": "text_to_number", "column": "Units"},
            {"op": "fill_formula_down", "column": "GST", "formula": "=F{row}*0.05"},
            {"op": "recalc_totals"},
        ],
    )
    ws = wb["Sales Report"]
    assert (
        ws["H1"].value == "GST" and ws["H2"].value == "=F2*0.05" and ws["H42"].value == "=F42*0.05"
    )
    assert ws["A42"].value == "P-9" and ws["D42"].value == 3  # text became a number
    assert ws.tables["tbl_Sales_Report"].ref == "A1:H42"
    assert wb["Summary"]["B6"].value == "=COUNTA('Sales Report'!$A$2:$A$42)"
    counts = {c["op"]: c for c in outcome.counts}
    assert counts["add_column"]["cells"] == 40 and counts["add_rows"]["rows"] == 1
    assert counts["text_to_number"]["cells"] == 1 and counts["find_replace"]["cells"] >= 1
    assert "Office" not in {c.value for c in ws["C"]}


def test_filter_copy_and_a_column_used_by_formulas_is_not_deleted(
    source: Path, tmp_path: Path
) -> None:
    outcome, wb = edit(
        source,
        tmp_path,
        [
            {
                "op": "filter_copy",
                "column": "Units",
                "operator": "ge",
                "value": "250",
                "new_sheet": "Big",
            },
        ],
    )
    big = wb["Big"]
    assert outcome.counts[0]["rows"] == big.max_row - 1 and big["A1"].value == "ID"
    assert big["A1"].fill.fill_type == "solid"
    assert wb["Sales Report"].max_row == 42  # the original sheet is unchanged
    with pytest.raises(SheetOpError, match="used by"):
        apply_sheet_ops(source, [{"op": "delete_column", "column": "Units"}], tmp_path / "x.xlsx")
    assert not (tmp_path / "x.xlsx").exists()
    with pytest.raises(SheetOpError, match="Unknown operation"):
        apply_sheet_ops(source, [{"op": "explode"}], tmp_path / "y.xlsx")


def test_delete_a_free_column_repoints_the_others(tmp_path: Path) -> None:
    sheet = {
        "title": "S",
        "headers": ["A", "B", "C"],
        "row_count": 4,
        "columns": [
            {"header": "A", "generate": {"kind": "range_int", "min": 1, "max": 9}},
            {"header": "B", "generate": {"kind": "range_int", "min": 1, "max": 9}},
            {"header": "C", "generate": {"kind": "formula", "template": "=A{row}*2"}},
        ],
    }
    path = build_spreadsheet([sheet], tmp_path / "d.xlsx").path
    outcome, wb = edit(path, tmp_path, [{"op": "delete_column", "column": "B"}])
    ws = wb["S"]
    assert [c.value for c in ws[1]] == ["A", "C"] and ws["B2"].value == "=A2*2"
    assert outcome.counts[0]["cells"] > 0


def test_a_text_number_sort_is_refused_not_guessed(tmp_path: Path) -> None:
    sheet = {"title": "S", "headers": ["A"], "rows": [["x"], ["3"], ["y"]]}
    path = build_spreadsheet([sheet], tmp_path / "m.xlsx").path
    wb = openpyxl.load_workbook(path)
    wb["S"]["A3"].value = 3
    wb.save(path)
    with pytest.raises(SheetOpError, match="mixes"):
        apply_sheet_ops(path, [{"op": "sort", "by": "A"}], tmp_path / "z.xlsx")


def test_edit_tool_wrapper_reports_plain_errors(source: Path, tmp_path: Path) -> None:
    outcome, problem = run_edit(source, [{"op": "sort", "by": "Nope"}], tmp_path / "e.xlsx")
    assert outcome is None and problem is not None and "no column" in problem.lower()
    outcome, problem = run_edit(source, "sort", tmp_path / "f.xlsx")
    assert outcome is None and problem is not None
    done, none = run_edit(source, [{"op": "sort", "by": "Units"}], tmp_path / "g.xlsx")
    assert none is None and done is not None and done.lines


# ------------------------------------------------------------------------------ sheet_summary
def test_sheet_summary_is_bounded_and_describes_the_data(tmp_path: Path) -> None:
    path = build_spreadsheet([generated_sheet(5000)], tmp_path / "big.xlsx").path
    summary = sheet_summary(path)
    main = summary["sheets"][0]
    assert main["data_rows"] == 5000 and main["totals_row"] == 5002
    assert len(main["first_rows"]) == 5 and main["header"][0] == "ID"
    types = {c["name"]: c["type"] for c in main["columns"]}
    assert types["Units"] == "number" and types["Total"] == "formula" and types["Date"] == "date"
    assert main["duplicate_rows"] == 0 and summary["sheet_names"][:2] == ["Sales Report", "Summary"]
    result = summary_result(path)
    assert result["status"] == "summarised" and len(json.dumps(result)) < 12_000


def test_sheet_summary_counts_nulls_and_duplicates(tmp_path: Path) -> None:
    sheet = {"title": "S", "headers": ["A", "B"], "rows": [["x", "1"], ["x", "1"], ["y", ""]]}
    path = build_spreadsheet([sheet], tmp_path / "n.xlsx").path
    main = sheet_summary(path)["sheets"][0]
    assert main["duplicate_rows"] == 1
    assert {c["name"]: c["empty"] for c in main["columns"]} == {"A": 0, "B": 1}
    assert summary_result(tmp_path / "missing.xlsx")["status"] == "error"


def test_file_edit_runs_named_operations_and_rejects_mixing(source: Path, tmp_path: Path) -> None:
    from app.automation.file_edit import EditError, apply_edit

    dest = tmp_path / "v.xlsx"
    outcome = apply_edit("xlsx", source, {"ops": [{"op": "dedupe", "columns": ["Category"]}]}, dest)
    assert outcome.changes == 32 and "32 duplicate rows removed" in outcome.diff
    mixed = {
        "ops": [
            {"op": "sort", "by": "Units"},
            {"sheet": "Sales Report", "cell": "A1", "value": "x"},
        ]
    }
    with pytest.raises(EditError, match="mix"):
        apply_edit("xlsx", source, mixed, tmp_path / "w.xlsx")
