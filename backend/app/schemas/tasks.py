"""Pydantic schemas for task submission and lifecycle management."""

from __future__ import annotations

from datetime import datetime
from decimal import Decimal
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class TaskCreateRequest(BaseModel):
    """Payload for submitting a new background task."""

    instruction: str = Field(
        ..., min_length=1, description="Goal or instruction for the agent fleet."
    )
    title: str | None = Field(
        default=None, max_length=255, description="Short human-readable title."
    )
    idempotency_key: str | None = Field(
        default=None,
        max_length=128,
        description="Client-provided idempotency key. If omitted, Idempotency-Key header is used.",
    )
    budget_usd: Decimal | None = Field(
        default=Decimal("1.50"),
        ge=0,
        description="Maximum model gateway spend allowed for this task.",
    )
    session_id: UUID | None = Field(
        default=None,
        description="Optional conversation session ID this task is associated with.",
    )
    input_files: list[str] = Field(
        default_factory=list,
        max_length=10,
        description="Upload ids (POST /api/v1/files/uploads) the task may read and edit.",
    )


class TaskInputAnswer(BaseModel):
    """One answer of a paused step's question card: chosen option labels and/or pasted text."""

    selected: list[str] = Field(default_factory=list, max_length=10)
    text: str = Field(default="", max_length=2000)


class TaskInputRequest(BaseModel):
    """Answer for a step that paused with a ``needs_input`` question (inline on the task card)."""

    subtask_id: UUID
    answers: list[TaskInputAnswer] = Field(min_length=1, max_length=4)


class TaskInputResponse(BaseModel):
    message: str


class SubtaskResponse(BaseModel):
    """Schema representing an individual subtask."""

    id: UUID
    task_id: UUID
    assigned_agent: str
    description: str
    dependencies: list[str] = Field(default_factory=list)
    status: str
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_result: dict[str, Any] | None = None
    verification_proof: dict[str, Any] | None = None
    error_message: str | None = None
    retry_count: int = 0
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None

    model_config = {"from_attributes": True}


class TaskDetailResponse(BaseModel):
    """Detailed task status and projection information."""

    id: UUID
    workspace_id: UUID
    user_id: UUID
    session_id: str
    title: str | None = None
    instruction: str
    idempotency_key: str | None = None
    status: str
    plan_status: str
    budget_usd: Decimal
    spent_usd: Decimal
    tool_turns: int = 0
    created_at: datetime
    updated_at: datetime
    completed_at: datetime | None = None
    active_plan: list[dict[str, Any]] = Field(default_factory=list)
    subtasks: list[SubtaskResponse] = Field(default_factory=list)
    # Self-healing report (no migration: read from ``tasks.shared_context``). ``warnings`` lists
    # steps that failed but were not critical; ``error`` is the first real cause of a failure.
    warnings: list[str] = Field(default_factory=list)
    recovered: list[dict[str, Any]] = Field(default_factory=list)
    error: str | None = None
    # "Completed with errors": the status is still ``completed``; ``partial`` is true when a step
    # failed, and ``failed_steps`` lists them (``step``, ``error``, ``deliverable_incomplete``).
    partial: bool = False
    failed_steps: list[dict[str, Any]] = Field(default_factory=list)

    model_config = {"from_attributes": True}


class TaskListItem(BaseModel):
    """Summary item in task listing."""

    id: UUID
    workspace_id: UUID
    title: str | None = None
    instruction: str
    status: str
    plan_status: str
    budget_usd: Decimal
    spent_usd: Decimal
    created_at: datetime
    completed_at: datetime | None = None
    subtask_count: int = 0
    partial: bool = False

    model_config = {"from_attributes": True}


class TaskListResponse(BaseModel):
    """Paginated task listing response."""

    items: list[TaskListItem]
    total: int
    limit: int
    offset: int


class TaskSubmissionResponse(BaseModel):
    """Response returned upon task submission."""

    task_id: UUID
    status: str
    idempotency_key: str
    created_at: datetime
    is_new: bool


class TaskAbortResponse(BaseModel):
    """Response returned when a task is aborted."""

    task_id: UUID
    status: str
    compensations_triggered: int
    executed: int
    failed: int
    notifications: list[str] = Field(default_factory=list)


class TaskBulkDeleteRequest(BaseModel):
    """Delete several finished tasks at once (at most 100 per request)."""

    ids: list[UUID] = Field(..., min_length=1, max_length=100)


class TaskBulkDeleteResponse(BaseModel):
    """What happened to each id. ``not_found`` also covers tasks already deleted."""

    deleted: list[UUID] = Field(default_factory=list)
    not_found: list[UUID] = Field(default_factory=list)
    not_finished: list[UUID] = Field(default_factory=list)
    files_removed: int = 0
