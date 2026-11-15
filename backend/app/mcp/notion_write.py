"""Notion writes: propose (no write), execute an approved payload, verify by reading back.

Four tools, all APPROVAL_REQUIRED (``app/mcp/permissions.py``): ``notion_create_page``,
``notion_append_blocks``, ``notion_add_row`` and ``notion_update_row``.

The two halves never meet in one call:

* :func:`propose_write` runs when the chat model calls a tool. It validates the model's simple
  arguments, reads what it needs (a database schema, a row's parent) and builds the EXACT Notion
  API requests. It never writes. The chat turn puts those requests, unchanged, into a one-step
  task; the worker shows them on the task card (inline
  approval) and binds the approval to their hash.
* :func:`execute_write` runs in the MCP gateway only for a step whose approval the worker has
  verified and claimed. It refuses without that flag, checks the requests again against the
  allowed shapes, sends them, then reads the result back and reports match or the differences.

One approved payload is sent at most once: a timeout or network error is never retried (the write
may have been applied), and the worker does not retry an approved step.

Image blocks take a public https URL only. Uploading a file (for example a chart PNG from
``app/automation/charts.py``) is not implemented: the documented File Upload API is shown only with
``Notion-Version: 2026-03-11`` and this connector sends ``2022-06-28`` (see ``UPLOAD_UNAVAILABLE``).
"""

from __future__ import annotations

import datetime as dt
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import dataclass
from typing import Any, Final

import httpx

from app.connections.providers import (
    NOTION_DATABASE_NOT_SHARED,
    NOTION_PAGE_NOT_SHARED,
    ConnectionInputError,
    normalize_notion_database_id,
)
from app.gateway.schemas import ToolDefinition
from app.mcp.connectors.notion import NotionCallError, NotionConnector, retry_after_seconds
from app.mcp.notion_edit_defs import (
    AFTER_BLOCK_PARAM,
    ALLOW_DUPLICATE_PARAM,
    EDIT_TOOL_DEFS,
    EDIT_TOOL_NAMES,
    MARKDOWN_PARAM,
)
from app.mcp.notion_markdown import (
    NOTION_CODE_LANGUAGES,
    MarkdownError,
    leftover_markdown,
    markdown_to_blocks,
)
from app.mcp.notion_tools import (
    NETWORK_MESSAGE,
    NO_DATABASE_MESSAGE,
    NotionToolError,
    NotionToolkit,
    property_text,
)

logger = logging.getLogger(__name__)

CREATE_PAGE: Final = "notion_create_page"
APPEND_BLOCKS: Final = "notion_append_blocks"
ADD_ROW: Final = "notion_add_row"
UPDATE_ROW: Final = "notion_update_row"
NOTION_WRITE_TOOL_NAMES: Final = (CREATE_PAGE, APPEND_BLOCKS, ADD_ROW, UPDATE_ROW, *EDIT_TOOL_NAMES)

# Notion request limits (developers.notion.com/reference/request-limits).
MAX_BLOCKS_PER_REQUEST: Final = 100
MAX_TEXT_CHARS: Final = 2000
MAX_RICH_TEXT_ITEMS: Final = 100
MAX_URL_CHARS: Final = 2000
MAX_BLOCK_ELEMENTS: Final = 1000
MAX_REQUEST_BYTES: Final = 450_000  # Notion's limit is 500 KB; headroom for its own framing
# One approval covers at most this many blocks (three append requests).
MAX_BLOCKS_PER_PROPOSAL: Final = 300
MAX_TABLE_ROWS: Final = 100
MAX_TABLE_COLUMNS: Final = 50
MAX_PROPERTIES: Final = 50
VERIFY_MAX_PAGES: Final = 20
PAYLOAD_PREVIEW_CHARS: Final = 4000

TEXT_BLOCK_TYPES: Final = (
    "heading_1",
    "heading_2",
    "heading_3",
    "paragraph",
    "bulleted_list_item",
    "numbered_list_item",
    "to_do",
    "code",
    "callout",
    "quote",
    "toggle",
)
BLOCK_TYPES: Final = (*TEXT_BLOCK_TYPES, "table", "image", "divider")
# What an approved payload may contain: the blocks above plus an equation (Markdown $$ blocks).
ALLOWED_BLOCK_TYPES: Final = (*BLOCK_TYPES, "equation")
MAX_NESTED_LEVELS: Final = 2
_BLOCK_ALIASES: Final = {
    "h1": "heading_1",
    "h2": "heading_2",
    "h3": "heading_3",
    "bullet": "bulleted_list_item",
    "bulleted_list": "bulleted_list_item",
    "todo": "to_do",
    "text": "paragraph",
    "numbered": "numbered_list_item",
    "numbered_list": "numbered_list_item",
    "hr": "divider",
    "rule": "divider",
}
CODE_LANGUAGES: Final = NOTION_CODE_LANGUAGES
WRITABLE_PROPERTY_TYPES: Final = frozenset(
    "title rich_text number select status multi_select date checkbox url email phone_number "
    "people".split()
)

UPLOAD_UNAVAILABLE: Final = (
    "Uploading an image file to Notion is not available: MAYA has not verified Notion's File "
    "Upload API with the API version it uses. Give the image block a public https image URL "
    "instead."
)
NOT_APPROVED_MESSAGE: Final = (
    "This Notion write has no approval. Nothing was written. It runs only after the user "
    "approves the exact payload on the task card in the chat."
)
WRITE_NOT_ALLOWED: Final = (
    "The Notion integration is not allowed to make this change. Open notion.so/my-integrations, "
    "give the integration the Insert content and Update content capabilities, and share the "
    "page or database with it."
)
TIMEOUT_MESSAGE: Final = (
    "Notion did not answer in time. The change may or may not have been saved: open the page in "
    "Notion and check before asking again."
)

_HEX32: Final = r"[0-9a-f]{32}"
_APPEND_PATH: Final = re.compile(rf"^/blocks/({_HEX32})/children$")
_PAGE_PATH: Final = re.compile(rf"^/pages/({_HEX32})$")
_CONTROL: Final = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")
_DATE_SPLIT: Final = re.compile(r"\s*(?:/|\bto\b)\s*", re.IGNORECASE)
_TITLE_WORDS: Final = frozenset(
    {"title", "name", "page", "page title", "page name", "task", "topic"}
)
_TRUE: Final = frozenset({"true", "yes", "y", "1", "checked", "done"})
_FALSE: Final = frozenset({"false", "no", "n", "0", "unchecked", ""})

_BLOCKS_PARAM: Final[dict[str, Any]] = {
    "type": "array",
    "description": (
        "The content, in order. Each item has a type: heading_1, heading_2, heading_3, "
        "paragraph, bulleted_list_item, to_do, code, callout, table or image."
    ),
    "items": {
        "type": "object",
        "properties": {
            "type": {"type": "string", "enum": list(BLOCK_TYPES)},
            "text": {"type": "string", "description": "The text (not for table or image)."},
            "checked": {"type": "boolean", "description": "to_do only."},
            "language": {"type": "string", "description": "code only, for example python."},
            "rows": {
                "type": "array",
                "description": "table only: the rows, each a list of cell texts.",
                "items": {"type": "array", "items": {"type": "string"}},
            },
            "has_header": {"type": "boolean", "description": "table only: first row is a header."},
            "url": {"type": "string", "description": "image only: a public https image URL."},
        },
        "required": ["type"],
    },
}
_PROPERTIES_PARAM: Final[dict[str, Any]] = {
    "type": "array",
    "description": (
        "Column values. 'name' is the exact column name of the database; 'value' is text: a "
        "number, true/false for a checkbox, an ISO date (2026-11-30, or start/end), option names "
        "separated by commas for a multi-select. An empty value clears the column."
    ),
    "items": {
        "type": "object",
        "properties": {"name": {"type": "string"}, "value": {"type": "string"}},
        "required": ["name", "value"],
    },
}
_APPROVAL_NOTE: Final = (
    " Nothing is written by this call: it prepares the exact Notion request, which waits for the "
    "user's approval on the task card in the chat. Say it waits for approval; never say it was "
    "written."
)
_DATABASE_PARAM: Final = {
    "type": "string",
    "description": "Database link or id. Leave out to use the user's connected tasks database.",
}

