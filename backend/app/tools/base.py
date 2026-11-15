"""Base abstractions for the Unified Tool Registry.

Defines:
- ToolRisk: Security and approval classification (SAFE, APPROVAL_REQUIRED, RESTRICTED).
- ToolExecutionContext: Execution environment metadata (workspace root, user ID, session ID).
- ToolDefinition: Pydantic schema representing tool metadata, parameters schema, and risk.
- BaseTool: Abstract base class for all tools in MAYA AI.
- FunctionTool: Wrapper to convert any callable into a BaseTool.
- tool: Decorator for creating FunctionTools easily.
- Tool exceptions: ToolError, ToolNotFoundError, ToolPermissionError, WorkspaceNotSetError.
"""

from __future__ import annotations

import inspect
from abc import ABC, abstractmethod
from collections.abc import Awaitable, Callable
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field


class ToolRisk(StrEnum):
    """Risk classification for tools.

    - SAFE: Autonomous read/inspect operations with zero destructive side effects.
    - APPROVAL_REQUIRED: Modifications, file writes, external API interactions, or actions
      requiring human-in-the-loop approval before execution.
    - RESTRICTED: Strictly prohibited actions (e.g., security bypass, credential exfiltration,
      tampering with guardrails). Attempted execution raises an error.
    """

    SAFE = "safe"
    APPROVAL_REQUIRED = "approval_required"
    RESTRICTED = "restricted"


class ToolError(Exception):
    """Base exception for tool execution and registry failures."""

    def __init__(self, message: str, code: str = "tool_error") -> None:
        super().__init__(message)
        self.code = code


class ToolNotFoundError(ToolError):
    """Raised when a requested tool is not found in the registry."""

    def __init__(self, name: str) -> None:
        super().__init__(f"Tool '{name}' is not registered.", code="tool_not_found")
        self.tool_name = name


class ToolPermissionError(ToolError):
    """Raised when an action violates safety or permission rules (e.g. RESTRICTED tool)."""

    def __init__(self, message: str) -> None:
        super().__init__(message, code="permission_denied")


class WorkspaceNotSetError(ToolError):
    """Raised when a workspace-scoped tool is executed without an active workspace_root."""

    def __init__(self, message: str = "Active workspace root directory is not set.") -> None:
        super().__init__(message, code="workspace_not_set")


@dataclass(frozen=True)
class ToolExecutionContext:
    """Contextual metadata passed to tool executions."""

    workspace_root: Path | None = None
    workspace_id: UUID | str | None = None
    user_id: UUID | str | None = None
    session_id: str | None = None
    call_id: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)


class ToolDefinition(BaseModel):
    """Unified definition and contract of a tool available for LLM function calling."""

    model_config = ConfigDict(extra="allow")

    name: str
    description: str
    parameters: dict[str, Any] = Field(default_factory=dict)
    risk: ToolRisk = ToolRisk.SAFE
    category: str = "general"
    tags: list[str] = Field(default_factory=list)

    @property
    def risk_level(self) -> ToolRisk:
        """Alias for risk classification."""
        return self.risk

    def to_gemini(self) -> dict[str, Any]:
        """Format as a Gemini function declaration dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "parameters": self.parameters,
        }

    def to_claude(self) -> dict[str, Any]:
        """Format as an Anthropic Claude tool declaration dictionary."""
        return {
            "name": self.name,
            "description": self.description,
            "input_schema": self.parameters,
        }

    def to_openai(self) -> dict[str, Any]:
        """Format as an OpenAI function calling dictionary."""
        return {
            "type": "function",
            "function": {
                "name": self.name,
                "description": self.description,
                "parameters": self.parameters,
            },
        }


class BaseTool(ABC):
    """Abstract base class for all tools in MAYA AI."""

    name: str
    description: str
    parameters: dict[str, Any]
    risk: ToolRisk = ToolRisk.SAFE
    category: str = "general"
    tags: list[str] = []

    @property
    def risk_level(self) -> ToolRisk:
        """Alias for risk classification."""
        return self.risk

    @property
    def definition(self) -> ToolDefinition:
        """Return the ToolDefinition for this tool."""
        return ToolDefinition(
            name=self.name,
            description=self.description,
            parameters=self.parameters,
            risk=self.risk,
            category=self.category,
            tags=list(self.tags),
        )

    def to_gemini(self) -> dict[str, Any]:
        """Gemini function declaration representation."""
        return self.definition.to_gemini()

    def to_claude(self) -> dict[str, Any]:
        """Claude tool declaration representation."""
        return self.definition.to_claude()

    def to_openai(self) -> dict[str, Any]:
        """OpenAI tool representation."""
        return self.definition.to_openai()

    @abstractmethod
    async def execute(self, context: ToolExecutionContext, **kwargs: Any) -> Any:
        """Asynchronously execute the tool within the provided context."""


ToolCallable = Callable[..., Any | Awaitable[Any]]


class FunctionTool(BaseTool):
    """Tool implementation wrapping an arbitrary Python callable (sync or async)."""

    def __init__(
        self,
        name: str,
        description: str,
        handler: ToolCallable,
        parameters: dict[str, Any] | None = None,
        risk: ToolRisk = ToolRisk.SAFE,
        category: str = "general",
        tags: list[str] | None = None,
    ) -> None:
        self.name = name
        self.description = description
        self.parameters = (
            parameters if parameters is not None else {"type": "object", "properties": {}}
        )
        self.risk = risk
        self.category = category
        self.tags = list(tags) if tags is not None else []
        self._handler = handler
        self._accepts_context = "context" in inspect.signature(handler).parameters

    async def execute(self, context: ToolExecutionContext, **kwargs: Any) -> Any:
        """Invoke the wrapped callable, injecting context if accepted."""
        call_kwargs = dict(kwargs)
        if self._accepts_context and "context" not in call_kwargs:
            call_kwargs["context"] = context

        if inspect.iscoroutinefunction(self._handler):
            return await self._handler(**call_kwargs)
        result = self._handler(**call_kwargs)
        if inspect.isawaitable(result):
            return await result
        return result


def tool(
    name: str,
    description: str,
    parameters: dict[str, Any] | None = None,
    risk: ToolRisk = ToolRisk.SAFE,
    category: str = "general",
    tags: list[str] | None = None,
) -> Callable[[ToolCallable], FunctionTool]:
    """Decorator to declare a FunctionTool from a function."""

    def decorator(fn: ToolCallable) -> FunctionTool:
        return FunctionTool(
            name=name,
            description=description,
            handler=fn,
            parameters=parameters,
            risk=risk,
            category=category,
            tags=tags,
        )

    return decorator
