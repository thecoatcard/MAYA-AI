"""Tolerant reshaping of model-written document and deck dicts, run BEFORE strict validation.

A model asked for structured output often gets the *content* right and the *shape* slightly
wrong: the block ``type`` key is missing or called ``kind``/``block_type``, the value is ``h2`` or
``ul`` instead of ``heading``/``bullets``, a table has only ``headers`` and ``rows``. The strict
models in :mod:`app.automation.model` (which stay strict) would reject all of that and the user
would lose a good report to a formatting problem.

These helpers only reshape what is there: aliases are renamed, a missing type is inferred from
the fields that are present, scalar text is wrapped where a list is expected. They never invent
content: a block with nothing recognisable in it is dropped (and counted in the returned notes),
not filled in. The strict validator still has the final word.
"""

from __future__ import annotations

import re
from collections.abc import Callable
from typing import Any

from app.automation.deck_transitions import canonical_transition

# Canonical names (kept in step with ``app.automation.model.BLOCK_TYPES`` / ``SLIDE_LAYOUTS`` by
# a unit test, so this module does not import the model and the model can import this one).
BLOCK_TYPE_NAMES: tuple[str, ...] = (
    "heading",
    "paragraph",
    "bullets",
    "numbered",
    "table",
    "callout",
    "metrics",
    "quote",
    "image",
    "code",
    "page_break",
)
SLIDE_LAYOUT_NAMES: tuple[str, ...] = (
    "cover",
    "section",
    "bullets",
    "two_column",
    "cards",
    "stat_callout",
    "timeline",
    "comparison",
    "table",
    "code",
    "quote",
    "closing",
    "image",
    "hero_cover",
    "section_break",
    "stat_grid",
    "icon_cards",
    "process",
    "image_text",
    "chart",
)

_TYPE_KEYS = ("type", "block_type", "blockType", "blocktype", "kind", "t", "block", "element")
_LAYOUT_KEYS = ("layout", "type", "slide_type", "slideType", "kind", "template", "t")
_CALLOUT_KINDS = {
    "info": "info",
    "information": "info",
    "note": "info",
    "tip": "info",
    "hint": "info",
    "warning": "warning",
    "warn": "warning",
    "caution": "warning",
    "danger": "warning",
    "alert": "warning",
    "success": "success",
    "ok": "success",
    "good": "success",
}

# alias -> (canonical type, heading level or None)
_BLOCK_ALIASES: dict[str, tuple[str, int | None]] = {
    "heading": ("heading", None),
    "heading1": ("heading", 1),
    "heading2": ("heading", 2),
    "heading3": ("heading", 3),
    "h": ("heading", None),
    "h1": ("heading", 1),
    "h2": ("heading", 2),
    "h3": ("heading", 3),
    "h4": ("heading", 3),
    "header": ("heading", None),
    "title": ("heading", 1),
    "section": ("heading", 2),
    "section_title": ("heading", 2),
    "section_header": ("heading", 2),
    "subtitle": ("heading", 2),
    "subheading": ("heading", 2),
    "sub_heading": ("heading", 2),
    "paragraph": ("paragraph", None),
    "p": ("paragraph", None),
    "para": ("paragraph", None),
    "text": ("paragraph", None),
    "body": ("paragraph", None),
    "prose": ("paragraph", None),
    "content": ("paragraph", None),
    "normal": ("paragraph", None),
    "string": ("paragraph", None),
    "bullets": ("bullets", None),
    "bullet": ("bullets", None),
    "bullet_list": ("bullets", None),
    "bulleted_list": ("bullets", None),
    "bullet_points": ("bullets", None),
    "unordered_list": ("bullets", None),
    "unordered": ("bullets", None),
    "ul": ("bullets", None),
    "list": ("bullets", None),
    "numbered": ("numbered", None),
    "numbered_list": ("numbered", None),
    "ordered_list": ("numbered", None),
    "ordered": ("numbered", None),
    "ol": ("numbered", None),
    "enumerate": ("numbered", None),
    "table": ("table", None),
    "data_table": ("table", None),
    "datatable": ("table", None),
    "grid": ("table", None),
    "callout": ("callout", None),
    "note": ("callout", None),
    "alert": ("callout", None),
    "info": ("callout", None),
    "warning": ("callout", None),
    "success": ("callout", None),
    "tip": ("callout", None),
    "highlight": ("callout", None),
    "admonition": ("callout", None),
    "metrics": ("metrics", None),
    "metric": ("metrics", None),
    "kpi": ("metrics", None),
    "kpis": ("metrics", None),
    "stats": ("metrics", None),
    "statistics": ("metrics", None),
    "key_metrics": ("metrics", None),
    "quote": ("quote", None),
    "blockquote": ("quote", None),
    "pullquote": ("quote", None),
    "pull_quote": ("quote", None),
    "image": ("image", None),
    "img": ("image", None),
    "picture": ("image", None),
    "figure": ("image", None),
    "code": ("code", None),
    "code_block": ("code", None),
    "codeblock": ("code", None),
    "snippet": ("code", None),
    "page_break": ("page_break", None),
    "pagebreak": ("page_break", None),
    "break": ("page_break", None),
    "pb": ("page_break", None),
}