NOTION_WRITE_TOOL_DEFS: Final[list[dict[str, Any]]] = [
    {
        "name": CREATE_PAGE,
        "description": (
            "Ask to create one Notion page: under a parent page (parent_page_id), or as a row "
            "page in a database (database_id), with optional content blocks." + _APPROVAL_NOTE
        ),
        "properties": {
            "title": {"type": "string", "description": "The page title."},
            "parent_page_id": {"type": "string", "description": "Parent page link or id."},
            "database_id": {"type": "string", "description": "Database link or id (row page)."},
            "properties": _PROPERTIES_PARAM,
            "blocks": _BLOCKS_PARAM,
        },
        "required": ["title"],
    },
    {
        "name": APPEND_BLOCKS,
        "description": (
            "Ask to append content blocks to the end of one Notion page." + _APPROVAL_NOTE
        ),
        "properties": {
            "page_id": {"type": "string", "description": "The page link or id."},
            "blocks": _BLOCKS_PARAM,
        },
        "required": ["page_id", "blocks"],
    },
    {
        "name": ADD_ROW,
        "description": ("Ask to add one row to a Notion database." + _APPROVAL_NOTE),
        "properties": {
            "database_id": _DATABASE_PARAM,
            "properties": _PROPERTIES_PARAM,
            "blocks": _BLOCKS_PARAM,
        },
        "required": ["properties"],
    },
    {
        "name": UPDATE_ROW,
        "description": (
            "Ask to change column values of one existing Notion database row." + _APPROVAL_NOTE
        ),
        "properties": {
            "page_id": {"type": "string", "description": "The row's page link or id."},
            "properties": _PROPERTIES_PARAM,
        },
        "required": ["page_id", "properties"],
    },
]
for _d in NOTION_WRITE_TOOL_DEFS:
    if _d["name"] in (CREATE_PAGE, APPEND_BLOCKS, ADD_ROW):
        _d["properties"]["markdown"] = MARKDOWN_PARAM
    if _d["name"] in (CREATE_PAGE, ADD_ROW):
        _d["properties"]["allow_duplicate"] = ALLOW_DUPLICATE_PARAM
    if _d["name"] == ADD_ROW:
        _d["properties"]["title"] = {"type": "string", "description": "The row title."}
        _d["required"] = []
    if _d["name"] == APPEND_BLOCKS:
        _d["properties"]["after_block_id"] = AFTER_BLOCK_PARAM
        _d["required"] = ["page_id"]
NOTION_WRITE_TOOL_DEFS.extend(EDIT_TOOL_DEFS)
NOTION_WRITE_DEFINITIONS: Final = tuple(
    ToolDefinition(
        name=d["name"],
        description=d["description"],
        parameters={
            "type": "object",
            "properties": dict(d["properties"]),
            "required": list(d["required"]),
        },
    )
    for d in NOTION_WRITE_TOOL_DEFS
)
NOTION_WRITE_CHAT_INSTRUCTION: Final = (
    "Notion write tools: notion_create_page, notion_append_blocks, notion_add_row and "
    "notion_update_row never write by themselves. Each one prepares the exact Notion request and "
    "returns 'waiting_for_approval'; the write happens only after the user approves it on the "
    "task card in the chat, and the result (with a read-back check) appears on the job. Use them "
    "only when the user asked to change Notion. One write request per reply. After the call, "
    "tell the user in one or two sentences what is waiting for approval; never say it was "
    "written. If the tool returns an error, say that sentence and the fix. When the user wants "
    "a new page and gave no parent page or database, never ask them for an id: call "
    "notion_search with scope 'workspace' first. If the user named a parent, or exactly one "
    "page is an obvious parent, call notion_create_page with it (the user still approves it). "
    "Otherwise list the three to five pages found (titles only) and ask which one to use. "
    "If the search finds no page, say the user must share a page with the integration in Notion. "
    "When a job result shows a note was really written (create page, add row or append blocks) "
    "and the save_memory tool is available, call it once with one short line about WHERE the "
    "note lives (the database or page name and the date), so a later 'where are my notes' works; "
    "never save the note's text or any credential, and never before the approval result is known."
)


def notion_write_mcp_manifest() -> list[dict[str, Any]]:
    """The four tools as MCP manifest entries. Their one argument is the exact request list that
    :func:`propose_write` built and the user approved (not the model's simple arguments)."""
    requests = {
        "type": "array",
        "description": "The exact Notion API requests: method, path and JSON body.",
    }
    return [
        {
            "name": d["name"],
            "description": "Approved Notion write (requires human approval). " + d["description"],
            "inputSchema": {
                "type": "object",
                "properties": {"requests": requests},
                "required": ["requests"],
            },
        }
        for d in NOTION_WRITE_TOOL_DEFS
    ]


class NotionWriteError(Exception):
    """A Notion write could not be proposed or run. ``str(error)`` is a plain sentence without
    secrets or stack traces."""


@dataclass(frozen=True, slots=True)
class WriteProposal:
    """What waits for approval: the gateway tool, its exact arguments and a one-line summary."""

    tool: str
    tool_args: dict[str, Any]
    summary: str

    def payload_text(self) -> str:
        """The exact payload as indented JSON (what the approval card shows)."""
        return json.dumps(self.tool_args, ensure_ascii=False, indent=2)


# ------------------------------------------------------------------------------ error mapping


def _notion_error(response: httpx.Response) -> tuple[str, str]:
    """(code, message) of a Notion error body; empty strings when it is not Notion's JSON."""
    try:
        data = response.json()
    except ValueError:
        return "", ""
    if not isinstance(data, dict):
        return "", ""
    message = _CONTROL.sub("", str(data.get("message") or ""))[:300]
    return str(data.get("code") or ""), message


