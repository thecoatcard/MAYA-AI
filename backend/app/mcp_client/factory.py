"""Builds the MCP client service from settings. Returns None while the kill switch is off."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

from app.core.config import Settings
from app.mcp_client.definitions import load_definitions
from app.mcp_client.service import (
    CredentialResolver,
    Identity,
    McpClientService,
    McpClientSettings,
)

if TYPE_CHECKING:
    from app.connections.service import ConnectionService

logger = logging.getLogger(__name__)


def connection_credentials(connections: ConnectionService | None) -> CredentialResolver:
    """Tokens come only from the signed-in user's own saved connection, never from MAYA's env."""

    async def resolve(provider: str, identity: Identity) -> str | None:
        if connections is None or identity.workspace_id is None or identity.user_id is None:
            return None
        connection = await connections.resolve(identity.workspace_id, identity.user_id, provider)
        return connection.token if connection else None

    return resolve


def build_mcp_client(
    settings: Settings, connections: ConnectionService | None
) -> McpClientService | None:
    """The service, or None when ``MAYA_MCP_CLIENT_ENABLED`` is off (nothing else is created)."""
    if not settings.mcp_client_enabled:
        return None
    definitions = load_definitions(settings.mcp_servers_file)
    for problem in definitions.problems:
        logger.warning("mcp_client_definition_problem detail=%s", problem)
    service = McpClientService(
        definitions,
        McpClientSettings(
            call_timeout_seconds=settings.mcp_call_timeout_seconds,
            start_timeout_seconds=settings.mcp_start_timeout_seconds,
            max_output_chars=settings.mcp_max_output_chars,
            max_calls_per_turn=settings.mcp_max_calls_per_turn,
            max_tools_per_turn=settings.mcp_max_tools_per_turn,
        ),
        credentials=connection_credentials(connections),
    )
    logger.info(
        "mcp_client_enabled servers=%s", ",".join(sorted(service.enabled_servers())) or "none"
    )
    return service