_SLIDE_ALIASES: dict[str, str] = {
    "title": "cover",
    "title_slide": "cover",
    "cover": "cover",
    "intro": "cover",
    "section": "section",
    "section_header": "section",
    "section_slide": "section",
    "divider": "section",
    "bullets": "bullets",
    "bullet": "bullets",
    "bullet_points": "bullets",
    "bullet_list": "bullets",
    "content": "bullets",
    "content_slide": "bullets",
    "text": "bullets",
    "list": "bullets",
    "summary": "bullets",
    "agenda": "bullets",
    "two_column": "two_column",
    "two_columns": "two_column",
    "2_column": "two_column",
    "two_col": "two_column",
    "comparison": "comparison",
    "pros_cons": "comparison",
    "before_after": "comparison",
    "code": "code",
    "code_slide": "code",
    "code_block": "code",
    "cards": "cards",
    "card": "cards",
    "metrics": "stat_callout",
    "metric": "stat_callout",
    "kpi": "stat_callout",
    "kpis": "stat_callout",
    "stats": "stat_callout",
    "stat_callout": "stat_callout",
    "big_number": "stat_callout",
    "quote": "quote",
    "table": "table",
    "data_table": "table",
    "image": "image",
    "picture": "image",
    "timeline": "timeline",
    # "process" is its own layout now (chevron steps); "roadmap" and "steps" stay a timeline.
    "process": "process",
    "process_steps": "process",
    "chevrons": "process",
    "roadmap": "timeline",
    "steps": "timeline",
    "closing": "closing",
    "end": "closing",
    "thank_you": "closing",
    "thanks": "closing",
    "conclusion": "closing",
    "hero_cover": "hero_cover",
    "hero": "hero_cover",
    "section_break": "section_break",
    "stat_grid": "stat_grid",
    "stat_tiles": "stat_grid",
    "icon_cards": "icon_cards",
    "icon_card": "icon_cards",
    "features": "icon_cards",
    "image_text": "image_text",
    "image_and_text": "image_text",
    "chart": "chart",
    "graph": "chart",
    "donut": "chart",
    "bar_chart": "chart",
    "line_chart": "chart",
}

_BULLET_PREFIX = re.compile(r"^\s*(?:[-*•▪●]|\d{1,3}[.)])\s+")


def _key(value: Any) -> str:
    return re.sub(r"[\s\-]+", "_", str(value).strip().lower())


def _first(raw: dict[str, Any], *names: str) -> Any:
    for name in names:
        if raw.get(name) not in (None, "", [], {}):
            return raw[name]
    return None


def _to_text(value: Any) -> str:
    if value is None:
        return ""
    if isinstance(value, str):
        return value.strip()
    if isinstance(value, bool):
        return str(value)
    if isinstance(value, int | float):
        return str(value)
    if isinstance(value, list):
        return " ".join(t for t in (_to_text(v) for v in value) if t)
    if isinstance(value, dict):
        inner = _first(value, "text", "content", "title", "label", "name", "value", "body")
        return _to_text(inner)
    return str(value).strip()


def _to_items(value: Any) -> list[str]:
    """A list of non-empty strings from a list, a dict list, or a newline separated string."""
    if value is None:
        return []
    if isinstance(value, str):
        value = value.splitlines()
    if not isinstance(value, list):
        value = [value]
    items: list[str] = []
    for entry in value:
        text = _to_text(entry)
        text = _BULLET_PREFIX.sub("", text).strip()
        if text:
            items.append(text)
    return items


