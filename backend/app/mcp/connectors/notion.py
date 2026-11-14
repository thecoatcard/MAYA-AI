"""Notion MCP Connector: reads for chat, voice and the PM agent, and the approval-gated write calls.

The chat and voice tools (``app/mcp/notion_tools.py``) use the ``*_checked`` READ helpers, which
raise :class:`NotionCallError` (status only, never the token or the body) instead of
``httpx.HTTPStatusError``, so the tool can show the precise message from ``app/connections``.

The write calls (``create_page``, ``append_block_children``, ``update_page``, ``update_block``,
``delete_block``, ``create_comment``) send a payload as it
is given. They are called only by ``app/mcp/notion_write.execute_write``, which the MCP gateway
runs for an APPROVAL_REQUIRED tool after the user approved that exact payload.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from typing import Any, Final, cast

import httpx

from app.connections.providers import (
    NOTION_VERSION,
    normalize_notion_database_id,
)
from app.mcp.limiter import VendorRateLimiter

logger = logging.getLogger(__name__)

# A 429 means Notion did not process the request, so the same request may be sent again. The wait
# is Notion's own Retry-After; a longer wait than the cap is not slept through, the 429 is raised.
MAX_RATE_LIMIT_RETRIES: Final = 2
MAX_RETRY_AFTER_SECONDS: Final = 10.0


def retry_after_seconds(response: httpx.Response) -> float | None:
    """The Retry-After header as seconds (Notion sends whole seconds), or None when absent/bad."""
    try:
        value = float(response.headers.get("retry-after", ""))
    except ValueError:
        return None
    return value if value >= 0 else None


class NotionCallError(Exception):
    """A Notion API call answered with an HTTP error. Carries the response for status mapping
    only: ``str(error)`` names the status and nothing from the request or the body."""

    def __init__(self, response: httpx.Response) -> None:
        super().__init__(f"Notion answered HTTP {response.status_code}")
        self.response = response


class NotionConnector:
    """Notion connector: reads, plus the three approval-gated write calls (see the module text)."""

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.notion.com/v1",
        rate_limiter: VendorRateLimiter | None = None,
        *,
        transport: httpx.AsyncBaseTransport | None = None,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.rate_limiter = rate_limiter or VendorRateLimiter()
        # Tests pass an ``httpx.MockTransport``; production leaves it None (real network).
        self._transport = transport
        self._sleep = sleep

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": NOTION_VERSION,
            "Content-Type": "application/json",
            "User-Agent": "Maya-AI-MCP-Connector",
        }

    async def get_page(self, page_id: str) -> dict[str, Any]:
        """Fetch Notion page details (READ tier)."""
        await self.rate_limiter.acquire("notion")
        clean_id = page_id.replace("-", "")
        url = f"{self.base_url}/pages/{clean_id}"

        async with httpx.AsyncClient(transport=self._transport) as client:
            resp = await client.get(url, headers=self._headers(), timeout=15.0)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())

    async def query_database(
        self,
        database_id: str,
        filter_params: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Query pages in a Notion database (READ tier)."""
        await self.rate_limiter.acquire("notion")
        # A pasted database URL or a hyphenated id both work; anything else raises
        # ConnectionInputError here instead of coming back from Notion as a confusing 400/404.
        clean_id = normalize_notion_database_id(database_id)
        url = f"{self.base_url}/databases/{clean_id}/query"
        payload = filter_params or {}

        async with httpx.AsyncClient(transport=self._transport) as client:
            resp = await client.post(url, headers=self._headers(), json=payload, timeout=20.0)
            resp.raise_for_status()
            data = resp.json()
            return cast(list[dict[str, Any]], data.get("results", []))

    # ------------------------------------------------------------------ checked READ helpers

    async def _read(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
    ) -> dict[str, Any]:
        """One READ call. Raises :class:`NotionCallError` on an HTTP error response."""
        # A 429 means Notion did not process the request, so a read may wait and retry once.
        return await self._call(method, path, params=params, json_body=json_body, retries=1)

    async def _call(
        self,
        method: str,
        path: str,
        *,
        params: dict[str, Any] | None = None,
        json_body: dict[str, Any] | None = None,
        retries: int = MAX_RATE_LIMIT_RETRIES,
    ) -> dict[str, Any]:
        """One API call with a bounded retry on 429 (the only retried answer: Notion did not act
        on the request). A timeout or network error is never retried here, because a write may
        already have been applied."""
        attempt = 0
        while True:
            await self.rate_limiter.acquire("notion")
            async with httpx.AsyncClient(transport=self._transport) as client:
                resp = await client.request(
                    method,
                    f"{self.base_url}{path}",
                    headers=self._headers(),
                    params=params,
                    json=json_body,
                    timeout=20.0,
                )
            wait = retry_after_seconds(resp) if resp.status_code == 429 else None
            if wait is not None and wait <= MAX_RETRY_AFTER_SECONDS and attempt < retries:
                attempt += 1
                await self._sleep(wait)
                continue
            if resp.status_code >= 400:
                raise NotionCallError(resp)
            return cast(dict[str, Any], resp.json())

    async def get_database_checked(self, database_id: str) -> dict[str, Any]:
        """The database object, including its property schema (READ tier)."""
        clean_id = normalize_notion_database_id(database_id)
        return await self._read("GET", f"/databases/{clean_id}")

    async def get_page_checked(self, page_id: str) -> dict[str, Any]:
        """The page object with its properties (READ tier)."""
        clean_id = normalize_notion_database_id(page_id)
        return await self._read("GET", f"/pages/{clean_id}")

    async def query_database_checked(
        self, database_id: str, body: dict[str, Any]
    ) -> list[dict[str, Any]]:
        """One page of rows from a database query, as the API returns them (READ tier)."""
        clean_id = normalize_notion_database_id(database_id)
        data = await self._read("POST", f"/databases/{clean_id}/query", json_body=body)
        rows = data.get("results", [])
        return cast(list[dict[str, Any]], rows if isinstance(rows, list) else [])

    async def query_database_page(self, database_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """One page of a database query with its paging fields (``has_more``, ``next_cursor``)."""
        clean_id = normalize_notion_database_id(database_id)
        return await self._read("POST", f"/databases/{clean_id}/query", json_body=body)

    async def search_databases(
        self, query: str, limit: int = 10, start_cursor: str | None = None
    ) -> dict[str, Any]:
        """Databases the integration can see (READ tier): one page with its paging fields."""
        body: dict[str, Any] = {
            "filter": {"property": "object", "value": "database"},
            "sort": {"direction": "descending", "timestamp": "last_edited_time"},
            "page_size": max(1, min(int(limit), 50)),
        }
        if query.strip():
            body["query"] = query.strip()
        if start_cursor:
            body["start_cursor"] = start_cursor
        return await self._read("POST", "/search", json_body=body)

    async def search_pages_page(
        self, query: str, limit: int = 10, start_cursor: str | None = None
    ) -> dict[str, Any]:
        """Like :meth:`search_pages` but returns the whole response (paging fields included)."""
        body: dict[str, Any] = {
            "filter": {"property": "object", "value": "page"},
            "sort": {"direction": "descending", "timestamp": "last_edited_time"},
            "page_size": max(1, min(int(limit), 50)),
        }
        if query.strip():
            body["query"] = query.strip()
        if start_cursor:
            body["start_cursor"] = start_cursor
        return await self._read("POST", "/search", json_body=body)

    async def get_block(self, block_id: str) -> dict[str, Any]:
        """One block object (READ tier). Used to read a block's type before an edit."""
        clean_id = normalize_notion_database_id(block_id)
        return await self._read("GET", f"/blocks/{clean_id}")

    async def search_pages(self, query: str, limit: int = 10) -> list[dict[str, Any]]:
        """Pages the integration can see in the whole workspace, by title text (READ tier).

        ``POST /search`` is the only call that finds pages without knowing an id. An empty query
        lists recently edited pages.
        """
        body: dict[str, Any] = {
            "filter": {"property": "object", "value": "page"},
            "sort": {"direction": "descending", "timestamp": "last_edited_time"},
            "page_size": max(1, min(int(limit), 25)),
        }
        if query.strip():
            body["query"] = query.strip()
        data = await self._read("POST", "/search", json_body=body)
        rows = data.get("results", [])
        return cast(list[dict[str, Any]], rows if isinstance(rows, list) else [])

    async def list_block_children(
        self, block_id: str, page_size: int = 100, start_cursor: str | None = None
    ) -> dict[str, Any]:
        """One page of a block's children (READ tier). ``has_more`` and ``next_cursor`` say if
        there are more; the caller decides whether to follow the cursor."""
        clean_id = normalize_notion_database_id(block_id)
        params: dict[str, Any] = {"page_size": int(page_size)}
        if start_cursor:
            params["start_cursor"] = start_cursor
        return await self._read("GET", f"/blocks/{clean_id}/children", params=params)

    # ------------------------------------------------------------------------------ WRITE calls
    # APPROVAL_REQUIRED tier. No caller other than ``notion_write.execute_write`` may use these.

    async def create_page(self, body: dict[str, Any]) -> dict[str, Any]:
        """``POST /pages`` with exactly ``body`` (a page under a page, or a database row)."""
        return await self._call("POST", "/pages", json_body=body)

    async def append_block_children(self, block_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """``PATCH /blocks/{id}/children`` with exactly ``body``."""
        clean_id = normalize_notion_database_id(block_id)
        return await self._call("PATCH", f"/blocks/{clean_id}/children", json_body=body)

    async def update_page(self, page_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """``PATCH /pages/{id}`` with exactly ``body`` (property values of a database row)."""
        clean_id = normalize_notion_database_id(page_id)
        return await self._call("PATCH", f"/pages/{clean_id}", json_body=body)

    async def update_block(self, block_id: str, body: dict[str, Any]) -> dict[str, Any]:
        """``PATCH /blocks/{id}`` with exactly ``body`` (the new content of one block)."""
        clean_id = normalize_notion_database_id(block_id)
        return await self._call("PATCH", f"/blocks/{clean_id}", json_body=body)

    async def delete_block(self, block_id: str) -> dict[str, Any]:
        """``DELETE /blocks/{id}``: Notion moves the block to the trash (it can be restored)."""
        clean_id = normalize_notion_database_id(block_id)
        return await self._call("DELETE", f"/blocks/{clean_id}")

    async def create_comment(self, body: dict[str, Any]) -> dict[str, Any]:
        """``POST /comments`` with exactly ``body`` (needs the integration's comment capability)."""
        return await self._call("POST", "/comments", json_body=body)
