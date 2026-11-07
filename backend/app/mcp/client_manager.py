"""MCP Client Manager, Connection Pool, and Centralized Tool Dispatcher (Phase 08)."""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any
from uuid import UUID

from app.gateway.schemas import ToolDefinition
from app.mcp.limiter import VendorRateLimiter
from app.mcp.normalizer import (
    normalize_mcp_tool_to_genai,
    verify_schema_hash,
)
from app.mcp.permissions import (
    DEFAULT_TOOL_PERMISSIONS,
    PermissionDeniedError,
    ToolPermissionRegistry,
)
from app.mcp.sanitizer import SanitizationResult, sanitize_tool_output
from app.mcp.supervisor import ProcessSupervisor
from app.mcp.transports.base import BaseTransport

logger = logging.getLogger(__name__)


@dataclass(frozen=True, slots=True)
class MCPServerInstanceConfig:
    """Configuration for an active MCP server connection."""

    server_id: UUID
    tenant_id: UUID
    name: str
    vendor: str
    transport: BaseTransport
    expected_schema_hash: str
    rate_limit_rpm: int = 60
    burst_capacity: int = 10


@dataclass
class ManagedServer:
    """An active managed MCP server in the client pool."""

    config: MCPServerInstanceConfig
    supervisor: ProcessSupervisor
    tools: list[ToolDefinition] = field(default_factory=list)
    raw_manifest: list[dict[str, Any]] = field(default_factory=list)


class MCPClientManager:
    """Manages active MCP connections, supervisor lifecycle, authorization, and tool dispatch."""

    def __init__(
        self,
        permission_registry: ToolPermissionRegistry | None = None,
        rate_limiter: VendorRateLimiter | None = None,
    ) -> None:
        self.permission_registry = permission_registry or ToolPermissionRegistry()
        self.rate_limiter = rate_limiter or VendorRateLimiter()
        self._servers: dict[UUID, ManagedServer] = {}

    def is_connected(self, server_id: UUID) -> bool:
        """Check if an MCP server is actively registered and connected in the pool."""
        return server_id in self._servers

    def get_server(self, server_id: UUID) -> ManagedServer | None:
        """Get managed server record by UUID."""
        return self._servers.get(server_id)

    async def connect_server(self, config: MCPServerInstanceConfig) -> ManagedServer:
        """Connect an MCP server, verify its schema hash, and register tools."""
        if config.server_id in self._servers:
            logger.info("Server %s is already connected. Reconnecting...", config.server_id)
            await self.disconnect_server(config.server_id)

        # 1. Start transport
        if not config.transport.is_running:
            await config.transport.start()

        try:
            # 2. Discover tools via JSON-RPC tools/list
            list_resp = await config.transport.send_request("tools/list", timeout=10.0)
            raw_tools = list_resp.get("tools", [])
            if not isinstance(raw_tools, list):
                raw_tools = []

            # 3. Verify schema hash against pinned hash (Poisoning Defense)
            verify_schema_hash(raw_tools, config.expected_schema_hash, raise_on_mismatch=True)

            # 4. Normalize tools to Google GenAI ToolDefinitions and register permissions
            normalized_tools: list[ToolDefinition] = []
            for raw_tool in raw_tools:
                if isinstance(raw_tool, dict):
                    tool_def = normalize_mcp_tool_to_genai(raw_tool)
                    normalized_tools.append(tool_def)

                    # Determine permission tier (Decision D4: deny-by-default)
                    if tool_def.name in DEFAULT_TOOL_PERMISSIONS:
                        tier = DEFAULT_TOOL_PERMISSIONS[tool_def.name]
                        # Extract required arguments
                        req_args = set(tool_def.parameters.get("required", []))
                        self.permission_registry.register_tool(
                            tool_name=tool_def.name,
                            tier=tier,
                            required_args=req_args,
                        )
                    else:
                        logger.warning(
                            "Unclassified MCP tool '%s' on server '%s' is "
                            "denied by default (Decision D4).",
                            tool_def.name,
                            config.name,
                        )

            # 5. Initialize supervisor
            supervisor = ProcessSupervisor(
                server_id=config.server_id,
                name=config.name,
                transport=config.transport,
            )
            await supervisor.start()

            # 6. Configure vendor rate limit
            self.rate_limiter.configure_vendor(
                vendor=config.vendor,
                rate_per_second=max(1.0, config.rate_limit_rpm / 60.0),
                burst_capacity=config.burst_capacity,
            )

            managed = ManagedServer(
                config=config,
                supervisor=supervisor,
                tools=normalized_tools,
                raw_manifest=raw_tools,
            )
            self._servers[config.server_id] = managed
            logger.info(
                "Connected MCP server %s (%s) with %d tools.",
                config.name,
                config.server_id,
                len(normalized_tools),
            )
            return managed

        except Exception:
            await config.transport.close()
            raise

    async def disconnect_server(self, server_id: UUID) -> None:
        """Disconnect server, shut down supervisor, and unregister its tools."""
        managed = self._servers.pop(server_id, None)
        if not managed:
            return

        # Stop supervisor and transport
        await managed.supervisor.stop()

        # Unregister tools from permission registry
        for tool in managed.tools:
            try:
                self.permission_registry.unregister_tool(tool.name)
            except Exception as exc:  # noqa: BLE001
                logger.warning("Error unregistering tool %s: %s", tool.name, exc)

        logger.info("Disconnected MCP server %s (%s).", managed.config.name, server_id)

    async def disconnect_all(self) -> None:
        """Disconnect all active MCP servers in the pool."""
        server_ids = list(self._servers.keys())
        for s_id in server_ids:
            await self.disconnect_server(s_id)

    def list_tools(self, server_id: UUID) -> list[ToolDefinition]:
        """List normalized tools available on a connected server."""
        managed = self._servers.get(server_id)
        return list(managed.tools) if managed else []

    async def invoke_tool(
        self,
        server_id: UUID,
        tool_name: str,
        arguments: dict[str, Any],
        has_valid_approval_token: bool = False,
        server_hints: dict[str, Any] | None = None,
    ) -> SanitizationResult:
        """Authorize, rate limit, execute MCP tool call, and wrap untrusted output."""
        managed = self._servers.get(server_id)
        if not managed:
            raise PermissionDeniedError(
                f"MCP server '{server_id}' is not connected.",
                tool_name=tool_name,
            )

        # 1. Authorize tool execution via ToolPermissionRegistry (Deny-by-default, approval check)
        self.permission_registry.authorize_tool_call(
            tool_name=tool_name,
            args=arguments,
            has_valid_approval_token=has_valid_approval_token,
            server_hints=server_hints,
        )

        # 2. Rate limit check for vendor
        await self.rate_limiter.acquire(managed.config.vendor)

        # 3. Execute tool call over transport
        call_args = dict(arguments)
        if has_valid_approval_token:
            call_args["has_valid_approval_token"] = True
        call_params = {"name": tool_name, "arguments": call_args}
        response = await managed.config.transport.send_request("tools/call", params=call_params)

        # 4. Extract content
        content_items = response.get("content", [])
        if isinstance(content_items, list) and content_items:
            # Join text items
            parts: list[str] = []
            for item in content_items:
                if isinstance(item, dict) and "text" in item:
                    parts.append(str(item["text"]))
                else:
                    parts.append(str(item))
            raw_text = "\n".join(parts)
        else:
            raw_text = str(response)

        # 5. Sanitize and wrap untrusted output
        return sanitize_tool_output(raw_text, tool_name=tool_name)