def failure_message(error: BaseException, *, target: str = "page") -> str:
    """The one place a failed Notion write (or its preparation) becomes a plain sentence.

    ``target`` is ``"page"`` or ``"database"``. Never includes the token, headers or a traceback;
    for a 400 it includes Notion's own explanation, which names the property at fault.
    """
    if isinstance(error, NotionWriteError | NotionToolError):
        return str(error)
    if isinstance(error, ConnectionInputError):
        return error.message
    if isinstance(error, httpx.TimeoutException):
        return TIMEOUT_MESSAGE
    if isinstance(error, httpx.HTTPError):
        return NETWORK_MESSAGE + " Nothing was confirmed as written."
    if not isinstance(error, NotionCallError):
        logger.error("notion_write_unexpected error=%s", type(error).__name__)
        return "The Notion write failed unexpectedly."
    response = error.response
    status = response.status_code
    code, detail = _notion_error(response)
    if status == 404 or code == "object_not_found":
        return NOTION_DATABASE_NOT_SHARED if target == "database" else NOTION_PAGE_NOT_SHARED
    if (status == 403 or code == "restricted_resource") and target == "comment":
        return (
            "The Notion integration is not allowed to comment. Open notion.so/my-integrations "
            "and turn on the Insert comments capability, then try again."
        )
    if status == 403 or code == "restricted_resource":
        return WRITE_NOT_ALLOWED
    if status == 401:
        return (
            "Notion rejected the saved token (401). Copy the Internal Integration Secret again "
            "from notion.so/my-integrations into Settings > Connections."
        )
    if status == 400:
        reason = f": {detail}" if detail else "."
        return (
            f"Notion rejected the values (400 {code or 'validation_error'}){reason} Check the "
            "column names and types against the database and try again. Nothing was written by "
            "this request."
        )
    if status == 409:
        return "Notion reported a save conflict (409). Nothing was written; try again."
    if status == 429:
        wait = retry_after_seconds(response)
        after = f" in about {int(wait)} seconds" if wait is not None else " in a minute"
        return f"Notion is rate limiting requests (429). Nothing was written; try again{after}."
    if status >= 500:
        return (
            f"Notion had a server problem ({status}). The change may not have been saved: check "
            "the page in Notion before asking again."
        )
    return f"Notion answered with an unexpected status ({status}). Nothing was written."


# --------------------------------------------------------------------------- payload building


def _dict(value: object) -> dict[str, Any]:
    return value if isinstance(value, dict) else {}


def _utf16_len(text: str) -> int:
    return len(text.encode("utf-16-le")) // 2


def chunk_text(text: str, limit: int = MAX_TEXT_CHARS) -> list[str]:
    """``text`` cut into pieces of at most ``limit`` UTF-16 units (how Notion counts), never
    inside a character. The pieces joined are exactly ``text``."""
    pieces: list[str] = []
    current: list[str] = []
    size = 0
    for char in text:
        units = 2 if ord(char) > 0xFFFF else 1
        if size + units > limit:
            pieces.append("".join(current))
            current, size = [], 0
        current.append(char)
        size += units
    if current:
        pieces.append("".join(current))
    return pieces


def rich_text(text: str, *, what: str = "The text") -> list[dict[str, Any]]:
    """Notion rich_text for plain ``text``: items of at most 2000 characters, at most 100 items."""
    clean = _CONTROL.sub("", text)
    pieces = chunk_text(clean)
    if len(pieces) > MAX_RICH_TEXT_ITEMS:
        raise NotionWriteError(
            f"{what} is too long for one Notion block ({_utf16_len(clean)} characters; the limit "
            f"is {MAX_TEXT_CHARS * MAX_RICH_TEXT_ITEMS}). Split it into several blocks."
        )
    return [{"type": "text", "text": {"content": piece}} for piece in pieces]


def _clean_id(raw: Any, what: str) -> str:
    text = "" if raw is None else str(raw).strip()
    if not text:
        raise NotionWriteError(f"The {what} link or id is required.")
    try:
        return normalize_notion_database_id(text[:600])
    except ConnectionInputError:
        raise NotionWriteError(
            f"That is not a Notion {what} link or id. Copy the link from Notion."
        ) from None


def _https_url(raw: Any) -> str:
    url = "" if raw is None else str(raw).strip()
    if not url:
        raise NotionWriteError(UPLOAD_UNAVAILABLE)
    if not url.lower().startswith("https://") or any(c.isspace() for c in url):
        raise NotionWriteError("An image block needs a public https:// image URL.")
    if _utf16_len(url) > MAX_URL_CHARS:
        raise NotionWriteError(f"The image URL is longer than {MAX_URL_CHARS} characters.")
    return url


def _table_block(spec: dict[str, Any], number: int) -> dict[str, Any]:
    rows = spec.get("rows")
    if not isinstance(rows, list) or not rows or not all(isinstance(r, list) and r for r in rows):
        raise NotionWriteError(f"Block {number} (table) needs 'rows': a list of non-empty rows.")
    width = len(rows[0])
    if any(len(r) != width for r in rows):
        raise NotionWriteError(f"Block {number} (table): every row must have {width} cells.")
    if len(rows) > MAX_TABLE_ROWS or width > MAX_TABLE_COLUMNS:
        raise NotionWriteError(
            f"Block {number} (table) is too big: at most {MAX_TABLE_ROWS} rows and "
            f"{MAX_TABLE_COLUMNS} columns."
        )
    children = [
        {
            "object": "block",
            "type": "table_row",
            "table_row": {
                "cells": [rich_text("" if c is None else str(c), what="A table cell") for c in row]
            },
        }
        for row in rows
    ]
    return {
        "object": "block",
        "type": "table",
        "table": {
            "table_width": width,
            "has_column_header": bool(spec.get("has_header", False)),
            "has_row_header": False,
            "children": children,
        },
    }


def build_block(spec: Any, number: int) -> dict[str, Any]:
    """One Notion block object from the model's simple block; raises on anything not allowed."""
    if not isinstance(spec, dict):
        raise NotionWriteError(f"Block {number} is not an object with a 'type'.")
    kind = str(spec.get("type") or "").strip().lower()
    kind = _BLOCK_ALIASES.get(kind, kind)
    if kind not in BLOCK_TYPES:
        raise NotionWriteError(
            f"Block {number} has the type '{kind or '(none)'}', which is not supported. Use one "
            f"of: {', '.join(BLOCK_TYPES)}."
        )
    if kind == "divider":
        return {"object": "block", "type": "divider", "divider": {}}
    if kind == "table":
        return _table_block(spec, number)
    if kind == "image":
        url = _https_url(spec.get("url"))
        return {
            "object": "block",
            "type": "image",
            "image": {"type": "external", "external": {"url": url}},
        }
    raw = spec.get("text")
    text = "" if raw is None else str(raw)
    if not text.strip() and kind != "paragraph":
        raise NotionWriteError(f"Block {number} ({kind}) needs 'text'.")
    body: dict[str, Any] = {"rich_text": rich_text(text, what=f"Block {number}")}
    if kind == "to_do":
        body["checked"] = bool(spec.get("checked", False))
    if kind == "code":
        language = str(spec.get("language") or "plain text").strip().lower()
        if language not in CODE_LANGUAGES:
            raise NotionWriteError(
                f"Block {number} (code): '{language}' is not a language MAYA sends to Notion. "
                "Use a common one (python, javascript, sql, json, bash ...) or 'plain text'."
            )
        body["language"] = language
    return {"object": "block", "type": kind, kind: body}


def build_blocks(specs: Any, *, limit: int, required: bool) -> list[dict[str, Any]]:
    """All blocks of one proposal, validated. ``limit`` is the most this write may carry."""
    if specs is None or specs == []:
        if required:
            raise NotionWriteError("Give at least one block in 'blocks'.")
        return []
    if not isinstance(specs, list):
        raise NotionWriteError("'blocks' must be a list of blocks.")
    if len(specs) > limit:
        raise NotionWriteError(
            f"That is {len(specs)} blocks; this write can carry at most {limit}. Send the first "
            f"{limit} now and append the rest in a later request."
        )
    return [build_block(spec, number) for number, spec in enumerate(specs, start=1)]


def _element_count(blocks: list[dict[str, Any]]) -> int:
    total = 0
    for block in blocks:
        total += 1
        inner = block.get(str(block.get("type")))
        kids = inner.get("children") if isinstance(inner, dict) else None
        if isinstance(kids, list):
            total += _element_count([k for k in kids if isinstance(k, dict)])
    return total


