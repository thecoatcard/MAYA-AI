"""Application startup wiring for the Unified Tool Registry."""

from __future__ import annotations

import logging
from typing import Any

from app.tools.bridge import bridge_chat_tools, bridge_voice_tools
from app.tools.registry import ToolRegistry, get_global_registry
from app.tools.todo import register_todo_tools
from app.tools.workspace import register_workspace_tools

logger = logging.getLogger(__name__)


def register_global_tools(
    registry: ToolRegistry | None = None,
    *,
    engine: Any | None = None,
    tool_gateway: Any | None = None,
    configuration: Any | None = None,
    gateway: Any | None = None,
) -> ToolRegistry:
    """Register all available tools into the global ToolRegistry singleton during lifespan."""
    target_registry = registry if registry is not None else get_global_registry()

    # 1. Register Cowork/Antigravity Workspace tools
    register_workspace_tools(target_registry)

    # 2. Register Dynamic Planning Todo tools
    register_todo_tools(target_registry)

    # 3. Register Core Chat Tools
    bridge_chat_tools(target_registry)

    # 4. Register Voice Tools (with real voice registry adapter)
    try:
        from app.voice.tools import VoiceToolRegistry

        vr = VoiceToolRegistry()
        bridge_voice_tools(target_registry, voice_registry=vr)
    except Exception as exc:
        logger.warning("Could not register voice tools into global tool registry: %s", exc)

    logger.info(
        "Registered %d tools into global ToolRegistry ('%s')",
        len(target_registry.list_tools()),
        target_registry.name,
    )
    return target_registry
