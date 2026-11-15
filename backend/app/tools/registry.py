"""Unified Tool Registry implementation.

Provides the central ToolRegistry class for registering, listing, formatting,
and executing tools across chat, voice, and background tasks.
"""

from __future__ import annotations

import logging
from typing import Any

from app.tools.base import (
    BaseTool,
    FunctionTool,
    ToolCallable,
    ToolDefinition,
    ToolExecutionContext,
    ToolNotFoundError,
    ToolPermissionError,
    ToolRisk,
)

logger = logging.getLogger(__name__)


class ToolRegistry:
    """Central registry for MAYA AI tools.

    Maintains the catalogue of tools, enforces risk policies (e.g. blocking
    RESTRICTED tools), and formats definitions for different LLM providers
    (Gemini, Claude, OpenAI).
    """

    def __init__(self, name: str = "default") -> None:
        self.name = name
        self._tools: dict[str, BaseTool] = {}

    def register(
        self,
        tool: BaseTool | ToolCallable,
        *,
        name: str | None = None,
        description: str | None = None,
        parameters: dict[str, Any] | None = None,
        risk: ToolRisk = ToolRisk.SAFE,
        category: str = "general",
        tags: list[str] | None = None,
        overwrite: bool = True,
    ) -> BaseTool:
        """Register a tool in the registry.

        Accepts a BaseTool instance or any sync/async callable which will be wrapped
        into a FunctionTool.
        """
        if isinstance(tool, BaseTool):
            tool_instance = tool
            tool_name = name or tool_instance.name
        elif callable(tool):
            if not name:
                name = getattr(tool, "__name__", "unnamed_tool")
            if not description:
                description = getattr(tool, "__doc__", "") or f"Tool {name}"
            tool_instance = FunctionTool(
                name=name,
                description=description.strip(),
                handler=tool,
                parameters=parameters,
                risk=risk,
                category=category,
                tags=tags,
            )
            tool_name = name
        else:
            raise TypeError(f"Expected BaseTool or callable, got {type(tool).__name__}")

        if tool_name in self._tools and not overwrite:
            raise ValueError(f"Tool '{tool_name}' is already registered in registry '{self.name}'.")

        self._tools[tool_name] = tool_instance
        logger.debug(
            "Registered tool '%s' (risk=%s) in registry '%s'",
            tool_name,
            tool_instance.risk,
            self.name,
        )
        return tool_instance

    def unregister(self, name: str) -> BaseTool | None:
        """Remove a tool from the registry. Returns the removed tool or None."""
        return self._tools.pop(name, None)

    def get(self, name: str) -> BaseTool | None:
        """Lookup a tool by name."""
        return self._tools.get(name)

    def get_or_raise(self, name: str) -> BaseTool:
        """Lookup a tool by name or raise ToolNotFoundError."""
        tool = self.get(name)
        if tool is None:
            raise ToolNotFoundError(name)
        return tool

    def __contains__(self, name: str) -> bool:
        return name in self._tools

    def __len__(self) -> int:
        return len(self._tools)

    def list_tools(
        self,
        *,
        risk: ToolRisk | None = None,
        category: str | None = None,
        tag: str | None = None,
    ) -> list[BaseTool]:
        """Return a filtered list of registered tools."""
        results: list[BaseTool] = []
        for t in self._tools.values():
            if risk is not None and t.risk != risk:
                continue
            if category is not None and t.category != category:
                continue
            if tag is not None and tag not in t.tags:
                continue
            results.append(t)
        return results

    def definitions(
        self,
        *,
        risk: ToolRisk | None = None,
        category: str | None = None,
        tag: str | None = None,
    ) -> list[ToolDefinition]:
        """Return ToolDefinitions for filtered tools."""
        return [t.definition for t in self.list_tools(risk=risk, category=category, tag=tag)]

    def to_gemini_format(
        self,
        *,
        risk: ToolRisk | None = None,
        category: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        """Format matching tools for Google GenAI / Gemini function declarations."""
        return [t.to_gemini() for t in self.list_tools(risk=risk, category=category, tag=tag)]

    def to_claude_format(
        self,
        *,
        risk: ToolRisk | None = None,
        category: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        """Format matching tools for Anthropic Claude tool calling."""
        return [t.to_claude() for t in self.list_tools(risk=risk, category=category, tag=tag)]

    def to_openai_format(
        self,
        *,
        risk: ToolRisk | None = None,
        category: str | None = None,
        tag: str | None = None,
    ) -> list[dict[str, Any]]:
        """Format matching tools for OpenAI function calling."""
        return [t.to_openai() for t in self.list_tools(risk=risk, category=category, tag=tag)]

    async def execute(
        self,
        name: str,
        context: ToolExecutionContext,
        **kwargs: Any,
    ) -> Any:
        """Execute a tool with safety and permission enforcement.

        Raises:
            ToolNotFoundError: If the tool is not registered.
            ToolPermissionError: If the tool is marked as RESTRICTED.
        """
        tool = self.get_or_raise(name)
        if tool.risk == ToolRisk.RESTRICTED:
            raise ToolPermissionError(f"Tool '{name}' is RESTRICTED and cannot be executed.")

        return await tool.execute(context, **kwargs)


_GLOBAL_REGISTRY: ToolRegistry | None = None


def get_global_registry() -> ToolRegistry:
    """Return the global default ToolRegistry singleton."""
    global _GLOBAL_REGISTRY
    if _GLOBAL_REGISTRY is None:
        _GLOBAL_REGISTRY = ToolRegistry("global")
    return _GLOBAL_REGISTRY


def reset_global_registry() -> None:
    """Reset the global ToolRegistry singleton (useful for testing)."""
    global _GLOBAL_REGISTRY
    _GLOBAL_REGISTRY = None