def _check_size(body: dict[str, Any]) -> None:
    children = body.get("children") or []
    size = len(json.dumps(body, ensure_ascii=False).encode("utf-8"))
    if _element_count(children) > MAX_BLOCK_ELEMENTS or size > MAX_REQUEST_BYTES:
        raise NotionWriteError(
            "This is too much content for one Notion request (limit: 1000 blocks including "
            "table rows, 500 KB). Split it into several smaller writes."
        )


def _number(value: str, name: str) -> int | float:
    try:
        number = float(value.replace(",", ""))
    except ValueError:
        raise NotionWriteError(
            f"'{name}' is a number column; '{value[:40]}' is not a number."
        ) from None
    if number != number or number in (float("inf"), float("-inf")):
        raise NotionWriteError(f"'{name}' is a number column; '{value[:40]}' is not a number.")
    return int(number) if number.is_integer() else number


def _iso(value: str, name: str) -> str:
    text = value.strip()
    try:
        if len(text) <= 10:
            dt.date.fromisoformat(text)
        else:
            dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        raise NotionWriteError(
            f"'{name}' is a date column; '{text[:40]}' is not an ISO date. Use 2026-11-30 or "
            "2026-11-30T14:00:00+05:30 (and start/end for a range)."
        ) from None
    return text


def property_value(kind: str, raw: Any, name: str) -> dict[str, Any]:
    """The Notion property payload for one column of type ``kind`` from the model's text value."""
    if isinstance(raw, bool):
        value = "true" if raw else "false"
    elif isinstance(raw, list):
        value = ", ".join(str(x) for x in raw)
    else:
        value = "" if raw is None else str(raw).strip()
    if kind in ("title", "rich_text"):
        if kind == "title" and not value:
            raise NotionWriteError(f"'{name}' is the title column and cannot be empty.")
        return {kind: rich_text(value, what=f"'{name}'")}
    if kind == "number":
        return {"number": _number(value, name) if value else None}
    if kind == "select":
        return {"select": {"name": value[:100]} if value else None}
    if kind == "status":
        if not value:
            raise NotionWriteError(f"'{name}' is a status column and needs a status name.")
        return {"status": {"name": value[:100]}}
    if kind == "multi_select":
        names = list(dict.fromkeys(p.strip()[:100] for p in value.split(",") if p.strip()))
        return {"multi_select": [{"name": n} for n in names]}
    if kind == "date":
        if not value:
            return {"date": None}
        parts = [p for p in _DATE_SPLIT.split(value) if p]
        if len(parts) > 2:
            raise NotionWriteError(f"'{name}' is a date column: give one date or start/end.")
        date: dict[str, Any] = {"start": _iso(parts[0], name)}
        if len(parts) == 2:
            date["end"] = _iso(parts[1], name)
        return {"date": date}
    if kind == "checkbox":
        lowered = value.lower()
        if lowered not in _TRUE and lowered not in _FALSE:
            raise NotionWriteError(f"'{name}' is a checkbox column: use true or false.")
        return {"checkbox": lowered in _TRUE}
    if kind in ("url", "email", "phone_number"):
        if _utf16_len(value) > MAX_URL_CHARS:
            raise NotionWriteError(f"The value of '{name}' is too long.")
        return {kind: value or None}
    if kind == "people":
        user_ids = [p.strip() for p in value.split(",") if p.strip()]
        for user_id in user_ids:
            if re.fullmatch(r"[0-9a-fA-F-]{32,36}", user_id) is None:
                raise NotionWriteError(
                    f"'{name}' is a people column: give Notion user ids (not names), separated by "
                    "commas."
                )
        return {"people": [{"object": "user", "id": u} for u in user_ids]}
    if kind in ("relation", "rollup", "formula", "created_time", "last_edited_time", "files"):
        raise NotionWriteError(
            f"'{name}' is a {kind} column, which is read-only for MAYA. Leave it out."
        )
    raise NotionWriteError(
        f"'{name}' is a {kind or 'unknown'} column. MAYA can set only: "
        f"{', '.join(sorted(WRITABLE_PROPERTY_TYPES))}."
    )


def _property_items(raw: Any) -> list[tuple[str, Any]]:
    if raw is None:
        return []
    if isinstance(raw, dict):
        return [(str(k), v) for k, v in raw.items()]
    if not isinstance(raw, list):
        raise NotionWriteError("'properties' must be a list of {name, value} items.")
    items: list[tuple[str, Any]] = []
    for entry in raw:
        if not isinstance(entry, dict) or not str(entry.get("name") or "").strip():
            raise NotionWriteError("Every item in 'properties' needs a 'name' and a 'value'.")
        items.append((str(entry["name"]).strip(), entry.get("value")))
    return items


def build_properties(schema: dict[str, str], raw: Any) -> dict[str, Any]:
    """Notion ``properties`` from the model's {name, value} items, typed by the REAL database
    schema (``schema`` is property name -> type). Unknown names and unsupported types raise."""
    items = _property_items(raw)
    if len(items) > MAX_PROPERTIES:
        raise NotionWriteError(f"At most {MAX_PROPERTIES} columns can be set in one write.")
    by_lower: dict[str, list[str]] = {}
    for known in schema:
        by_lower.setdefault(known.lower(), []).append(known)
    out: dict[str, Any] = {}
    for given, value in items:
        name = given if given in schema else None
        if name is None:
            matches = by_lower.get(given.lower(), [])
            name = matches[0] if len(matches) == 1 else None
        if name is None and given.strip().lower() in _TITLE_WORDS:
            # The model said "Title" or "Name" for the page title, but this database calls the
            # column something else: with exactly one title column there is no doubt which it is.
            titles = [n for n, k in schema.items() if k == "title"]
            name = titles[0] if len(titles) == 1 else None
        if name is None:
            columns = ", ".join(f"{n} ({k})" for n, k in list(schema.items())[:30])
            raise NotionWriteError(
                f"The database has no column named '{given[:80]}'. Its columns are: {columns}."
            )
        if name in out:
            raise NotionWriteError(f"The column '{name}' is given twice.")
        out[name] = property_value(schema[name], value, name)
    return out


def _title_column(schema: dict[str, str]) -> str:
    for name, kind in schema.items():
        if kind == "title":
            return name
    raise NotionWriteError("The Notion database has no title column.")


def _request(method: str, path: str, body: dict[str, Any]) -> dict[str, Any]:
    _check_size(body)
    return {"method": method, "path": path, "body": body}


def _count(number: int, word: str) -> str:
    plural = "S" if word.isupper() else "s"
    return f"{number} {word}" + ("" if number == 1 else plural)


# ---------------------------------------------------------------------------------- proposing


async def propose_write(toolkit: NotionToolkit, name: str, args: dict[str, Any]) -> WriteProposal:
    """Validate the model's arguments and build the exact requests. READS only; never writes.

    Raises :class:`NotionWriteError` with a plain sentence (bad input, not connected, not shared).
    """
    if name not in NOTION_WRITE_TOOL_NAMES:
        raise NotionWriteError(f"Unknown tool '{name}'.")
    target = "database" if name == ADD_ROW else "page"
    try:
        connector, saved_database = await toolkit.connection()
        if name in EDIT_TOOL_NAMES:
            from app.mcp.notion_edit import propose_edit  # local: notion_edit imports this module

            return await propose_edit(toolkit, connector, name, args)
        if name == APPEND_BLOCKS:
            return await _propose_append(connector, args)
        if name == UPDATE_ROW:
            return await _propose_update(toolkit, connector, args)
        return await _propose_create(toolkit, connector, saved_database, name, args)
    except NotionWriteError:
        raise
    except Exception as error:  # noqa: BLE001 - every failure becomes one plain sentence
        if isinstance(error, NotionCallError) and name == CREATE_PAGE and args.get("database_id"):
            target = "database"
        raise NotionWriteError(failure_message(error, target=target)) from None


