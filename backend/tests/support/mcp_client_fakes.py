"""Builders for the MCP client tests: a definition and a service over the in-repo fake server."""

from __future__ import annotations

import sys
from collections.abc import Mapping
from pathlib import Path
from typing import Any

from app.mcp_client.definitions import DefinitionSet, ServerDefinition
from app.mcp_client.service import McpClientService, McpClientSettings

FAKE_SERVER = Path(__file__).with_name("fake_mcp_server.py")
READ_TOOLS = frozenset(
    {"echo", "count_calls", "sleep_seconds", "crash", "big", "poisoned", "env_names"}
)


def fake_definition(**overrides: Any) -> ServerDefinition:
    """The fake server as an enabled, pinned definition. ``navigate`` is a guarded URL tool."""
    data: dict[str, Any] = {
        "name": "fake",
        "label": "Fake",
        "command": sys.executable,
        "args": (str(FAKE_SERVER),),
        "version": "1.0.0",
        "enabled": True,
        "read_tools": READ_TOOLS,
        "navigate_tools": frozenset({"navigate"}),
        "url_guard": True,
        "keywords": ("fakeserver",),
    }
    data.update(overrides)
    return ServerDefinition(**data)


def fake_service(
    *,
    definition: ServerDefinition | None = None,
    environ: Mapping[str, str] | None = None,
    **settings: Any,
) -> McpClientService:
    chosen = definition or fake_definition()
    values: dict[str, Any] = {"call_timeout_seconds": 20.0, "start_timeout_seconds": 30.0}
    values.update(settings)

    async def resolver(host: str) -> list[str]:  # never touches the network
        return {"public.example": ["93.184.216.34"], "rebind.example": ["10.0.0.5"]}.get(host, [])

    return McpClientService(
        DefinitionSet(servers={chosen.name: chosen}),
        McpClientSettings(**values),
        environ=environ if environ is not None else {},
        resolver=resolver,
    )
