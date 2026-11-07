"""Notion MCP Connector strictly read-only for project management context (Phase 08)."""

from __future__ import annotations

import logging
from typing import Any, cast

import httpx

from app.mcp.limiter import VendorRateLimiter
from app.mcp.permissions import PermissionDeniedError

logger = logging.getLogger(__name__)


class NotionConnector:
    """Strictly read-only Notion connector for PMAgent Daily Briefing and issue context."""

    def __init__(
        self,
        token: str,
        base_url: str = "https://api.notion.com/v1",
        rate_limiter: VendorRateLimiter | None = None,
    ) -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")
        self.rate_limiter = rate_limiter or VendorRateLimiter()

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
            "User-Agent": "Maya-AI-MCP-Connector",
        }

    async def get_page(self, page_id: str) -> dict[str, Any]:
        """Fetch Notion page details (READ tier)."""
        await self.rate_limiter.acquire("notion")
        clean_id = page_id.replace("-", "")
        url = f"{self.base_url}/pages/{clean_id}"

        async with httpx.AsyncClient() as client:
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
        clean_id = database_id.replace("-", "")
        url = f"{self.base_url}/databases/{clean_id}/query"
        payload = filter_params or {}

        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=self._headers(), json=payload, timeout=20.0)
            resp.raise_for_status()
            data = resp.json()
            return cast(list[dict[str, Any]], data.get("results", []))

    async def create_page(self, *args: Any, **kwargs: Any) -> None:
        """Mutations to Notion are forbidden in read-only MVP mode."""
        raise PermissionDeniedError(
            "Mutations to Notion are forbidden in read-only MVP mode.",
            tool_name="notion_create_page",
        )