def content_blocks(args: dict[str, Any], *, limit: int, required: bool) -> list[dict[str, Any]]:
    """The blocks of one proposal from ``markdown`` (converted) or ``blocks`` (explicit)."""
    markdown = args.get("markdown")
    explicit = args.get("blocks")
    if markdown is not None and str(markdown).strip():
        if explicit:
            raise NotionWriteError("Give 'markdown' or 'blocks', not both.")
        try:
            blocks = markdown_to_blocks(str(markdown))
        except MarkdownError as error:
            raise NotionWriteError(str(error)) from None
        if len(blocks) > limit:
            raise NotionWriteError(
                f"That is {len(blocks)} blocks; this write can carry at most {limit}. Send the "
                f"first {limit} now and append the rest in a later request."
            )
        if blocks:
            _check_children(blocks)
        return blocks
    return build_blocks(explicit, limit=limit, required=required)


async def _propose_append(connector: NotionConnector, args: dict[str, Any]) -> WriteProposal:
    page_id = _clean_id(args.get("page_id"), "page")
    after_raw = args.get("after_block_id")
    after = _clean_id(after_raw, "block") if after_raw else None
    blocks = content_blocks(
        args,
        limit=MAX_BLOCKS_PER_REQUEST if after else MAX_BLOCKS_PER_PROPOSAL,
        required=True,
    )
    bodies: list[dict[str, Any]] = [
        {"children": blocks[start : start + MAX_BLOCKS_PER_REQUEST]}
        for start in range(0, len(blocks), MAX_BLOCKS_PER_REQUEST)
    ]
    if after:
        bodies[0]["after"] = after
    requests = [_request("PATCH", f"/blocks/{page_id}/children", body) for body in bodies]
    name = await _page_name(connector, page_id)
    where = f" after the block {after}" if after else ""
    summary = f"Notion: append {_count(len(blocks), 'block')} to the page {name}{where}"
    if len(requests) > 1:
        summary += f" ({len(requests)} requests of at most {MAX_BLOCKS_PER_REQUEST} blocks)"
    return WriteProposal(APPEND_BLOCKS, {"requests": requests}, summary[:300])


async def _page_name(connector: NotionConnector, page_id: str) -> str:
    """'Title' (quoted) when the page can be read, else its id. Never raises."""
    try:
        page = await connector.get_page_checked(page_id)
    except Exception:  # noqa: BLE001 - a name is a nicety; the id still identifies the page
        return page_id
    for prop in _dict(page.get("properties")).values():
        if isinstance(prop, dict) and prop.get("type") == "title":
            title = property_text(prop)
            if title:
                return f"'{title[:80]}'"
    return page_id


async def _propose_update(
    toolkit: NotionToolkit, connector: NotionConnector, args: dict[str, Any]
) -> WriteProposal:
    page_id = _clean_id(args.get("page_id"), "page")
    page = await connector.get_page_checked(page_id)
    parent = _dict(page.get("parent"))
    database_id = str(parent.get("database_id") or "").replace("-", "")
    if not database_id:
        raise NotionWriteError(
            "That page is not a row of a database, so it has no columns to update."
        )
    try:
        schema = await toolkit.schema(connector, database_id)
    except NotionCallError as error:
        raise NotionWriteError(failure_message(error, target="database")) from None
    properties = build_properties(schema, args.get("properties"))
    if not properties:
        raise NotionWriteError("Give at least one column to change in 'properties'.")
    request = _request("PATCH", f"/pages/{page_id}", {"properties": properties})
    summary = (
        f"Notion: update {_count(len(properties), 'column')} "
        f"({', '.join(properties)}) of the row {page_id}"
    )
    return WriteProposal(UPDATE_ROW, {"requests": [request]}, summary[:300])


async def _propose_create(
    toolkit: NotionToolkit,
    connector: NotionConnector,
    saved_database: str | None,
    name: str,
    args: dict[str, Any],
) -> WriteProposal:
    blocks = content_blocks(args, limit=MAX_BLOCKS_PER_REQUEST, required=False)
    title = " ".join(str(args.get("title") or "").split())
    parent_page = args.get("parent_page_id")
    database_raw = args.get("database_id")
    if name == CREATE_PAGE and parent_page and database_raw:
        raise NotionWriteError("Give parent_page_id or database_id, not both.")
    if name == CREATE_PAGE and parent_page:
        if not title:
            raise NotionWriteError("The page needs a title.")
        if _property_items(args.get("properties")):
            raise NotionWriteError(
                "A page under a page has no columns: leave 'properties' out, or give "
                "database_id to create a database row."
            )
        parent_id = _clean_id(parent_page, "parent page")
        if args.get("allow_duplicate") is not True:
            await _refuse_duplicate_page(connector, parent_id, title)
        body: dict[str, Any] = {
            "parent": {"page_id": parent_id},
            "properties": {"title": {"title": rich_text(title, what="The title")}},
        }
        summary = f"Notion: create the page '{title[:80]}' under the page {parent_id}"
    else:
        if name == CREATE_PAGE and not database_raw:
            raise NotionWriteError(
                "Say where the page goes: parent_page_id (a page) or database_id (a database). "
                "Do not ask the user for an id: call notion_search with scope 'workspace' to find "
                "pages the integration can see, then use the one the user named, or the only "
                "sensible one, as parent_page_id."
            )
        if database_raw:
            database_id = _clean_id(database_raw, "database")
        elif saved_database:
            database_id = _clean_id(saved_database, "database")
        else:
            raise NotionWriteError(NO_DATABASE_MESSAGE)
        try:
            schema = await toolkit.schema(connector, database_id)
        except NotionCallError as error:
            raise NotionWriteError(failure_message(error, target="database")) from None
        properties = build_properties(schema, args.get("properties"))
        title_column = _title_column(schema)
        if title and title_column not in properties:
            properties[title_column] = property_value("title", title, title_column)
        if title_column not in properties:
            raise NotionWriteError(f"The row needs a value for the title column '{title_column}'.")
        shown = "".join(i["text"]["content"] for i in properties[title_column]["title"])[:80]
        if args.get("allow_duplicate") is not True:
            await _refuse_duplicate_row(connector, database_id, title_column, shown)
        body = {"parent": {"database_id": database_id}, "properties": properties}
        # The name comes from the schema read above (cached); the id only when there is no name.
        database_name = toolkit.database_title(database_id)
        target = f"the {database_name} database" if database_name else f"the database {database_id}"
        summary = f"Notion: add the row '{shown}' to {target} ({_count(len(properties), 'column')})"
    if blocks:
        body["children"] = blocks
        summary += f" with {_count(len(blocks), 'block')}"
    return WriteProposal(name, {"requests": [_request("POST", "/pages", body)]}, summary[:300])


