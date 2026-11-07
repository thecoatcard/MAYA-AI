"""Brave Search MCP Connector with prompt injection protection and citations (Phase 08)."""

from __future__ import annotations

import logging
from dataclasses import dataclass

import httpx

from app.mcp.limiter import VendorRateLimiter
from app.mcp.sanitizer import SanitizationResult, sanitize_tool_output

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class BraveSearchResult:
    """Structured result of a Brave Search operation including citations and sanitized output."""

    citations: list[dict[str, str]]
    sanitized_result: SanitizationResult


class BraveSearchConnector:
    """Brave Search API connector with rate limiting and untrusted output sanitization."""

    def __init__(
        self,
        api_key: str,
        base_url: str = "https://api.search.brave.com/res/v1/web/search",
        rate_limiter: VendorRateLimiter | None = None,
    ) -> None:
        self.api_key = api_key
        self.base_url = base_url
        self.rate_limiter = rate_limiter or VendorRateLimiter()

    def _headers(self) -> dict[str, str]:
        return {
            "X-Subscription-Token": self.api_key,
            "Accept": "application/json",
            "User-Agent": "Maya-AI-MCP-Connector",
        }

    async def search(self, query: str, count: int = 5) -> BraveSearchResult:
        """Execute a web search, extract citations, and sanitize external content."""
        # 1. Acquire leaky-bucket rate limiter quota
        await self.rate_limiter.acquire("brave")

        # 2. Query Brave Search API
        async with httpx.AsyncClient() as client:
            resp = await client.get(
                self.base_url,
                headers=self._headers(),
                params={"q": query, "count": count},
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()

        web_results = data.get("web", {}).get("results", [])
        citations: list[dict[str, str]] = []
        raw_text_parts: list[str] = []

        for idx, item in enumerate(web_results, 1):
            title = str(item.get("title", "")).strip()
            url = str(item.get("url", "")).strip()
            snippet = str(item.get("description", "")).strip()

            citations.append({"title": title, "url": url, "snippet": snippet})
            raw_text_parts.append(f"[{idx}] {title}\nURL: {url}\nSnippet: {snippet}\n")

        full_raw_text = "\n".join(raw_text_parts) if raw_text_parts else "No results found."

        # 3. Sanitize and wrap untrusted content
        sanitized = sanitize_tool_output(full_raw_text, tool_name="web_search")

        return BraveSearchResult(citations=citations, sanitized_result=sanitized)
