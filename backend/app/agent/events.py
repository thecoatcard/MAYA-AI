"""Streaming and lifecycle events for the Unified Agent Runtime.

Provides:
- AgentEvent: Base class for all runtime events.
- TextDeltaEvent: Live streamed text and reasoning tokens.
- ToolCallEvent: Dispatched when a tool call is initiated.
- ToolResultEvent: Dispatched when tool execution completes with result or error.
- ApprovalNeededEvent: Dispatched when an APPROVAL_REQUIRED tool awaits human review.
- TodoStateEvent: Dispatched when the dynamic planning checklist updates.
- DoneEvent: Dispatched when the agent run finishes or terminates.
"""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from app.tools.base import ToolRisk


class AgentEvent(BaseModel):
    """Base model for all events emitted during AgentRuntime execution."""

    model_config = ConfigDict(extra="allow", frozen=True)

    round_index: int = Field(default=0, description="The 0-indexed execution round.")


class TextDeltaEvent(AgentEvent):
    """Emitted when text or reasoning chunk is streamed from the model."""

    delta: str = Field(default="", description="The streamed text delta fragment.")
    thought: str = Field(default="", description="Reasoning/thought summary fragment if available.")


class ToolCallEvent(AgentEvent):
    """Emitted when a tool call is initiated."""

    call_id: str = Field(description="Unique identifier of the tool call.")
    tool_name: str = Field(description="Name of the tool being called.")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="Arguments passed to the tool."
    )
    risk_level: ToolRisk = Field(
        default=ToolRisk.SAFE, description="Risk level of the tool (SAFE, APPROVAL_REQUIRED)."
    )


class ToolResultEvent(AgentEvent):
    """Emitted when a tool execution completes."""

    call_id: str = Field(description="Identifier of the associated tool call.")
    tool_name: str = Field(description="Name of the executed tool.")
    result: Any = Field(default=None, description="Result payload returned by the tool.")
    error: str | None = Field(default=None, description="Error message if execution failed.")


class ApprovalNeededEvent(AgentEvent):
    """Emitted when an APPROVAL_REQUIRED tool requests human confirmation."""

    call_id: str = Field(description="Identifier of the pending tool call.")
    tool_name: str = Field(description="Name of the tool requiring approval.")
    arguments: dict[str, Any] = Field(
        default_factory=dict, description="Arguments submitted for the tool call."
    )
    risk_level: ToolRisk = Field(
        default=ToolRisk.APPROVAL_REQUIRED, description="Risk level requiring approval."
    )
    reason: str | None = Field(
        default=None, description="Optional explanation of why approval is required."
    )


class TodoStateEvent(AgentEvent):
    """Emitted when the dynamic planning checklist (TodoStore) updates."""

    tasks: list[dict[str, Any]] = Field(
        default_factory=list, description="Current list of dynamic tasks."
    )
    counts: dict[str, int] = Field(
        default_factory=dict, description="Summary counts by task status."
    )
    note: str | None = Field(default=None, description="Optional note on current planning status.")


class DoneEvent(AgentEvent):
    """Emitted when the agent runtime completes its execution loop."""

    final_text: str = Field(default="", description="Final accumulated text response.")
    rounds_used: int = Field(default=0, description="Total execution rounds consumed.")
    total_tool_calls: int = Field(default=0, description="Total tool calls performed.")
    total_tokens: int = Field(default=0, description="Total tokens consumed.")
    status: str = Field(
        default="completed",
        description=(
            "Outcome status: 'completed', 'max_rounds_reached', 'budget_exceeded', "
            "'restricted_tool_rejected', or 'error'."
        ),
    )
    error: str | None = Field(
        default=None, description="Error description if status is not completed."
    )
