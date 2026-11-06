"""Pydantic state models for MAYA AI orchestrator and agents."""

from __future__ import annotations

import operator
from typing import Annotated, Any, Literal

from pydantic import BaseModel, Field

AgentRole = Literal["research", "coding", "document", "email", "pm", "browser", "file"]
SubTaskStatus = Literal["pending", "in_progress", "awaiting_approval", "completed", "failed"]
PlanStatus = Literal["planning", "executing", "paused", "completed", "failed"]
InteractionModality = Literal["voice", "text"]

APPROVAL_REQUIRED_ROLES: frozenset[str] = frozenset({"email"})


class SubTask(BaseModel):
    """Discrete executable unit in a DAG execution plan."""

    id: str
    assigned_agent: AgentRole
    description: str
    dependencies: list[str] = Field(default_factory=list)
    status: SubTaskStatus = "pending"
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_result: dict[str, Any] | None = None
    verification_proof: dict[str, Any] | None = None
    error_message: str | None = None
    retry_count: int = 0
    requires_approval: bool = False


def subtask_requires_approval(subtask: SubTask) -> bool:
    """Check if a subtask requires human approval before or during execution."""
    return (
        subtask.requires_approval is True
        or subtask.assigned_agent in APPROVAL_REQUIRED_ROLES
        or bool(subtask.input_data.get("requires_approval"))
    )


class AgentExecutionState(BaseModel):
    """Authoritative state for a multi-agent LangGraph workflow execution."""

    session_id: str
    user_id: str
    workspace_id: str
    user_instruction: str
    interaction_modality: InteractionModality
    active_plan: list[SubTask] = Field(default_factory=list)
    plan_status: PlanStatus = "planning"
    completed_task_ids: Annotated[list[str], operator.add] = Field(default_factory=list)
    pending_approval: dict[str, Any] | None = None
    shared_context: dict[str, Any] = Field(default_factory=dict)
    execution_trace: Annotated[list[dict[str, Any]], operator.add] = Field(default_factory=list)
