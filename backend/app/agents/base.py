"""Base Specialist Agent Framework with Immutable Tool Allowlist (Phase 09)."""

from __future__ import annotations

import logging
from abc import ABC, abstractmethod
from typing import Any
from uuid import UUID

from app.agents.schemas import SpecialistAgentResult, SpecialistAgentRole
from app.mcp.client_manager import MCPClientManager
from app.mcp.permissions import (
    ToolPermissionRegistry,
    ToolTier,
)
from app.mcp.sanitizer import SanitizationResult
from app.tasks.queue import SubtaskJob

logger = logging.getLogger(__name__)


class UnauthorizedToolError(Exception):
    """Raised when an agent attempts to invoke a tool not explicitly in its assigned allowlist."""


class PermissionCeilingExceededError(Exception):
    """Raised when a tool's required permission tier exceeds the agent's permission ceiling."""


class BaseSpecialistAgent(ABC):
    """Abstract base class for all specialist agents enforcing strict boundaries."""

    role: SpecialistAgentRole
    model_alias: str = "orchestrator"
    permission_ceiling: ToolTier = ToolTier.READ
    assigned_tools: frozenset[str] = frozenset()

    def __init__(
        self,
        permission_registry: ToolPermissionRegistry | None = None,
        client_manager: MCPClientManager | None = None,
    ) -> None:
        self.permission_registry = permission_registry or ToolPermissionRegistry()
        self.client_manager = client_manager

    def __setattr__(self, name: str, value: Any) -> None:
        """Prevent runtime escalation of immutable security boundaries (Decision D4)."""
        if name in ("assigned_tools", "permission_ceiling", "role"):
            if name in self.__dict__:
                raise AttributeError(f"Cannot mutate immutable agent guardrail '{name}'")
        super().__setattr__(name, value)

    async def invoke_tool(
        self,
        server_id: UUID,
        tool_name: str,
        arguments: dict[str, Any],
        has_valid_approval_token: bool = False,
        server_hints: dict[str, Any] | None = None,
    ) -> SanitizationResult:
        """Execute a tool call with tool allowlist and permission ceiling pre-checks."""
        # 1. Enforce strict agent tool allowlist
        if tool_name not in self.assigned_tools:
            raise UnauthorizedToolError(
                f"Agent '{self.role.value}' is not permitted to invoke tool '{tool_name}'. "
                f"Allowed tools: {sorted(self.assigned_tools)}"
            )

        # 2. Enforce permission ceiling (Decisions D4, D5)
        tool_tier = self.permission_registry.classify(tool_name, server_hints)
        if tool_tier > self.permission_ceiling:
            raise PermissionCeilingExceededError(
                f"Tool '{tool_name}' tier {tool_tier.value} exceeds agent '{self.role.value}' "
                f"permission ceiling {self.permission_ceiling.value}."
            )

        if not self.client_manager:
            raise RuntimeError("MCPClientManager is not configured on this agent instance.")

        # 3. Dispatch through MCP client manager
        return await self.client_manager.invoke_tool(
            server_id=server_id,
            tool_name=tool_name,
            arguments=arguments,
            has_valid_approval_token=has_valid_approval_token,
            server_hints=server_hints,
        )

    @abstractmethod
    async def execute_subtask(
        self,
        job: SubtaskJob,
        context: dict[str, Any] | None = None,
    ) -> SpecialistAgentResult:
        """Execute assigned subtask and return structured result with verification proof."""
        ...
