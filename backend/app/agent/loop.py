"""Agent execution loop interface and re-exports."""

from __future__ import annotations

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
    "AgentExecutionResult",
    "AgentRuntime",
    "ApprovalCallback",
    "EventCallback",
    "ModelCaller",
    "RuntimeBudget",
    "run",
    "run_agent_loop",
    "run_stream",
]

# Alias run_agent_loop to run for compatibility
run_agent_loop = run
