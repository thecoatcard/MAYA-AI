"""A small stateful fake of api.notion.com for the Notion write tests (``httpx.MockTransport``).

It stores what is written and serves it back in Notion's read shape (``plain_text`` on rich text,
``type`` on properties, table rows as the table's children), so the read-back check of
``app/mcp/notion_write`` runs against real round-tripped data. Nothing reaches the network.
"""

from __future__ import annotations

import copy
import json
from collections.abc import Awaitable, Callable
from typing import Any

import httpx

from app.mcp.connectors.notion import NotionConnector
from app.mcp.limiter import VendorRateLimiter
from app.mcp.notion_tools import NotionToolkit

TOKEN = "ntn_" + "w" * 40
DB = "0123456789abcdef0123456789abcdef"
PAGE = "fedcba9876543210fedcba9876543210"
ROW = "aaaabbbbccccddddeeeeffff00001111"
SCHEMA: dict[str, str] = {
    "Name": "title",
    "Status": "status",
    "Due": "date",
    "Points": "number",
    "Tags": "multi_select",
    "Done": "checkbox",
    "Notes": "rich_text",
    "Link": "url",
    "Owner": "people",
}


def _read_rich(items: Any) -> list[dict[str, Any]]:
    return [{**item, "plain_text": item["text"]["content"]} for item in items or []]


def _read_property(payload: dict[str, Any]) -> dict[str, Any]:
    kind = next(iter(payload))
    value = payload[kind]
    if kind in ("title", "rich_text"):
        value = _read_rich(value)
    return {"type": kind, kind: value}


