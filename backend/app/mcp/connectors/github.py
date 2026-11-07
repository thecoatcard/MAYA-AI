"""GitHub Remote API MCP Connector with approval token gating (Phase 08)."""

from __future__ import annotations

import logging
from typing import Any, cast

import httpx

from app.mcp.permissions import PermissionDeniedError

logger = logging.getLogger(__name__)


class GitHubConnector:
    """GitHub API client connector enforcing READ queries and APPROVAL_REQUIRED mutations."""

    def __init__(self, token: str, base_url: str = "https://api.github.com") -> None:
        self.token = token
        self.base_url = base_url.rstrip("/")

    def _headers(self) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {self.token}",
            "Accept": "application/vnd.github+json",
            "User-Agent": "Maya-AI-MCP-Connector",
        }

    async def get_issue(self, repo: str, issue_number: int) -> dict[str, Any]:
        """Fetch issue details from GitHub (READ tier)."""
        url = f"{self.base_url}/repos/{repo}/issues/{issue_number}"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self._headers(), timeout=15.0)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())

    async def search_issues(self, query: str) -> list[dict[str, Any]]:
        """Search issues across repositories (READ tier)."""
        url = f"{self.base_url}/search/issues"
        async with httpx.AsyncClient() as client:
            resp = await client.get(url, headers=self._headers(), params={"q": query}, timeout=15.0)
            resp.raise_for_status()
            data = resp.json()
            return cast(list[dict[str, Any]], data.get("items", []))

    async def create_pull_request(
        self,
        repo: str,
        title: str,
        head: str,
        base: str,
        body: str,
        has_valid_approval_token: bool = False,
    ) -> dict[str, Any]:
        """Open a pull request on GitHub (APPROVAL_REQUIRED tier)."""
        if not has_valid_approval_token:
            raise PermissionDeniedError(
                "Execution of APPROVAL_REQUIRED tool 'github_create_pull_request' "
                "forbidden without valid approval token",
                tool_name="github_create_pull_request",
            )

        url = f"{self.base_url}/repos/{repo}/pulls"
        payload = {"title": title, "head": head, "base": base, "body": body}
        async with httpx.AsyncClient() as client:
            resp = await client.post(url, headers=self._headers(), json=payload, timeout=20.0)
            resp.raise_for_status()
            return cast(dict[str, Any], resp.json())