_DUPLICATE_HINT: Final = (
    " Nothing was prepared. Tell the user it already exists and offer to update it "
    "(notion_update_page / notion_update_block) or append to it (notion_append_blocks) instead; "
    "call this tool again with allow_duplicate true only if the user explicitly wants a second "
    "copy."
)


async def _refuse_duplicate_page(connector: NotionConnector, parent_id: str, title: str) -> None:
    """Raise when a sub-page with this title already exists under the parent (never on a failed
    lookup: the check is a safeguard, not a gate)."""
    try:
        cursor: str | None = None
        for _page in range(3):
            data = await connector.list_block_children(parent_id, start_cursor=cursor)
            for block in data.get("results") or []:
                found = (
                    _dict(block.get("child_page")).get("title") if isinstance(block, dict) else None
                )
                if isinstance(found, str) and found.strip().casefold() == title.casefold():
                    raise NotionWriteError(
                        f"A page titled '{title[:80]}' already exists under that parent "
                        f"(id {block.get('id', '')})." + _DUPLICATE_HINT
                    )
            cursor = str(data.get("next_cursor") or "") or None
            if not data.get("has_more") or cursor is None:
                return
    except (NotionCallError, httpx.HTTPError):
        return


async def _refuse_duplicate_row(
    connector: NotionConnector, database_id: str, title_column: str, title: str
) -> None:
    try:
        rows = await connector.query_database_checked(
            database_id,
            {"filter": {"property": title_column, "title": {"equals": title}}, "page_size": 3},
        )
    except (NotionCallError, httpx.HTTPError):
        return
    for row in rows:
        if isinstance(row, dict) and row.get("object", "page") == "page":
            raise NotionWriteError(
                f"A row titled '{title[:80]}' already exists in that database "
                f"(id {row.get('id', '')}, {row.get('url', '')})." + _DUPLICATE_HINT
            )


# ------------------------------------------------------------------------------- chat tool call


def write_step(proposal: WriteProposal) -> Any:
    """The one planned step that carries the proposal: an explicit tool step whose ``tool_args``
    are the exact payload. The worker classifies the tool (APPROVAL_REQUIRED), records the
    approval request with these arguments and their hash, and stops until the user decides."""
    from app.agents.state import SubTask  # local: keeps app.mcp free of the agents package

    return SubTask(
        id="step_1",
        assigned_agent="pm",
        description=proposal.summary,
        tool=proposal.tool,
        tool_args=proposal.tool_args,
    )


async def chat_write_call(
    toolkit: NotionToolkit,
    name: str,
    args: dict[str, Any],
    submit: Callable[[str, Any], Awaitable[Any]],
) -> dict[str, Any]:
    """What the chat model gets back for a Notion write tool call. NEVER writes to Notion.

    It validates and builds the payload (:func:`propose_write`, reads only), then hands the one
    step to ``submit(summary, step)``, which creates the approval-gated task. The result says
    'waiting_for_approval' and carries the exact payload; any problem is a plain 'error'.
    """
    try:
        proposal = await propose_write(toolkit, name, args)
    except NotionWriteError as error:
        return {"status": "error", "message": f"{error} Nothing was prepared or written."}
    except Exception as error:  # noqa: BLE001 - a tool bug must not fail the chat turn
        logger.error("notion_write_propose_failed tool=%s error=%s", name, type(error).__name__)
        return {"status": "error", "message": "The Notion write could not be prepared."}
    outcome = await submit(proposal.summary, write_step(proposal))
    task_id = getattr(outcome, "task_id", None)
    if task_id is None:
        reason = str(getattr(outcome, "reason", "") or "")
        return {
            "status": "error",
            "message": (
                "The approval request could not be created. Nothing was written."
                + (f" Reason: {reason}" if reason else "")
            ),
        }
    if getattr(outcome, "already_waiting", False) is True:
        return {
            "status": "already_waiting",
            "job_id": str(task_id),
            "tool": proposal.tool,
            "summary": proposal.summary,
            "message": (
                "This exact change is ALREADY waiting for the user's approval from earlier in "
                "this conversation; no second request was created. Do not announce it as new: say "
                "in one short sentence that it is still waiting for their approval."
            ),
        }
    if getattr(outcome, "is_new", True) is False:
        return {
            "status": "error",
            "job_id": str(task_id),
            "message": (
                "A job already exists for this message, so this write was NOT added to it. "
                "Nothing was written. Ask the user to send the request again as a new message."
            ),
        }
    payload = proposal.payload_text()
    shortened = len(payload) > PAYLOAD_PREVIEW_CHARS
    return {
        "status": "waiting_for_approval",
        "job_id": str(task_id),
        "tool": proposal.tool,
        "summary": proposal.summary,
        "payload": payload[:PAYLOAD_PREVIEW_CHARS],
        "payload_truncated": shortened,
        "message": (
            "Nothing has been written to Notion. The exact request above waits for the user's "
            "approval on the task card in the chat (the full payload is shown there); it runs "
            "once, only after they approve, and the job then reports the result with a read-back "
            "check. Tell the user what will be written and that it waits for their approval. "
            "Never say it was written."
        ),
    }


# -------------------------------------------------------------------- validating before sending


def _check_children(children: Any, level: int = 1) -> list[dict[str, Any]]:
    if not isinstance(children, list) or not children or len(children) > MAX_BLOCKS_PER_REQUEST:
        raise NotionWriteError("The approved payload has an invalid block list. Nothing written.")
    for block in children:
        kind = block.get("type") if isinstance(block, dict) else None
        if kind not in ALLOWED_BLOCK_TYPES or not isinstance(block.get(kind), dict):
            raise NotionWriteError(
                "The approved payload has a block type that is not allowed. Nothing written."
            )
        if kind == "image" and block["image"].get("type") != "external":
            raise NotionWriteError(UPLOAD_UNAVAILABLE)
        nested = block[kind].get("children")
        if nested is not None and kind != "table":
            if level >= MAX_NESTED_LEVELS + 1:
                raise NotionWriteError(
                    "The approved payload nests blocks too deeply. Nothing written."
                )
            _check_children(nested, level + 1)
    return children


check_children = _check_children  # the same gate, for notion_edit


def validate_requests(tool: str, requests: Any) -> list[dict[str, Any]]:
    """The approved request list, checked against what ``tool`` may send: only its own method and
    path, only the allowed body keys and block types. Raises :class:`NotionWriteError`."""
    bad = NotionWriteError("The approved payload is not a valid Notion write. Nothing written.")
    if tool not in NOTION_WRITE_TOOL_NAMES or not isinstance(requests, list) or not requests:
        raise bad
    if tool in EDIT_TOOL_NAMES:
        from app.mcp.notion_edit import validate_edit  # local: notion_edit imports this module

        return validate_edit(tool, requests)
    most = MAX_BLOCKS_PER_PROPOSAL // MAX_BLOCKS_PER_REQUEST if tool == APPEND_BLOCKS else 1
    if len(requests) > most:
        raise bad
    targets: set[str] = set()
    for request in requests:
        if not isinstance(request, dict) or set(request) != {"method", "path", "body"}:
            raise bad
        method, path, body = request["method"], str(request["path"]), request["body"]
        if not isinstance(body, dict):
            raise bad
        if tool == APPEND_BLOCKS:
            match = _APPEND_PATH.match(path)
            if (
                method != "PATCH"
                or match is None
                or set(body) not in ({"children"}, {"children", "after"})
            ):
                raise bad
            if "after" in body and (
                len(requests) != 1 or re.fullmatch(_HEX32, str(body["after"])) is None
            ):
                raise bad
            targets.add(match.group(1))
            _check_children(body["children"])
        elif tool == UPDATE_ROW:
            properties = body.get("properties")
            if method != "PATCH" or _PAGE_PATH.match(path) is None or set(body) != {"properties"}:
                raise bad
            if not isinstance(properties, dict) or not properties:
                raise bad
        else:
            parent = body.get("parent")
            if (
                method != "POST"
                or path != "/pages"
                or not set(body)
                <= {
                    "parent",
                    "properties",
                    "children",
                }
            ):
                raise bad
            if not isinstance(parent, dict) or not isinstance(body.get("properties"), dict):
                raise bad
            allowed = {"database_id"} if tool == ADD_ROW else {"database_id", "page_id"}
            if len(parent) != 1 or not set(parent) <= allowed:
                raise bad
            if re.fullmatch(_HEX32, str(next(iter(parent.values())))) is None:
                raise bad
            if "children" in body:
                _check_children(body["children"])
        _check_size(body)
    if len(targets) > 1:
        raise bad
    return requests