class FakeNotionApi:
    """Routes by method and path. ``writes`` are the non-GET requests that reached 'Notion'."""

    def __init__(self) -> None:
        self.requests: list[httpx.Request] = []
        self.databases: dict[str, dict[str, str]] = {DB: dict(SCHEMA)}
        self.database_titles: dict[str, str] = {}
        self.pages: dict[str, dict[str, Any]] = {
            PAGE: {"object": "page", "id": PAGE, "url": "https://notion.so/plan", "properties": {}},
            ROW: {
                "object": "page",
                "id": ROW,
                "url": "https://notion.so/row",
                "parent": {"type": "database_id", "database_id": DB},
                "properties": {
                    "Name": {"type": "title", "title": [{"plain_text": "Old title"}]},
                    "Status": {"type": "status", "status": {"name": "Todo"}},
                },
            },
        }
        self.children: dict[str, list[dict[str, Any]]] = {PAGE: [], ROW: []}
        # Every block by id (nested ones too), the options of select/status columns per database,
        # comments, and a switch that makes comments answer 403 (capability missing).
        self.blocks: dict[str, dict[str, Any]] = {}
        self.database_options: dict[str, dict[str, list[str]]] = {}
        self.comments: list[dict[str, Any]] = []
        self.comments_forbidden = False
        self._insert_after: str | None = None  # set for one _store_blocks call (append ``after``)
        self.reject_after = False  # answer 400 to an append that carries ``after``
        # Queued answers for the next write requests (errors, rate limits); then normal service.
        self.write_answers: list[httpx.Response | Exception] = []
        self.fail_reads = False
        self.sleeps: list[float] = []
        self._next = 0

    # ------------------------------------------------------------------------------ helpers

    @property
    def writes(self) -> list[httpx.Request]:
        """Requests that change something (searches and database queries are POST reads)."""
        reads = ("search", "query")
        return [
            r
            for r in self.requests
            if r.method != "GET" and not (r.method == "POST" and r.url.path.endswith(reads))
        ]

    def write_bodies(self) -> list[dict[str, Any]]:
        return [json.loads(r.content) for r in self.writes]

    def _new_id(self) -> str:
        self._next += 1
        return f"{self._next:032x}"

    async def _sleep(self, seconds: float) -> None:
        self.sleeps.append(seconds)

    def connector(self) -> NotionConnector:
        limiter = VendorRateLimiter()
        limiter.configure_vendor("notion", 1000.0, 1000.0)
        return NotionConnector(
            TOKEN, rate_limiter=limiter, transport=httpx.MockTransport(self), sleep=self._sleep
        )

    def provider(
        self, database: str | None = DB
    ) -> Callable[[], Awaitable[tuple[NotionConnector, str | None]]]:
        connector = self.connector()

        async def provide() -> tuple[NotionConnector, str | None]:
            return connector, database

        return provide

    def toolkit(self, database: str | None = DB) -> NotionToolkit:
        return NotionToolkit(self.provider(database))

    def _store_blocks(self, parent: str, blocks: list[dict[str, Any]]) -> list[dict[str, Any]]:
        stored: list[dict[str, Any]] = []
        for block in blocks:
            kind = block["type"]
            body = copy.deepcopy(block[kind])
            block_id = self._new_id()
            nested = body.pop("children", None)
            if kind == "table":
                self.children[block_id] = [
                    {
                        "object": "block",
                        "id": self._new_id(),
                        "type": "table_row",
                        "table_row": {"cells": [_read_rich(c) for c in r["table_row"]["cells"]]},
                    }
                    for r in nested or []
                ]
                for row in self.children[block_id]:
                    self.blocks[row["id"]] = row
            elif "rich_text" in body:
                body["rich_text"] = _read_rich(body["rich_text"])
            if kind == "image" and "caption" in body:
                body["caption"] = _read_rich(body["caption"])
            record = {"object": "block", "id": block_id, "type": kind, kind: body}
            if nested and kind != "table":
                self._store_blocks(block_id, nested)
            if kind == "table" or nested:
                record["has_children"] = True
            self.blocks[block_id] = record
            stored.append(record)
        siblings = self.children.setdefault(parent, [])
        after, self._insert_after = self._insert_after, None
        if after is not None:
            position = next((i for i, b in enumerate(siblings) if b["id"] == after), None)
            if position is not None:
                siblings[position + 1 : position + 1] = stored
                return stored
        siblings.extend(stored)
        return stored

    def add_block(self, parent: str, block: dict[str, Any]) -> dict[str, Any]:
        """Seed one block (Notion write shape) under a page; returns the stored block."""
        return self._store_blocks(parent, [block])[0]

    def add_row(self, database: str, **columns: Any) -> str:
        """Seed a row of ``database``; ``columns`` maps a column name to a Notion property payload
        (for example ``Name={"title": [...]}``). Returns the row id."""
        page_id = self._new_id()
        self.pages[page_id] = {
            "object": "page",
            "id": page_id,
            "url": f"https://notion.so/{page_id}",
            "parent": {"type": "database_id", "database_id": database},
            "properties": {n: _read_property(p) for n, p in columns.items()},
        }
        self.children[page_id] = []
        return page_id

    # --------------------------------------------------------------------------- query / search

    @staticmethod
    def _text(prop: dict[str, Any]) -> str:
        kind = prop.get("type")
        value = prop.get(str(kind))
        if kind in ("title", "rich_text"):
            return "".join(str(i.get("plain_text", "")) for i in value or [])
        if kind in ("select", "status"):
            return str((value or {}).get("name", ""))
        if kind == "multi_select":
            return ",".join(str(o.get("name")) for o in value or [])
        if kind == "date":
            return str((value or {}).get("start", ""))
        return "" if value is None else str(value)

    def _matches(self, page: dict[str, Any], filt: dict[str, Any]) -> bool:
        if "and" in filt:
            return all(self._matches(page, f) for f in filt["and"])
        if "or" in filt:
            return any(self._matches(page, f) for f in filt["or"])
        prop = page["properties"].get(filt["property"])
        if prop is None:
            return False
        kind = prop["type"]
        ((op, expected),) = filt[kind].items()
        text = self._text(prop)
        if op == "is_empty":
            return text == ""
        if op == "is_not_empty":
            return text != ""
        if kind == "checkbox":
            return bool((prop["checkbox"] is expected) == (op == "equals"))
        if kind == "number":
            number = float(prop["number"] or 0)
            return bool(
                {
                    "equals": number == expected,
                    "does_not_equal": number != expected,
                    "greater_than": number > expected,
                    "less_than": number < expected,
                    "greater_than_or_equal_to": number >= expected,
                    "less_than_or_equal_to": number <= expected,
                }[op]
            )
        if kind == "date":
            return bool(
                {
                    "equals": text == expected,
                    "before": text < expected,
                    "after": text > expected,
                    "on_or_before": text <= expected,
                    "on_or_after": text >= expected,
                }[op]
            )
        low, want = text.lower(), str(expected).lower()
        return {
            "equals": low == want,
            "does_not_equal": low != want,
            "contains": want in low,
            "does_not_contain": want not in low,
            "starts_with": low.startswith(want),
            "ends_with": low.endswith(want),
        }[op]

    def _query(self, database: str, body: dict[str, Any]) -> httpx.Response:
        rows = [
            p
            for p in self.pages.values()
            if p.get("parent", {}).get("database_id") == database and not p.get("archived")
        ]
        if "filter" in body:
            rows = [r for r in rows if self._matches(r, body["filter"])]
        start = int(body.get("start_cursor") or 0)
        size = int(body.get("page_size") or 100)
        more = start + size < len(rows)
        return httpx.Response(
            200,
            json={
                "results": rows[start : start + size],
                "has_more": more,
                "next_cursor": str(start + size) if more else None,
            },
        )

    def _database_object(self, database: str) -> dict[str, Any]:
        props: dict[str, Any] = {}
        for name, kind in self.databases[database].items():
            prop: dict[str, Any] = {"type": kind}
            options = self.database_options.get(database, {}).get(name)
            if options is not None:
                prop[kind] = {"options": [{"name": o} for o in options]}
            props[name] = prop
        body: dict[str, Any] = {
            "object": "database",
            "id": database,
            "url": f"https://notion.so/{database}",
            "properties": props,
        }
        if database in self.database_titles:
            body["title"] = [{"plain_text": self.database_titles[database]}]
        return body

    # ------------------------------------------------------------------------------ routing

    def __call__(self, request: httpx.Request) -> httpx.Response:
        self.requests.append(request)
        missing = httpx.Response(404, json={"object": "error", "code": "object_not_found"})
        parts = request.url.path.removeprefix("/v1/").split("/")
        if request.method == "GET":
            if self.fail_reads:
                return httpx.Response(500, json={"code": "internal_server_error"})
            if parts[0] == "databases" and parts[1] in self.databases:
                return httpx.Response(200, json=self._database_object(parts[1]))
            if parts[0] == "blocks" and len(parts) == 2 and parts[1] in self.blocks:
                return httpx.Response(200, json=self.blocks[parts[1]])
            if parts[0] == "pages" and parts[1] in self.pages:
                return httpx.Response(200, json=self.pages[parts[1]])
            if parts[0] == "blocks" and parts[1] in self.children:
                start = int(request.url.params.get("start_cursor") or 0)
                size = int(request.url.params.get("page_size") or 100)
                found = self.children[parts[1]]
                more = start + size < len(found)
                return httpx.Response(
                    200,
                    json={
                        "results": found[start : start + size],
                        "has_more": more,
                        "next_cursor": str(start + size) if more else None,
                    },
                )
            return missing
        if request.method == "POST" and parts[0] == "databases" and parts[-1] == "query":
            if parts[1] not in self.databases:
                return missing
            return self._query(parts[1], json.loads(request.content))
        if request.method == "POST" and parts == ["search"]:
            search = json.loads(request.content)
            query = str(search.get("query") or "").lower()
            if (search.get("filter") or {}).get("value") == "database":
                listed = [
                    self._database_object(d)
                    for d in self.databases
                    if query in self.database_titles.get(d, "").lower()
                ]
                return httpx.Response(200, json={"results": listed, "has_more": False})
            found = [
                page
                for page in self.pages.values()
                if page.get("parent", {}).get("type") != "database_id"
                and query in json.dumps(page["properties"]).lower()
            ]
            return httpx.Response(200, json={"results": found})
        if self.write_answers:
            answer = self.write_answers.pop(0)
            if isinstance(answer, Exception):
                raise answer
            return answer
        body = json.loads(request.content) if request.content else {}
        if request.method == "POST" and parts == ["comments"]:
            if self.comments_forbidden:
                return httpx.Response(403, json={"code": "restricted_resource"})
            comment = {
                "object": "comment",
                "id": self._new_id(),
                "parent": body["parent"],
                "rich_text": _read_rich(body["rich_text"]),
            }
            self.comments.append(comment)
            return httpx.Response(200, json=comment)
        if request.method == "DELETE" and parts[0] == "blocks" and parts[1] in self.blocks:
            block = self.blocks[parts[1]]
            block["archived"] = True
            block["in_trash"] = True
            for siblings in self.children.values():
                siblings[:] = [b for b in siblings if b["id"] != parts[1]]
            return httpx.Response(200, json=block)
        if (
            request.method == "PATCH"
            and parts[0] == "blocks"
            and len(parts) == 2
            and parts[1] in self.blocks
        ):
            block = self.blocks[parts[1]]
            kind = block["type"]
            update = body[kind]
            if "rich_text" in update:
                block[kind]["rich_text"] = _read_rich(update["rich_text"])
            if "checked" in update:
                block[kind]["checked"] = update["checked"]
            return httpx.Response(200, json=block)
        if request.method == "POST" and parts == ["pages"]:
            parent = body["parent"]
            if parent.get("database_id") not in self.databases and (
                parent.get("page_id") not in self.pages
            ):
                return missing
            page_id = self._new_id()
            page = {
                "object": "page",
                "id": page_id,
                "url": f"https://notion.so/{page_id}",
                "parent": parent,
                "properties": {n: _read_property(p) for n, p in body["properties"].items()},
            }
            self.pages[page_id] = page
            self.children[page_id] = []
            self._store_blocks(page_id, body.get("children") or [])
            return httpx.Response(200, json=page)
        if request.method == "PATCH" and parts[0] == "blocks" and parts[1] in self.children:
            if "after" in body and self.reject_after:
                return httpx.Response(400, json={"code": "validation_error", "message": "after"})
            after = str(body["after"]).replace("-", "") if "after" in body else None
            self._insert_after = after
            stored = self._store_blocks(parts[1], body["children"])
            return httpx.Response(200, json={"object": "list", "results": stored})
        if request.method == "PATCH" and parts[0] == "pages" and parts[1] in self.pages:
            page = self.pages[parts[1]]
            props = body.get("properties") or {}
            page["properties"].update({n: _read_property(p) for n, p in props.items()})
            if body.get("archived") is True or body.get("in_trash") is True:
                page["archived"] = page["in_trash"] = True
            if "icon" in body:
                page["icon"] = body["icon"]
            return httpx.Response(200, json=page)
        return missing