def _level(value: Any) -> int | None:
    try:
        level = int(str(value).strip().lstrip("hH"))
    except (TypeError, ValueError):
        return None
    return max(1, min(3, level))


def _cell(value: Any) -> str:
    return _to_text(value)


_TABLE_SEPARATOR = re.compile(r"^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$")


def _split_row_text(line: str) -> list[str]:
    """``"a | b | c"``, ``"| a | b |"`` or a tab-separated line to its cells."""
    text = line.strip()
    if "|" in text:
        text = text.removeprefix("|").removesuffix("|")
        return [cell.strip() for cell in text.split("|")]
    if "\t" in text:
        return [cell.strip() for cell in text.split("\t")]
    return [text]


def _text_lines(value: str) -> list[str]:
    """The non-empty lines of a markdown or pipe table, without its ``---|---`` separator."""
    return [
        line for line in value.splitlines() if line.strip() and not _TABLE_SEPARATOR.match(line)
    ]


def _header_cells(value: Any) -> list[str]:
    if isinstance(value, str):
        lines = _text_lines(value)
        return [c for c in _split_row_text(lines[0]) if c] if lines else []
    if isinstance(value, list):
        return [_cell(h) for h in value]
    return []


def coerce_rows(rows_raw: Any, headers: list[str]) -> tuple[list[str], list[list[str]], bool]:
    """Table rows in every shape a model sends, as ``(headers, list[list[str]], changed)``.

    Understood: a list of lists; a list of dicts keyed by header; a list of pipe-delimited (or
    tab-separated) strings; a whole markdown table as ONE string; a flat list of cells (cut into
    rows of ``len(headers)``). Rows are then made rectangular: a short row is padded and an overlong
    row keeps its overflow text in its last cell (text is never dropped). ``changed`` is True when
    the input was not already a list of rectangular lists of strings.
    """
    headers = list(headers)
    items: list[Any]
    if isinstance(rows_raw, str):
        items = _text_lines(rows_raw)
    elif isinstance(rows_raw, list):
        items = rows_raw
    else:
        items = []
    changed = not isinstance(rows_raw, list)
    scalars = bool(items) and all(
        not isinstance(i, list | dict) and "|" not in str(i) and "\t" not in str(i) for i in items
    )
    rows: list[list[str]] = []
    if scalars and len(headers) > 1:
        flat = [_cell(i) for i in items]
        rows = [flat[i : i + len(headers)] for i in range(0, len(flat), len(headers))]
        changed = True
    else:
        for row in items:
            if isinstance(row, dict):
                if not headers:
                    headers = [str(k) for k in row]
                rows.append([_cell(row.get(h, "")) for h in headers])
                changed = True
            elif isinstance(row, list):
                cells = [_cell(c) for c in row]
                changed = changed or any(not isinstance(c, str) for c in row)
                rows.append(cells)
            elif row not in (None, ""):
                rows.append([_cell(c) for c in _split_row_text(str(row))])
                changed = True
    if not headers and len(rows) >= 2:
        headers, rows = rows[0], rows[1:]  # a 2-D array whose first row is the header
        changed = True
    elif not headers and rows:
        headers = [""] * len(rows[0])
    width = len(headers)
    fixed: list[list[str]] = []
    for row in rows:
        if len(row) > width > 0:
            row = [*row[: width - 1], " | ".join(c for c in row[width - 1 :] if c)]
            changed = True
        elif len(row) < width:
            row = [*row, *[""] * (width - len(row))]
            changed = True
        fixed.append(row)
    return headers, fixed, changed


def _table_parts(raw: dict[str, Any]) -> tuple[list[str], list[list[str]]]:
    headers = _header_cells(_first(raw, "headers", "header", "columns", "cols"))
    headers, rows, _changed = coerce_rows(
        _first(raw, "rows", "data", "body", "cells", "values"), headers
    )
    return headers, rows


def _metric_items(value: Any) -> list[dict[str, str]]:
    metrics: list[dict[str, str]] = []
    if isinstance(value, dict):
        value = [{"label": k, "value": v} for k, v in value.items()]
    if not isinstance(value, list):
        return metrics
    for entry in value:
        if not isinstance(entry, dict):
            continue
        number = _to_text(_first(entry, "value", "number", "stat", "amount", "metric"))
        label = _to_text(_first(entry, "label", "name", "title", "description", "caption"))
        if number and label:
            item: dict[str, Any] = {"value": number, "label": label}
            progress = _percent(_first(entry, "progress", "percent", "percentage"))
            if progress is not None:
                item["progress"] = progress
            metrics.append(item)
    return metrics


