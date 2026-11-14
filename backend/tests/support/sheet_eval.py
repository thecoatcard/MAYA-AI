"""A small evaluator for the formulas the sheet engine writes (no spreadsheet application here).

openpyxl stores formulas without cached values, so tests compute what Excel or LibreOffice would
show and compare it with the value Python expects. Supported: row arithmetic, SUM, SUBTOTAL,
AVERAGE, MAX, MIN, COUNTA, COUNTIFS, SUMIFS, AVERAGEIFS, IFERROR, LARGE, INDEX, MATCH, EDATE.
"""

from __future__ import annotations

import re
from datetime import date, datetime
from typing import Any

from openpyxl import Workbook
from openpyxl.utils import column_index_from_string

_RANGE = re.compile(
    r"^(?:(?:'((?:[^']|'')+)'|([A-Za-z0-9_]+))!)?\$?([A-Z]+)\$?(\d+)(?::\$?([A-Z]+)\$?(\d+))?$"
)


def _split(args: str) -> list[str]:
    out, depth, quote, current = [], 0, False, ""
    for ch in args:
        if ch == '"':
            quote = not quote
        if not quote:
            if ch == "(":
                depth += 1
            elif ch == ")":
                depth -= 1
            elif ch == "," and depth == 0:
                out.append(current)
                current = ""
                continue
        current += ch
    out.append(current)
    return out


class Evaluator:
    def __init__(self, wb: Workbook) -> None:
        self.wb = wb
        self.memo: dict[tuple[str, str], Any] = {}

    def cell(self, sheet: str, coord: str) -> Any:
        key = (sheet, coord)
        if key not in self.memo:
            value = self.wb[sheet][coord].value
            if isinstance(value, str) and value.startswith("="):
                value = self.formula(sheet, value[1:])
            self.memo[key] = value
        return self.memo[key]

    def rng(self, sheet: str, text: str) -> list[Any]:
        m = _RANGE.match(text.strip())
        assert m, text
        name = (m.group(1) or m.group(2) or sheet).replace("''", "'")
        c1, r1 = column_index_from_string(m.group(3)), int(m.group(4))
        c2 = column_index_from_string(m.group(5)) if m.group(5) else c1
        r2 = int(m.group(6)) if m.group(6) else r1
        from openpyxl.utils import get_column_letter

        return [
            self.cell(name, f"{get_column_letter(c)}{r}")
            for r in range(r1, r2 + 1)
            for c in range(c1, c2 + 1)
        ]

    def value(self, sheet: str, text: str) -> Any:
        text = text.strip()
        if _RANGE.match(text) and not re.fullmatch(r"-?\d+(\.\d+)?", text):
            cells = self.rng(sheet, text)
            return cells[0] if len(cells) == 1 else cells
        if text.startswith('"') and text.endswith('"'):
            return text[1:-1]
        return self.formula(sheet, text)

    def criterion(self, sheet: str, text: str) -> Any:
        """``">="&$A27`` -> (">=", value); ``$A16`` -> ("=", value)."""
        m = re.match(r'^"(>=|<=|<|>)"&(.+)$', text.strip())
        if m:
            return m.group(1), self.value(sheet, m.group(2))
        return "=", self.value(sheet, text)

    @staticmethod
    def _ok(cell: Any, crit: tuple[str, Any]) -> bool:
        op, target = crit
        if isinstance(cell, datetime):
            cell = cell.date()
        if isinstance(target, datetime):
            target = target.date()
        if cell is None:
            return False
        verdicts: dict[str, bool] = {
            "=": cell == target,
            ">=": cell >= target,
            "<=": cell <= target,
            "<": cell < target,
            ">": cell > target,
        }
        return verdicts[op]

    def formula(self, sheet: str, expr: str) -> Any:
        expr = expr.strip()
        m = re.match(r"^([A-Z]+)\((.*)\)$", expr)
        if m and expr.count("(") == expr.count(")") and _balanced_outer(expr):
            name, args = m.group(1), _split(m.group(2))
            return self.call(sheet, name, args)
        if _RANGE.match(expr) and not re.fullmatch(r"\d+(\.\d+)?", expr):
            return self.value(sheet, expr)

        # Arithmetic over cell references and numbers.
        def ref(mm: re.Match[str]) -> str:
            v = self.value(sheet, mm.group(0))
            return repr(float(v))

        py = re.sub(r"(?:'[^']+'!)?\$?[A-Z]{1,3}\$?\d+", ref, expr)
        assert re.fullmatch(r"[0-9eE+\-*/(). ]+", py), (expr, py)
        return eval(py, {"__builtins__": {}}, {})  # noqa: S307 - digits and operators only

    def call(self, sheet: str, name: str, args: list[str]) -> Any:
        def nums(text: str) -> list[float]:
            got = self.value(sheet, text)
            items = got if isinstance(got, list) else [got]
            return [float(v) for v in items if isinstance(v, int | float)]

        if name in ("SUM",):
            return sum(nums(args[0]))
        if name == "SUBTOTAL":
            data = nums(args[1])
            return sum(data) if int(args[0]) == 109 else sum(data) / len(data)
        if name == "AVERAGE":
            data = nums(args[0])
            return sum(data) / len(data)
        if name == "MAX":
            return max(nums(args[0]))
        if name == "MIN":
            return min(nums(args[0]))
        if name == "COUNTA":
            return len([v for v in self.value(sheet, args[0]) if v not in (None, "")])
        if name in ("COUNTIFS", "SUMIFS", "AVERAGEIFS"):
            pairs = args[1:] if name != "COUNTIFS" else args
            ranges = [self.value(sheet, pairs[i]) for i in range(0, len(pairs), 2)]
            crits = [self.criterion(sheet, pairs[i]) for i in range(1, len(pairs), 2)]
            idx = [
                i
                for i in range(len(ranges[0]))
                if all(self._ok(r[i], c) for r, c in zip(ranges, crits, strict=True))
            ]
            if name == "COUNTIFS":
                return len(idx)
            data = self.value(sheet, args[0])
            picked = [float(data[i]) for i in idx if isinstance(data[i], int | float)]
            if name == "SUMIFS":
                return sum(picked)
            return sum(picked) / len(picked)
        if name == "IFERROR":
            try:
                return self.formula(sheet, args[0])
            except ZeroDivisionError:
                return self.formula(sheet, args[1])
        if name == "LARGE":
            return sorted(nums(args[0]), reverse=True)[int(self.value(sheet, args[1])) - 1]
        if name == "MATCH":
            target = self.value(sheet, args[0])
            return self.value(sheet, args[1]).index(target) + 1
        if name == "INDEX":
            return self.value(sheet, args[0])[int(self.formula(sheet, args[1])) - 1]
        if name == "EDATE":
            base = self.value(sheet, args[0])
            base = base.date() if isinstance(base, datetime) else base
            months = int(args[1])
            total = base.year * 12 + base.month - 1 + months
            return date(total // 12, total % 12 + 1, base.day)
        raise AssertionError(f"unsupported function {name}")


def _balanced_outer(expr: str) -> bool:
    """True when the first '(' closes at the very end (so ``F(a)+G(b)`` is not one call)."""
    depth, quote = 0, False
    for i, ch in enumerate(expr):
        if ch == '"':
            quote = not quote
        if quote:
            continue
        if ch == "(":
            depth += 1
        elif ch == ")":
            depth -= 1
            if depth == 0 and i != len(expr) - 1:
                return False
    return True
