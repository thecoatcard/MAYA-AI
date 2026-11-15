"""Unified Agent Runtime package for MAYA AI.

Provides the iterative ReAct agent runtime loop, streaming events, and safety policies.
"""

from __future__ import annotations

from app.agent.events import (
    AgentEvent,
    ApprovalNeededEvent,
    DoneEvent,
    TextDeltaEvent,
    TodoStateEvent,
    ToolCallEvent,
    ToolResultEvent,
)
from app.agent.loop import run_agent_loop
from app.agent.runtime import (
    AgentExecutionResult,
    AgentRuntime,
    ApprovalCallback,
    EventCallback,
    ModelCaller,
    RuntimeBudget,
    run,
    run_stream,
)

__all__ = [
    "AgentEvent",
    "AgentExecutionResult",
    "AgentRuntime",
    "ApprovalCallback",
    "ApprovalNeededEvent",
    "DoneEvent",
    "EventCallback",
    "ModelCaller",
    "RuntimeBudget",
    "TextDeltaEvent",
    "TodoStateEvent",
    "ToolCallEvent",
    "ToolResultEvent",
    "run",
    "run_agent_loop",
    "run_stream",
]