def _percent(value: Any) -> float | None:
    """A 0-100 number from ``42``, ``"42%"`` or ``0.42`` is NOT guessed: only 0-100 is accepted."""
    if isinstance(value, bool):
        return None
    if isinstance(value, str):
        text = value.strip().rstrip("%").strip()
        try:
            value = float(text)
        except ValueError:
            return None
    if isinstance(value, int | float) and 0 <= float(value) <= 100:
        return float(value)
    return None


def _number(value: Any) -> float | None:
    if isinstance(value, bool):
        return None
    if isinstance(value, int | float):
        return float(value)
    if isinstance(value, str):
        text = value.strip().replace(",", "").rstrip("%").strip()
        try:
            return float(text)
        except ValueError:
            return None
    return None


_CHART_TYPES = {
    "column": "column",
    "columns": "column",
    "vertical": "column",
    "bar": "bar",
    "bars": "bar",
    "horizontal": "bar",
    "line": "line",
    "area": "line",
    "doughnut": "doughnut",
    "donut": "doughnut",
    "ring": "doughnut",
    "pie": "doughnut",
}


def _chart(value: Any) -> dict[str, Any] | None:
    """A chart dict in the strict schema's shape (series values as numbers), or ``None``."""
    if not isinstance(value, dict):
        return None
    categories = [_to_text(c) for c in (value.get("categories") or value.get("labels") or [])]
    categories = [c for c in categories if c]
    raw_series = value.get("series")
    if raw_series is None and isinstance(value.get("values"), list):
        raw_series = [{"name": value.get("name", ""), "values": value["values"]}]
    series: list[dict[str, Any]] = []
    for entry in raw_series or []:
        if not isinstance(entry, dict):
            continue
        numbers = [_number(v) for v in (entry.get("values") or entry.get("data") or [])]
        if numbers and all(n is not None for n in numbers):
            series.append({"name": _to_text(entry.get("name")), "values": numbers})
    if not categories or not series:
        return None
    kind = _CHART_TYPES.get(_key(value.get("type", "")) or "column", "column")
    return {"type": kind, "categories": categories, "series": series}


def _infer_block_type(raw: dict[str, Any]) -> str | None:
    if _first(raw, "headers", "header", "columns") is not None and _first(
        raw, "rows", "data", "cells"
    ):
        return "table"
    if isinstance(raw.get("rows"), list) and raw["rows"]:
        return "table"
    if _metric_items(raw.get("metrics") or raw.get("items") or raw.get("stats")):
        return "metrics"
    if _first(raw, "src", "image", "url_path") is not None:
        return "image"
    if _first(raw, "code") is not None:
        return "code"
    if _first(raw, "quote") is not None or (_first(raw, "by", "author") and _first(raw, "text")):
        return "quote"
    if _first(raw, "items", "bullets", "points", "list") is not None:
        return "numbered" if raw.get("ordered") is True else "bullets"
    if _first(raw, "level") is not None and _first(raw, "text", "heading", "title"):
        return "heading"
    kind = _key(raw.get("kind", ""))
    if kind in _CALLOUT_KINDS and _first(raw, "text", "content", "body", "message"):
        return "callout"
    if _first(raw, "text", "content", "body", "paragraph", "description", "message") is not None:
        return "paragraph"
    if _first(raw, "heading", "title") is not None:
        return "heading"
    return None


def _resolve_block_type(raw: dict[str, Any]) -> tuple[str | None, int | None, str | None]:
    """(canonical type, alias heading level, callout kind) for a raw block dict."""
    callout_kind: str | None = None
    for name in _TYPE_KEYS:
        value = raw.get(name)
        if not isinstance(value, str) or not value.strip():
            continue
        alias = _key(value)
        if name == "kind" and alias in _CALLOUT_KINDS and alias not in _BLOCK_ALIASES:
            # ``kind: warning`` is the callout style, not the block type.
            continue
        if alias in _BLOCK_ALIASES:
            canonical, level = _BLOCK_ALIASES[alias]
            if canonical == "callout" and alias in _CALLOUT_KINDS:
                callout_kind = _CALLOUT_KINDS[alias]
            return canonical, level, callout_kind
    return None, None, callout_kind


