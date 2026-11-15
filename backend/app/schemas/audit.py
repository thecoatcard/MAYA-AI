"""Schemas for tamper-evident audit log endpoints (Phase 07/12, Decisions D4, D5)."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict


class AuditEventResponse(BaseModel):
    """Normalized audit trail event payload for frontend inspection."""

    model_config = ConfigDict(from_attributes=True)

    id: str
    timestamp: datetime
    event_type: str
    category: str
    actor: str
    details: str
    status: str
    task_id: str | None = None
    subtask_id: str | None = None
    approval_id: str | None = None
    server_name: str | None = None
    tool_name: str | None = None
    permission_level: str | None = None
    prev_hash: str
    row_hash: str
    duration_ms: int | None = None
    input_payload: dict[str, Any] | None = None
    output_payload: dict[str, Any] | None = None


class AuditListResponse(BaseModel):
    """Paginated list of immutable audit log events with tamper verification."""

    items: list[AuditEventResponse]
    total: int
    limit: int
    offset: int
    chain_valid: bool = True
    chain_first_bad_id: str | None = None
    chain_length: int = 0
    statuses: list[str] = []
