"""Streamable HTTP transport client for remote and local HTTP-based MCP servers."""

from __future__ import annotations

import logging
from itertools import count
from typing import Any, cast

import httpx

from app.mcp.transports.base import (
    BaseTransport,
    MCPError,
    MCPTimeoutError,
    MCPTransportError,
)

logger = logging.getLogger(__name__)


class StreamableHttpTransport(BaseTransport):
    """Streamable HTTP MCP transport client using persistent keep-alive connections."""

    def __init__(
        self,
        url: str,
        headers: dict[str, str] | None = None,
        timeout: float = 30.0,  # noqa: ASYNC109
    ) -> None:
        self.url = url
        self.headers = dict(headers) if headers else {}
        self.timeout = timeout
        self._client: httpx.AsyncClient | None = None
        self._req_counter = count(1)
        self._alive = False

    async def start(self) -> None:
        """Initialize HTTP client pool with connection limits."""
        if self._client is not None and not self._client.is_closed:
            return

        limits = httpx.Limits(
            max_keepalive_connections=20, max_connections=50, keepalive_expiry=30.0
        )
        timeout_cfg = httpx.Timeout(self.timeout, connect=5.0)
        self._client = httpx.AsyncClient(
            headers=self.headers,
            limits=limits,
            timeout=timeout_cfg,
        )
        self._alive = True

    def is_alive(self) -> bool:
        """Check if transport client is open and initialized."""
        return self._alive and self._client is not None and not self._client.is_closed

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,  # noqa: ASYNC109
    ) -> dict[str, Any]:
        """Send a JSON-RPC 2.0 request via HTTP POST."""
        if not self.is_alive() or self._client is None:
            raise MCPTransportError("Streamable HTTP transport is not running.")

        req_id = next(self._req_counter)
        payload = {
            "jsonrpc": "2.0",
            "id": req_id,
            "method": method,
            "params": params or {},
        }

        try:
            resp = await self._client.post(
                self.url,
                json=payload,
                timeout=timeout,
            )
            if resp.status_code == 429:
                from app.mcp.limiter import VendorRateLimitError

                raise VendorRateLimitError("Upstream server returned HTTP 429 Too Many Requests")

            resp.raise_for_status()
            data = resp.json()

            if "error" in data:
                err_msg = data["error"].get("message", "Unknown HTTP JSON-RPC error")
                raise MCPError(err_msg)

            return cast(dict[str, Any], data.get("result", {}))
        except httpx.TimeoutException:
            raise MCPTimeoutError(f"HTTP request to '{self.url}' timed out.") from None
        except httpx.HTTPStatusError as err:
            raise MCPTransportError(f"HTTP status error: {err.response.status_code}") from err
        except Exception as err:
            if isinstance(err, (MCPError, MCPTransportError)):
                raise
            raise MCPTransportError(f"Streamable HTTP request failed: {err}") from err

    async def stop(self) -> None:
        """Close HTTP client pool."""
        self._alive = False
        if self._client is not None:
            await self._client.aclose()
            self._client = None
