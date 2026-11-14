"""Chat tools for spreadsheets made in this conversation: ``sheet_summary`` (read-only) and
``edit_sheet`` (update in place through named operations).

The model never has to put hundreds of rows into its context or its answer: it asks for a bounded
summary, then names operations (sort, add a column, remove duplicates ...) and the code applies
them (``app.automation.sheet_ops``). The chat service updates the sheet in place; the
declarations and the pure helpers live here so the engine and the schema cannot drift.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any, Final

from app.automation.sheet_ops import (
    FILTER_OPERATORS,
    MAX_OPS,
    MAX_SUMMARY_CHARS,
    SHEET_OPS,
    OpsOutcome,
    SheetOpError,
    apply_sheet_ops,
    sheet_summary,
)
from app.automation.verifiers.xlsx_verifier import verify_xlsx
from app.gateway.schemas import ToolDefinition

SHEET_SUMMARY_NAME: Final = "sheet_summary"
EDIT_SHEET_NAME: Final = "edit_sheet"
SHEET_TOOL_NAMES: Final = frozenset({SHEET_SUMMARY_NAME, EDIT_SHEET_NAME})

SHEET_SUMMARY_TOOL: Final = ToolDefinition(
    name=SHEET_SUMMARY_NAME,
    description=(
        "Read-only look at an xlsx made earlier in this conversation: its sheets, dimensions, "
        "header, the first 5 rows, the type of every column, empty counts and duplicate rows. "
        "Use it to answer questions about a sheet or to plan an edit; it never returns hundreds "
        "of rows."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "The id of an xlsx made in this chat."}
        },
        "required": ["file_id"],
    },
)
EDIT_SHEET_TOOL: Final = ToolDefinition(
    name=EDIT_SHEET_NAME,
    description=(
        "Update an xlsx made earlier in this conversation in place; the "
        "previous version is kept in a backup and the existing design "
        "(styles, widths, table) stays. Name the operations; the code "
        "applies them and reports exact counts. Never paste rows."
    ),
    parameters={
        "type": "object",
        "properties": {
            "file_id": {"type": "string", "description": "The id of an xlsx made in this chat."},
            "operations": {
                "type": "array",
                "description": f"At most {MAX_OPS}, applied in order.",
                "items": {
                    "type": "object",
                    "properties": {
                        "op": {"type": "string", "enum": list(SHEET_OPS)},
                        "sheet": {"type": "string", "description": "Sheet name (default first)."},
                        "column": {"type": "string", "description": "A header or a column letter."},
                        "by": {"type": "string", "description": "sort: the column to sort by."},
                        "descending": {"type": "boolean"},
                        "name": {"type": "string", "description": "add_column: the new header."},
                        "new_name": {"type": "string", "description": "rename_column: new header."},
                        "position": {"type": "integer", "description": "add_column: 1-based."},
                        "formula": {
                            "type": "string",
                            "description": "add_column or fill_formula_down: for example "
                            "'=D{row}*E{row}' ({row} is the row number).",
                        },
                        "value": {
                            "type": "string",
                            "description": "add_column: a fixed value; filter_copy: the value.",
                        },
                        "number_format": {"type": "string"},
                        "rows": {
                            "type": "array",
                            "description": "add_rows: the rows to append.",
                            "items": {"type": "array", "items": {"type": "string"}},
                        },
                        "operator": {"type": "string", "enum": list(FILTER_OPERATORS)},
                        "new_sheet": {"type": "string", "description": "filter_copy: its name."},
                        "columns": {
                            "type": "array",
                            "description": "dedupe: the columns that define a duplicate.",
                            "items": {"type": "string"},
                        },
                        "find": {"type": "string"},
                        "replace": {"type": "string"},
                        "match_case": {"type": "boolean"},
                        "whole_cell": {"type": "boolean"},
                    },
                    "required": ["op"],
                },
            },
        },
        "required": ["file_id", "operations"],
    },
)
SHEET_TOOL_DEFINITIONS: Final = (SHEET_SUMMARY_TOOL, EDIT_SHEET_TOOL)


def summary_result(path: Path) -> dict[str, Any]:
    """The ``sheet_summary`` result: bounded, with a plain error instead of an exception."""
    try:
        summary = sheet_summary(path)
    except SheetOpError as error:
        return {"status": "error", "message": str(error)}
    text = json.dumps(summary, ensure_ascii=False, default=str)
    if len(text) > MAX_SUMMARY_CHARS:
        for sheet in summary["sheets"]:
            sheet["first_rows"] = sheet["first_rows"][:2]
            sheet["columns"] = sheet["columns"][:12]
            sheet["header"] = sheet["header"][:12]
        summary["note"] = "The summary was shortened to fit."
    return {
        "status": "summarised",
        **summary,
        "message": (
            "Answer from these facts only. Say how many data rows and columns there are; never "
            "claim a value this summary does not show."
        ),
    }


def run_edit(source: Path, operations: Any, dest: Path) -> tuple[OpsOutcome | None, str | None]:
    """Apply ``operations`` to a copy at ``dest``; ``(outcome, None)`` or ``(None, plain error)``.
    The new file is opened and verified again, so a broken edit is never delivered."""
    if not isinstance(operations, list) or not all(isinstance(o, dict) for o in operations):
        return None, "'operations' must be a list of objects, each with an 'op'."
    try:
        outcome = apply_sheet_ops(source, operations, dest)
    except SheetOpError as error:
        return None, str(error)
    except Exception as error:  # a broken workbook is reported, never raised into the loop
        return (
            None,
            f"The sheet could not be changed ({type(error).__name__}). Nothing was changed.",
        )
    report = verify_xlsx(dest)
    if report.status != "verified":
        return None, "The changed sheet failed its checks: " + "; ".join(report.errors[:3])
    return outcome, None


__all__ = [
    "EDIT_SHEET_NAME",
    "EDIT_SHEET_TOOL",
    "SHEET_SUMMARY_NAME",
    "SHEET_SUMMARY_TOOL",
    "SHEET_TOOL_DEFINITIONS",
    "SHEET_TOOL_NAMES",
    "run_edit",
    "summary_result",
]