def _explicit_unknown_type(raw: dict[str, Any]) -> str | None:
    """A stated ``type`` that is not a known block type or alias (left for strict validation)."""
    for name in ("type", "block_type", "blockType", "blocktype"):
        value = raw.get(name)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return None


def normalize_block(raw: Any) -> dict[str, Any] | None:
    """One block dict in the strict schema's shape, or ``None`` when nothing usable is there.

    A block that states a known type but has no content is returned as it is (with the canonical
    type) so strict validation reports it; a block that states an unknown type is likewise left
    for validation to name. Only a block with no type and nothing inferable is dropped.
    """
    if isinstance(raw, str):
        text = raw.strip()
        return {"type": "paragraph", "text": text} if text else None
    if not isinstance(raw, dict):
        return None

    block_type, alias_level, callout_kind = _resolve_block_type(raw)
    if block_type is None:
        stated = _explicit_unknown_type(raw)
        if stated is not None:
            return {**{k: v for k, v in raw.items() if k not in _TYPE_KEYS}, "type": stated}
        block_type = _infer_block_type(raw)
        if block_type is None:
            return None
    built = _build_block(raw, block_type, alias_level, callout_kind)
    if built is None and _explicit_known(raw):
        return {**{k: v for k, v in raw.items() if k not in _TYPE_KEYS}, "type": block_type}
    return built


def _explicit_known(raw: dict[str, Any]) -> bool:
    return any(isinstance(raw.get(name), str) and raw[name].strip() for name in _TYPE_KEYS[:4])


def _build_block(
    raw: dict[str, Any], block_type: str, alias_level: int | None, callout_kind: str | None
) -> dict[str, Any] | None:

    if block_type == "heading":
        text = _to_text(_first(raw, "text", "heading", "title", "content", "name"))
        if not text:
            return None
        level = _level(raw.get("level")) or alias_level or 1
        return {"type": "heading", "level": level, "text": text}
    if block_type == "paragraph":
        text = _to_text(_first(raw, "text", "content", "body", "paragraph", "description"))
        return {"type": "paragraph", "text": text} if text else None
    if block_type in ("bullets", "numbered"):
        items = _to_items(_first(raw, "items", "bullets", "points", "list", "values", "text"))
        return {"type": block_type, "items": items} if items else None
    if block_type == "table":
        headers, rows = _table_parts(raw)
        if not headers:
            return None
        table: dict[str, Any] = {"type": "table", "headers": headers, "rows": rows}
        caption = _to_text(raw.get("caption") or raw.get("title"))
        if caption:
            table["caption"] = caption
        return table
    if block_type == "callout":
        text = _to_text(_first(raw, "text", "content", "body", "message"))
        if not text:
            return None
        kind = _CALLOUT_KINDS.get(_key(raw.get("kind", "")), callout_kind or "info")
        return {"type": "callout", "kind": kind, "text": text}
    if block_type == "metrics":
        metrics = _metric_items(_first(raw, "items", "metrics", "stats", "values"))
        return {"type": "metrics", "items": metrics} if metrics else None
    if block_type == "quote":
        text = _to_text(_first(raw, "text", "quote", "content"))
        if not text:
            return None
        quote: dict[str, Any] = {"type": "quote", "text": text}
        by = _to_text(_first(raw, "by", "author", "source", "attribution"))
        if by:
            quote["by"] = by
        return quote
    if block_type == "image":
        src = _to_text(_first(raw, "src", "image", "path", "url_path"))
        if not src:
            return None
        caption = _to_text(raw.get("caption"))
        image: dict[str, Any] = {
            "type": "image",
            "src": src,
            "alt": _to_text(_first(raw, "alt", "alt_text")) or caption or "Image",
        }
        if caption:
            image["caption"] = caption
        return image
    if block_type == "code":
        text = _to_text(_first(raw, "text", "code", "content"))
        if not text:
            return None
        return {
            "type": "code",
            "language": _to_text(raw.get("language") or raw.get("lang")),
            "text": raw.get("text") if isinstance(raw.get("text"), str) else text,
        }
    return {"type": "page_break"}


