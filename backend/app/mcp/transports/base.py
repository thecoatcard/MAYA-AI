"""Base abstractions and exceptions for MCP transports."""

from __future__ import annotations

from abc import ABC, abstractmethod
from typing import Any


class MCPError(Exception):
    """Base exception for Model Context Protocol subsystem."""


class MCPTransportError(MCPError):
    """Raised when transport I/O, socket, or stream communication fails."""


class MCPProcessCrashedError(MCPTransportError):
    """Raised when an MCP subprocess crashes or terminates unexpectedly."""


class MCPTimeoutError(MCPTransportError):
    """Raised when an MCP request exceeds configured timeout."""


class BaseTransport(ABC):
    """Abstract interface for bidirectional MCP transport implementations."""

    @abstractmethod
    async def start(self) -> None:
        """Initialize transport connection or spawn subprocess."""
        ...

    @abstractmethod
    async def stop(self) -> None:
        """Gracefully terminate transport and clean up resources."""
        ...

    @abstractmethod
    def is_alive(self) -> bool:
        """Check if transport connection is active and healthy."""
        ...

    @property
    def is_running(self) -> bool:
        """Check if transport is running."""
        return self.is_alive()

    async def close(self) -> None:
        """Gracefully close transport resources."""
        await self.stop()

    @abstractmethod
    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 30.0,  # noqa: ASYNC109
    ) -> dict[str, Any]:
        """Send a JSON-RPC 2.0 request and await response result payload."""
        ...