# ------------------------------------------------------------------------------- verification


def _plain(parts: Any) -> str:
    """The text of a rich_text array, sent (``text.content``) or read (``plain_text``)."""
    if not isinstance(parts, list):
        return ""
    out: list[str] = []
    for part in parts:
        if not isinstance(part, dict):
            continue
        text = _dict(part.get("text"))
        out.append(str(part.get("plain_text") or text.get("content") or ""))
    return "".join(out).strip()


def _short(text: str) -> str:
    clean = _CONTROL.sub("", text)
    return clean if len(clean) <= 80 else clean[:77] + "..."


def block_view(block: dict[str, Any]) -> dict[str, Any]:
    """What is compared for one block: its type, text and the type's own settings."""
    kind = str(block.get("type") or "")
    data = _dict(block.get(kind))
    view: dict[str, Any] = {"type": kind}
    if kind == "image":
        external = _dict(data.get("external"))
        view["url"] = str(external.get("url") or "")
    elif kind == "equation":
        view["text"] = str(data.get("expression") or "")
    elif kind == "divider":
        pass
    elif kind == "table":
        view["columns"] = data.get("table_width")
        view["header"] = bool(data.get("has_column_header"))
    else:
        view["text"] = _plain(data.get("rich_text"))
        if kind == "to_do":
            view["checked"] = bool(data.get("checked"))
        if kind == "code":
            view["language"] = str(data.get("language") or "")
    return view


def compare_blocks(sent: list[dict[str, Any]], read: list[dict[str, Any]]) -> list[str]:
    """The differences between the blocks sent and the blocks read back (empty when equal)."""
    diffs: list[str] = []
    if len(sent) != len(read):
        diffs.append(f"sent {_count(len(sent), 'block')}, Notion has {len(read)}")
    for number, (ours, theirs) in enumerate(zip(sent, read, strict=False), start=1):
        want, got = block_view(ours), block_view(theirs)
        for key, value in want.items():
            if key != "type" and want["type"] != got.get("type"):
                continue  # a different block type: the type difference says it all
            if got.get(key) != value:
                diffs.append(
                    f"block {number} ({want['type']}) {key}: sent '{_short(str(value))}', "
                    f"Notion has '{_short(str(got.get(key)))}'"
                )
    return diffs


def _same_date(sent: dict[str, Any] | None, read: Any) -> bool:
    def instant(value: Any) -> Any:
        text = str(value or "")
        try:
            if len(text) <= 10:
                return dt.date.fromisoformat(text)
            return dt.datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return text

    if not sent:
        return not read
    if not isinstance(read, dict):
        return False
    for key in ("start", "end"):
        ours, theirs = sent.get(key), read.get(key)
        if (ours or theirs) and instant(ours) != instant(theirs):
            return False
    return True


def _norm_id(raw: Any) -> str:
    return str(raw or "").replace("-", "").lower()


def compare_properties(sent: dict[str, Any], page: dict[str, Any]) -> list[str]:
    """The differences between the property values sent and the page read back."""
    read_props = _dict(page.get("properties"))
    diffs: list[str] = []
    for name, payload in sent.items():
        kind = next(iter(payload))
        value = payload[kind]
        read = read_props.get(name)
        if not isinstance(read, dict):
            diffs.append(f"column '{name}': not present on the page Notion returned")
            continue
        got: Any = read.get(kind)
        if kind in ("title", "rich_text"):
            same, want_text, got_text = _plain(value) == _plain(got), _plain(value), _plain(got)
        elif kind == "date":
            same, want_text, got_text = _same_date(value, got), str(value), str(got)
        elif kind == "multi_select":
            want_names = sorted(str(o.get("name")) for o in value)
            got_names = sorted(str(o.get("name")) for o in got or [] if isinstance(o, dict))
            same, want_text, got_text = want_names == got_names, str(want_names), str(got_names)
        elif kind == "people":
            want_ids = sorted(_norm_id(u.get("id")) for u in value)
            got_ids = sorted(_norm_id(u.get("id")) for u in got or [] if isinstance(u, dict))
            same, want_text, got_text = want_ids == got_ids, str(want_ids), str(got_ids)
        elif kind in ("select", "status"):
            want_name = value.get("name") if isinstance(value, dict) else None
            got_name = got.get("name") if isinstance(got, dict) else None
            same, want_text, got_text = want_name == got_name, str(want_name), str(got_name)
        else:
            same = (value or None) == (got or None) if kind != "checkbox" else value == got
            if kind == "number" and value is not None and isinstance(got, int | float):
                same = float(value) == float(got)
            want_text, got_text = str(value), str(got)
        if not same:
            shown = property_text(read) or got_text
            diffs.append(
                f"column '{name}' ({kind}): sent '{_short(want_text)}', "
                f"Notion has '{_short(str(shown))}'"
            )
    return diffs


async def _children(connector: NotionConnector, block_id: str) -> list[dict[str, Any]] | None:
    """Every child block (following the cursor), or None when there are too many to check."""
    blocks: list[dict[str, Any]] = []
    cursor: str | None = None
    for _page in range(VERIFY_MAX_PAGES):
        data = await connector.list_block_children(block_id, start_cursor=cursor)
        results = data.get("results")
        found = results if isinstance(results, list) else []
        blocks.extend(b for b in found if isinstance(b, dict))
        cursor = str(data.get("next_cursor") or "") or None
        if not data.get("has_more") or cursor is None:
            return blocks
    return None


async def _compare_nested(
    connector: NotionConnector, sent: list[dict[str, Any]], read: list[dict[str, Any]]
) -> list[str]:
    """Compare the nested children (list items under list items, toggle content) of the blocks
    that were sent with those Notion has: type and text, one level, a bounded number of reads."""
    diffs: list[str] = []
    reads = 0
    for number, (ours, theirs) in enumerate(zip(sent, read, strict=False), start=1):
        kind = str(ours.get("type"))
        want = _dict(ours.get(kind)).get("children")
        if kind == "table" or not isinstance(want, list) or not want or not theirs.get("id"):
            continue
        if reads >= 20:
            diffs.append("nested blocks were not all compared (too many)")
            break
        reads += 1
        found = await _children(connector, str(theirs["id"]))
        for line in compare_blocks(want, found or []):
            diffs.append(f"nested in block {number} ({kind}): {line}")
    return diffs


