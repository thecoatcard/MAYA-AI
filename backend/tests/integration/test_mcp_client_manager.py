"""Integration tests for MCPClientManager (Phase 08)."""

from __future__ import annotations

from uuid import uuid4

import pytest

from app.mcp.client_manager import MCPClientManager, MCPServerInstanceConfig
from app.mcp.normalizer import SchemaPoisoningError, compute_server_schema_hash
from app.mcp.permissions import (
    PermissionDeniedError,
    ToolPermissionRegistry,
    ToolTier,
)
from app.mcp.transports.base import BaseTransport


class DummyTransport(BaseTransport):
    """Controllable mock transport for client manager testing."""

    def __init__(self, tools_manifest: list[dict[str, object]] | None = None) -> None:
        super().__init__()
        self._alive = True
        self.tools_manifest = tools_manifest or [
            {
                "name": "web_search",
                "description": "Search the web for real-time information",
                "inputSchema": {
                    "type": "object",
                    "properties": {"query": {"type": "string"}},
                    "required": ["query"],
                },
            },
            {
                "name": "gmail_send",
                "description": "Send an email via Gmail",
                "inputSchema": {
                    "type": "object",
                    "properties": {
                        "to": {"type": "string"},
                        "subject": {"type": "string"},
                        "body": {"type": "string"},
                    },
                    "required": ["to", "subject", "body"],
                },
            },
        ]
        self.invocations: list[tuple[str, dict[str, object]]] = []
        self.responses: dict[str, object] = {
            "web_search": {"results": ["Maya AI architecture doc", "Gemini 2.5 details"]},
            "gmail_send": {"sent": True, "message_id": "msg-12345"},
        }

    async def start(self) -> None:
        self._alive = True

    async def stop(self) -> None:
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive

    async def send_request(
        self,
        method: str,
        params: dict[str, object] | None = None,
        timeout: float = 30.0,  # noqa: ASYNC109
    ) -> dict[str, object]:
        if method == "tools/list":
            return {"tools": self.tools_manifest}
        if method == "tools/call":
            p = params or {}
            tool_name = str(p.get("name", ""))
            args = p.get("arguments", {})
            assert isinstance(args, dict)
            self.invocations.append((tool_name, args))
            resp = self.responses.get(tool_name, {"status": "ok"})
            return {"content": [{"type": "text", "text": str(resp)}]}
        if method == "ping":
            return {"status": "ok"}
        return {}

    async def send_notification(self, method: str, params: dict[str, object] | None = None) -> None:
        pass

    async def close(self) -> None:
        self._is_running = False


@pytest.mark.asyncio
async def test_client_manager_connect_and_schema_hash_validation() -> None:
    transport = DummyTransport()
    # Compute the expected hash for DummyTransport's tools
    expected_hash = compute_server_schema_hash(transport.tools_manifest)

    registry = ToolPermissionRegistry()
    manager = MCPClientManager(permission_registry=registry)

    server_id = uuid4()
    cfg = MCPServerInstanceConfig(
        server_id=server_id,
        tenant_id=uuid4(),
        name="test-server",
        vendor="test_vendor",
        transport=transport,
        expected_schema_hash=expected_hash,
    )

    await manager.connect_server(cfg)

    # Server should be connected and registered
    assert manager.is_connected(server_id)
    # web_search should be registered in the ToolPermissionRegistry
    assert registry.is_registered("web_search")
    assert registry.classify("web_search") == ToolTier.READ
    assert registry.is_registered("gmail_send")
    assert registry.classify("gmail_send") == ToolTier.APPROVAL_REQUIRED

    await manager.disconnect_server(server_id)
    assert not manager.is_connected(server_id)


@pytest.mark.asyncio
async def test_client_manager_detects_schema_poisoning() -> None:
    transport = DummyTransport()
    poisoned_hash = "0" * 64  # Wrong hash!

    registry = ToolPermissionRegistry()
    manager = MCPClientManager(permission_registry=registry)

    server_id = uuid4()
    cfg = MCPServerInstanceConfig(
        server_id=server_id,
        tenant_id=uuid4(),
        name="poisoned-server",
        vendor="evil_vendor",
        transport=transport,
        expected_schema_hash=poisoned_hash,
    )

    with pytest.raises(SchemaPoisoningError):
        await manager.connect_server(cfg)

    assert not manager.is_connected(server_id)


@pytest.mark.asyncio
async def test_client_manager_invoke_tool_with_sanitization() -> None:
    transport = DummyTransport()
    expected_hash = compute_server_schema_hash(transport.tools_manifest)

    registry = ToolPermissionRegistry()
    manager = MCPClientManager(permission_registry=registry)

    server_id = uuid4()
    cfg = MCPServerInstanceConfig(
        server_id=server_id,
        tenant_id=uuid4(),
        name="search-server",
        vendor="brave",
        transport=transport,
        expected_schema_hash=expected_hash,
    )
    await manager.connect_server(cfg)

    # Invoke READ tool autonomously
    result = await manager.invoke_tool(
        server_id=server_id,
        tool_name="web_search",
        arguments={"query": "Maya AI release"},
    )

    assert result.has_injection_suspicions is False
    assert "<<<UNTRUSTED_EXTERNAL_INPUT" in result.wrapped_output
    assert "Maya AI architecture doc" in result.clean_text

    await manager.disconnect_server(server_id)


@pytest.mark.asyncio
async def test_client_manager_invoke_approval_required_without_token_denied() -> None:
    transport = DummyTransport()
    expected_hash = compute_server_schema_hash(transport.tools_manifest)

    registry = ToolPermissionRegistry()
    manager = MCPClientManager(permission_registry=registry)

    server_id = uuid4()
    cfg = MCPServerInstanceConfig(
        server_id=server_id,
        tenant_id=uuid4(),
        name="email-server",
        vendor="google",
        transport=transport,
        expected_schema_hash=expected_hash,
    )
    await manager.connect_server(cfg)

    # Attempt to invoke APPROVAL_REQUIRED tool without valid approval token
    with pytest.raises(PermissionDeniedError) as exc_info:
        await manager.invoke_tool(
            server_id=server_id,
            tool_name="gmail_send",
            arguments={"to": "ceo@maya.ai", "subject": "Hello", "body": "World"},
            has_valid_approval_token=False,
        )

    assert "forbidden without valid approval token" in str(exc_info.value).lower()
    # Ensure transport was never invoked!
    assert len(transport.invocations) == 0

    await manager.disconnect_server(server_id)


@pytest.mark.asyncio
async def test_client_manager_invoke_approval_required_with_token_succeeds() -> None:
    transport = DummyTransport()
    expected_hash = compute_server_schema_hash(transport.tools_manifest)

    registry = ToolPermissionRegistry()
    manager = MCPClientManager(permission_registry=registry)

    server_id = uuid4()
    cfg = MCPServerInstanceConfig(
        server_id=server_id,
        tenant_id=uuid4(),
        name="email-server",
        vendor="google",
        transport=transport,
        expected_schema_hash=expected_hash,
    )
    await manager.connect_server(cfg)

    # Invoke with valid token
    result = await manager.invoke_tool(
        server_id=server_id,
        tool_name="gmail_send",
        arguments={"to": "ceo@maya.ai", "subject": "Hello", "body": "World"},
        has_valid_approval_token=True,
    )

    assert len(transport.invocations) == 1
    assert transport.invocations[0][0] == "gmail_send"
    assert "msg-12345" in result.clean_text

    await manager.disconnect_server(server_id)
