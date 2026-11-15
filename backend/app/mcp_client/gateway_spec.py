"""Registers the approved-execution tool ``mcp_client_call`` with the MCP gateway.

Imported only by ``app.main`` when the client is enabled. The tool is APPROVAL_REQUIRED
(``app/mcp/permissions.py``): the gate lets it run only for a step whose payload the user
approved, and the handler refuses again unless the gate marked the call approved.
"""

from __future__ import annotations

from typing import Any
from uuid import UUID, uuid5

from app.mcp.client_manager import MCPServerInstanceConfig
from app.mcp.gateway import (
    _NAMESPACE,
    InProcessToolTransport,
    ToolServerSpec,
    _required_from_manifest,
)
from app.mcp.normalizer import compute_server_schema_hash
from app.mcp.scope import current_scope, current_user
from app.mcp.transports.base import MCPError
from app.mcp_client.chat import EXECUTE_TOOL, execute_call, gateway_manifest
from app.mcp_client.service import McpClientError, McpClientService


def mcp_client_spec(service: McpClientService) -> ToolServerSpec:
    manifest = gateway_manifest()

    async def handle(args: dict[str, Any]) -> str:
        # ``has_valid_approval_token`` is set by the client manager only for a call the permission
        # gate passed with a verified approval.
        workspace_id, _task = current_scope()
        try:
            return await execute_call(
                service,
                workspace_id,
                current_user(),
                args,
                approved=args.get("has_valid_approval_token") is True,
            )
        except McpClientError as error:
            raise MCPError(str(error)) from None

    name = "mcp-client"

    async def build() -> MCPServerInstanceConfig:
        return MCPServerInstanceConfig(
            server_id=uuid5(_NAMESPACE, name),
            tenant_id=UUID(int=0),
            name=name,
            vendor=name,
            transport=InProcessToolTransport(manifest, {EXECUTE_TOOL: handle}),
            expected_schema_hash=compute_server_schema_hash(manifest),
        )

    return ToolServerSpec(
        name=name,
        vendor=name,
        tools=frozenset({EXECUTE_TOOL}),
        build=build,
        server_id=uuid5(_NAMESPACE, name),
        required_args=_required_from_manifest(manifest),
    )