async def _verify_blocks(
    connector: NotionConnector,
    parent_id: str,
    sent: list[dict[str, Any]],
    created_ids: list[str],
) -> list[str]:
    """Read the parent's children back and compare the written ones (by id when Notion returned
    the new ids, else the last ones). Table rows are read back too."""
    existing = await _children(connector, parent_id)
    if existing is None:
        return ["the page has too many blocks to read back; the new blocks were not compared"]
    wanted = set(created_ids)
    read = [b for b in existing if str(b.get("id") or "").replace("-", "") in wanted]
    if len(read) != len(sent):
        read = existing[-len(sent) :] if sent else []
    diffs = compare_blocks(sent, read) + leftover_markdown(read)
    diffs += await _compare_nested(connector, sent, read)
    for number, (ours, theirs) in enumerate(zip(sent, read, strict=False), start=1):
        if ours.get("type") != "table" or theirs.get("type") != "table" or not theirs.get("id"):
            continue
        rows = await _children(connector, str(theirs["id"]))
        sent_rows = [
            [_plain(cell) for cell in row["table_row"]["cells"]]
            for row in ours["table"]["children"]
        ]
        read_rows = [
            [_plain(cell) for cell in (r.get("table_row") or {}).get("cells") or []]
            for r in rows or []
        ]
        if sent_rows != read_rows:
            diffs.append(f"block {number} (table): the cell texts read back differ from those sent")
    return diffs


def ids_of(response: dict[str, Any]) -> list[str]:
    results = response.get("results")
    return [
        str(b.get("id") or "").replace("-", "")
        for b in (results if isinstance(results, list) else [])
        if isinstance(b, dict) and b.get("id")
    ]


def _report(done: str, checked: list[str], diffs: list[str], unverified: str | None) -> str:
    if unverified is not None:
        return f"{done}\nNOT VERIFIED: the read-back check could not run ({unverified})."
    if diffs:
        lines = "\n".join(f"- {d}" for d in diffs[:20])
        more = f"\n- and {len(diffs) - 20} more" if len(diffs) > 20 else ""
        return (
            f"{done}\nVERIFICATION FOUND {_count(len(diffs), 'DIFFERENCE')} between what was "
            f"sent and what Notion now has:\n{lines}{more}"
        )
    return f"{done}\nVERIFIED by reading it back: {' and '.join(checked)} match what was sent."


# ---------------------------------------------------------------------------------- executing


async def execute_write(
    tool: str, args: dict[str, Any], connector: NotionConnector | None, *, approved: bool
) -> str:
    """Send one APPROVED payload and return the result text (with the read-back check).

    ``approved`` must be the gateway's ``has_valid_approval_token`` flag, which only the worker
    sets after it verified and claimed the approval for exactly these arguments. Raises
    :class:`NotionWriteError` (plain sentence) when nothing, or only a part, was written.
    """
    if approved is not True or connector is None:
        raise NotionWriteError(NOT_APPROVED_MESSAGE)
    requests = validate_requests(tool, args.get("requests"))
    if tool in EDIT_TOOL_NAMES:
        from app.mcp.notion_edit import execute_edit  # local: notion_edit imports this module

        return await execute_edit(tool, requests, connector)
    target = "database" if tool == ADD_ROW else "page"
    responses: list[dict[str, Any]] = []
    fallback_note = ""
    for number, request in enumerate(requests, start=1):
        body = request["body"]
        try:
            if tool == APPEND_BLOCKS:
                block_id = str(request["path"]).split("/")[2]
                try:
                    responses.append(await connector.append_block_children(block_id, body))
                except NotionCallError as error:
                    if "after" not in body or error.response.status_code != 400:
                        raise
                    plain = {k: v for k, v in body.items() if k != "after"}
                    responses.append(await connector.append_block_children(block_id, plain))
                    fallback_note = (
                        "NOTE: Notion did not accept inserting after the chosen block, so the "
                        "content was added at the END of the page instead."
                    )
            elif tool == UPDATE_ROW:
                page_id = str(request["path"]).rsplit("/", 1)[-1]
                responses.append(await connector.update_page(page_id, body))
            else:
                if "database_id" in body["parent"]:
                    target = "database"
                responses.append(await connector.create_page(body))
        except Exception as error:  # noqa: BLE001 - mapped to one plain sentence
            message = failure_message(error, target=target)
            if number > 1:
                message = (
                    f"Only {number - 1} of {len(requests)} requests were written (the first "
                    f"{(number - 1) * MAX_BLOCKS_PER_REQUEST} blocks); request {number} failed: "
                    f"{message}"
                )
            raise NotionWriteError(message) from None
    text = await _verify(tool, requests, responses, connector)
    return f"{text}\n{fallback_note}" if fallback_note else text


async def _verify(
    tool: str,
    requests: list[dict[str, Any]],
    responses: list[dict[str, Any]],
    connector: NotionConnector,
) -> str:
    """The result text: what was done, then the read-back comparison (never raises: the write
    has happened, so a failed check is reported as NOT VERIFIED, not as a failed write)."""
    first, body = responses[0], requests[0]["body"]
    url = str(first.get("url") or "")
    link = f" Link: {url}" if url else ""
    checked: list[str] = []
    diffs: list[str] = []
    unverified: str | None = None
    try:
        if tool == APPEND_BLOCKS:
            sent = [b for r in requests for b in r["body"]["children"]]
            parent_id = str(requests[0]["path"]).split("/")[2]
            done = f"Appended {_count(len(sent), 'block')} to the Notion page {parent_id}."
            created = [i for response in responses for i in ids_of(response)]
            diffs = await _verify_blocks(connector, parent_id, sent, created)
            checked.append(_count(len(sent), "block"))
        else:
            page_id = str(first.get("id") or "").replace("-", "")
            done = (
                f"Updated the Notion row {page_id}.{link}"
                if tool == UPDATE_ROW
                else f"Created the Notion page {page_id}.{link}"
            )
            if not page_id:
                return _report(done, [], [], "Notion returned no page id")
            page = await connector.get_page_checked(page_id)
            diffs = compare_properties(body["properties"], page)
            checked.append(_count(len(body["properties"]), "property value"))
            children = body.get("children") or []
            if children:
                diffs += await _verify_blocks(connector, page_id, children, [])
                checked.append(_count(len(children), "block"))
    except Exception as error:  # noqa: BLE001 - the write is done; only the check failed
        unverified = failure_message(error)
    return _report(done, checked, diffs, unverified)


__all__ = [
    "ADD_ROW",
    "APPEND_BLOCKS",
    "BLOCK_TYPES",
    "CREATE_PAGE",
    "MAX_BLOCKS_PER_PROPOSAL",
    "MAX_BLOCKS_PER_REQUEST",
    "MAX_TEXT_CHARS",
    "NOTION_WRITE_CHAT_INSTRUCTION",
    "NOTION_WRITE_DEFINITIONS",
    "NOTION_WRITE_TOOL_DEFS",
    "NOTION_WRITE_TOOL_NAMES",
    "NOT_APPROVED_MESSAGE",
    "PAYLOAD_PREVIEW_CHARS",
    "UPDATE_ROW",
    "UPLOAD_UNAVAILABLE",
    "NotionWriteError",
    "WriteProposal",
    "build_block",
    "build_blocks",
    "build_properties",
    "chat_write_call",
    "chunk_text",
    "compare_blocks",
    "compare_properties",
    "execute_write",
    "failure_message",
    "notion_write_mcp_manifest",
    "propose_write",
    "rich_text",
    "validate_requests",
    "write_step",
]