def normalize_blocks(blocks: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Reshape a list of raw blocks. Returns ``(blocks, notes)``; notes say what was changed."""
    if isinstance(blocks, dict):
        blocks = blocks.get("blocks") or [blocks]
    if not isinstance(blocks, list):
        return [], ["the block list was not a list"]
    out: list[dict[str, Any]] = []
    dropped = 0
    reshaped = 0
    for raw in blocks:
        block = normalize_block(raw)
        if block is None:
            dropped += 1
            continue
        if block != raw:
            reshaped += 1
        out.append(block)
    notes: list[str] = []
    if reshaped:
        notes.append(f"{reshaped} block(s) were reshaped to the document schema")
    if dropped:
        notes.append(f"{dropped} unusable block(s) with no content were dropped")
    return out, notes


NL = chr(10)


def _flat_text(value: Any) -> str:
    if isinstance(value, list):
        rows = [_flat_text(v) if isinstance(v, list) else _to_text(v) for v in value]
        sep = " | " if value and all(isinstance(v, str | int | float) for v in value) else NL
        return sep.join(r for r in rows if r)
    return _to_text(value)


def _text_of(raw: dict[str, Any]) -> str:
    """Every readable string in a block that could not be repaired, one per line."""
    parts: list[str] = []
    for key, value in raw.items():
        if key in _TYPE_KEYS and isinstance(value, str) and len(value) < 24:
            continue
        text = _flat_text(value)
        if text:
            parts.append(text)
    return NL.join(parts).strip()


def repair_spec_blocks(
    blocks: Any, validate: Callable[[dict[str, Any]], Any] | None = None
) -> tuple[list[Any], list[str]]:
    """Reshape the model's raw block list BEFORE the flat ``BlockSpec`` schema checks it.

    Every block goes through :func:`normalize_block` (aliases, table rows in any shape, scalar
    text where a list is expected). A block that still fails ``validate`` (the flat schema), or
    that normalising could not read, is kept as a PARAGRAPH of its text and named in the notes:
    a repair never drops a block or a section silently. A block with no text at all is the only
    thing dropped, and it is counted in the notes. ``validate`` raises when a block is unusable.
    """
    if not isinstance(blocks, list):
        return blocks, []
    out: list[Any] = []
    notes: list[str] = []
    for position, raw in enumerate(blocks, start=1):
        block = normalize_block(raw)
        if block is not None and block.get("type") == "metrics" and "items" in block:
            block = {**{k: v for k, v in block.items() if k != "items"}, "metrics": block["items"]}
        if block is not None and block.get("type") == "table" and isinstance(raw, dict):
            before = _first(raw, "rows", "data", "body", "cells", "values")
            if before != block.get("rows"):
                notes.append(
                    f"block {position} (table): the rows were reshaped into lists of cells"
                )
        if block is not None and validate is not None:
            try:
                validate(block)
            except Exception:
                label = block.get("type", "block")
                fallback = _text_of(raw) if isinstance(raw, dict) else str(raw).strip()
                if fallback:
                    notes.append(
                        f"block {position} ({label}) could not be repaired; its text was kept "
                        "as a paragraph"
                    )
                    block = {"type": "paragraph", "text": fallback}
                else:
                    block = None
        if block is None:
            fallback = _text_of(raw) if isinstance(raw, dict) else ""
            if fallback:
                notes.append(
                    f"block {position} could not be read; its text was kept as a paragraph"
                )
                block = {"type": "paragraph", "text": fallback}
            else:
                notes.append(f"block {position} had no content and was left out")
                continue
        out.append(block)
    return out, notes


# --------------------------------------------------------------------------- slides
def _resolve_layout(raw: dict[str, Any]) -> str | None:
    for name in _LAYOUT_KEYS:
        value = raw.get(name)
        if isinstance(value, str) and value.strip():
            alias = _key(value)
            if alias in _SLIDE_ALIASES:
                return _SLIDE_ALIASES[alias]
    return None


def _infer_layout(raw: dict[str, Any], first: bool) -> str:
    if raw.get("left") and raw.get("right"):
        return "two_column"
    if raw.get("cards"):
        return "cards"
    if raw.get("metrics"):
        return "stat_callout"
    if raw.get("steps"):
        return "timeline"
    if raw.get("table"):
        return "table"
    if raw.get("image"):
        return "image"
    if raw.get("quote"):
        return "quote"
    if _first(raw, "bullets", "points", "items", "content", "body", "text") is not None:
        return "bullets"
    if _first(raw, "title", "heading") is not None:
        return "cover" if first else "section"
    return "bullets"


def _column(value: Any) -> dict[str, Any] | None:
    if isinstance(value, dict):
        bullets = _to_items(_first(value, "bullets", "items", "points", "text", "content"))
        if not bullets:
            return None
        return {"heading": _to_text(value.get("heading") or value.get("title")), "bullets": bullets}
    bullets = _to_items(value)
    return {"heading": "", "bullets": bullets} if bullets else None


def normalize_slide(
    raw: Any, *, first: bool = False, notes: list[str] | None = None
) -> dict[str, Any] | None:
    """One slide dict reshaped to the strict schema, or ``None`` when it has no content.

    A stated layout that is not one of :data:`SLIDE_LAYOUT_NAMES` is replaced by the layout the
    content suggests (bullets when it has nothing more specific) and reported in ``notes``.
    """
    if isinstance(raw, str):
        return {"layout": "bullets", "title": raw.strip(), "bullets": []} if raw.strip() else None
    if not isinstance(raw, dict):
        return None
    layout = _resolve_layout(raw)
    stated = raw.get("layout")
    unknown = layout is None and isinstance(stated, str) and stated.strip() != ""
    layout = layout or _infer_layout(raw, first)
    if layout not in SLIDE_LAYOUT_NAMES:
        layout = "bullets"
    if unknown and notes is not None:
        notes.append(f"layout '{stated}' is not a slide layout; used '{layout}'")
    slide: dict[str, Any] = {"layout": layout}
    title = _to_text(_first(raw, "title", "heading", "header", "name"))
    if title:
        slide["title"] = title
    subtitle = _to_text(_first(raw, "subtitle", "sub_title", "tagline"))
    if subtitle:
        slide["subtitle"] = subtitle
    number = _to_text(raw.get("number"))
    if number and layout in ("section", "section_break"):
        slide["number"] = number
    bullets = _to_items(_first(raw, "bullets", "points", "items", "content", "body", "text"))
    if (
        layout
        in (
            "bullets",
            "cover",
            "closing",
            "section",
            "hero_cover",
            "section_break",
            "image_text",
            "chart",
        )
        and bullets
    ):
        slide["bullets"] = bullets
    if layout in ("two_column", "comparison"):
        left, right = _column(raw.get("left")), _column(raw.get("right"))
        if left:
            slide["left"] = left
        if right:
            slide["right"] = right
    if layout == "code" and isinstance(raw.get("code"), dict):
        slide["code"] = dict(raw["code"])
    if layout in ("cards", "icon_cards"):
        cards = [
            {
                "title": _to_text(_first(c, "title", "heading", "name")),
                "body": _to_text(c.get("body") or c.get("text") or c.get("description")),
                "glyph": _to_text(c.get("glyph")),
                "icon": _to_text(c.get("icon")),
            }
            for c in raw.get("cards") or []
            if isinstance(c, dict)
        ]
        slide["cards"] = [c for c in cards if c["title"]]
    if layout in ("stat_callout", "stat_grid"):
        slide["metrics"] = _metric_items(raw.get("metrics") or raw.get("items"))
    if layout == "quote":
        slide["quote"] = _to_text(_first(raw, "quote", "text", "content"))
        by = _to_text(_first(raw, "by", "author", "source"))
        if by:
            slide["by"] = by
    if layout == "table" and isinstance(raw.get("table"), dict):
        headers, rows = _table_parts(raw["table"])
        slide["table"] = {"headers": headers, "rows": rows}
    if layout in ("image", "image_text") and isinstance(raw.get("image"), dict):
        slide["image"] = dict(raw["image"])
    if layout in ("timeline", "process"):
        steps = [
            {
                "title": _to_text(_first(s, "title", "name", "step")),
                "detail": _to_text(_first(s, "detail", "description", "text", "body")),
                "icon": _to_text(s.get("icon")),
            }
            for s in raw.get("steps") or []
            if isinstance(s, dict)
        ]
        slide["steps"] = [s for s in steps if s["title"]]
    if layout == "chart":
        chart = _chart(raw.get("chart"))
        if chart is not None:
            slide["chart"] = chart
    transition = _to_text(raw.get("transition"))
    if transition:
        name = canonical_transition(transition)
        if name is None:
            if notes is not None:
                notes.append(f"transition '{transition}' is not available; the deck's was used")
        else:
            slide["transition"] = name
    speaker_notes = _to_text(_first(raw, "notes", "speaker_notes"))
    if speaker_notes:
        slide["notes"] = speaker_notes
    return slide


def normalize_slides(slides: Any) -> tuple[list[dict[str, Any]], list[str]]:
    """Reshape a list of raw slides. Returns ``(slides, notes)``."""
    if not isinstance(slides, list):
        return [], ["the slide list was not a list"]
    out: list[dict[str, Any]] = []
    dropped = 0
    reshaped = 0
    notes: list[str] = []
    for raw in slides:
        slide = normalize_slide(raw, first=not out, notes=notes)
        if slide is None:
            dropped += 1
            continue
        if slide != raw:
            reshaped += 1
        out.append(slide)
    if reshaped:
        notes.append(f"{reshaped} slide(s) were reshaped to the deck schema")
    if dropped:
        notes.append(f"{dropped} unusable slide(s) with no content were dropped")
    return out, notes


# --------------------------------------------------------------------------- plain-language errors
_BLOCK_LOC = re.compile(r"^(blocks|slides)$")


_SHAPE_HINTS: dict[str, str] = {
    "rows": (
        "rows must be a list of lists of strings, one inner list per table row with exactly as "
        'many cells as headers, e.g. [["a","b"],["c","d"]] (not "a | b" strings or objects)'
    ),
    "headers": 'headers must be a list of strings, e.g. ["Name","Type"]',
    "items": 'items must be a list of strings, e.g. ["first","second"]',
    "text": "text must be one string",
    "metrics": 'metrics must be a list of {"value": "...", "label": "..."} objects',
}
_SHAPE_ERRORS = frozenset(
    {"list_type", "string_type", "model_type", "dict_type", "model_attributes_type"}
)


def describe_validation_problems(error: Any, limit: int = 5, blocks: Any = None) -> list[str]:
    """Plain-language problems from a pydantic ``ValidationError`` (no discriminator jargon).

    ``blocks`` (the raw block list the model sent) lets a problem name the block's type and a
    wrong-shape field state the exact shape wanted, so one retry can fix it.
    """
    problems: list[str] = []
    seen: set[str] = set()
    for item in error.errors():
        loc = list(item.get("loc", ()))
        where = "the document"
        hint = ""
        if len(loc) >= 2 and _BLOCK_LOC.match(str(loc[0])) and isinstance(loc[1], int):
            noun = "block" if loc[0] == "blocks" else "slide"
            where = f"{noun} {loc[1] + 1}"
            rest = [str(p) for p in loc[2:] if str(p) not in BLOCK_TYPE_NAMES]
            named = [p for p in rest if not p.isdigit()]
            if noun == "block" and isinstance(blocks, list) and loc[1] < len(blocks):
                raw = blocks[loc[1]]
                stated = raw.get("type") if isinstance(raw, dict) else None
                if isinstance(stated, str) and stated.strip():
                    where += f" {stated.strip()}"
            if named and str(item.get("type", "")) in _SHAPE_ERRORS:
                hint = _SHAPE_HINTS.get(named[0], "")
                rest = named[:1]
            if rest:
                where += f" (field '{'.'.join(rest)}')"
        elif loc:
            where = f"field '{'.'.join(str(p) for p in loc)}'"
        kind = str(item.get("type", ""))
        message = str(item.get("msg", "")).removeprefix("Value error, ")
        if kind == "union_tag_invalid":
            tag = (item.get("ctx") or {}).get("tag")
            message = (
                f"its type '{tag}' is not supported (use a paragraph, heading, list, table, ...)"
            )
        elif kind == "union_tag_not_found":
            message = "it has no recognisable type (a paragraph, heading, list, table, ...)"
        elif kind == "missing":
            message = "a required value is missing"
        elif kind == "extra_forbidden":
            message = "it has a field that is not allowed"
        elif kind in ("string_too_long", "too_long"):
            message = "it is longer than the allowed limit"
        elif kind in ("string_too_short", "too_short"):
            message = "it is empty"
        if hint:
            message = hint
        line = f"{where}: {message}"
        if line not in seen:
            seen.add(line)
            problems.append(line)
    if len(problems) > limit:
        extra = len(problems) - limit
        problems = [*problems[:limit], f"and {extra} more problem(s)"]
    return problems
