"""Schemas for approval requests and responses."""

from datetime import datetime
from enum import StrEnum
from typing import Any, Literal
from uuid import UUID

from pydantic import BaseModel, Field


class ApprovalStatus(StrEnum):
    """Explicit lifecycle states for tool approval requests."""

    PENDING = "pending"
    APPROVED = "approved"
    REJECTED = "rejected"
    EXPIRED = "expired"
    REVOKED = "revoked"
    CLAIMED = "claimed"
    EXECUTING = "executing"
    COMPLETED = "completed"
    FAILED = "failed"
    # Backwards compatibility aliases
    DENIED = "denied"
    CONSUMED = "consumed"


class ApprovalRequestOut(BaseModel):
    id: UUID
    workspace_id: UUID
    user_id: UUID
    task_id: UUID
    subtask_id: UUID | None = None
    tool_name: str
    tool_args: dict[str, Any]
    payload_hash: str
    tier: Literal["APPROVAL_REQUIRED", "RESTRICTED"]
    action_description: str
    payload_diff: dict[str, Any] | list[Any] | str | None = None
    status: ApprovalStatus | str
    approval_token: str | None = None
    requested_at: datetime
    expires_at: datetime
    decided_at: datetime | None = None
    consumed_at: datetime | None = None
    decision_channel: Literal["web", "api"] | None = None
    # True when the owner's Notion auto-approve setting (not a click) recorded the approval. The
    # decide route only ever writes "web", so the stored "api" channel is that marker.
    auto_approved: bool = False

    # Execution lifecycle tracking fields
    claimed_by: str | None = None
    claimed_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    failure_reason: str | None = None
    idempotency_key: str | None = None
    execution_output: dict[str, Any] | list[Any] | None = None


class ApprovalDecisionRequest(BaseModel):
    action: Literal["approve", "reject", "revoke"]
    totp_code: str | None = Field(default=None, min_length=6, max_length=8)
    rejection_reason: str | None = Field(default=None, max_length=500)


class ApprovalDecisionResponse(BaseModel):
    id: UUID
    status: Literal["approved", "rejected", "revoked", "denied"]
    approval_token: str | None = None
    expires_at: datetime | None = None
