"""PostgreSQL repository for approval requests with full execution lifecycle tracking."""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

SET_WORKSPACE_USER_CONTEXT = text(
    "SELECT set_config('app.workspace_id', :workspace_id, true), "
    "       set_config('app.user_id', :user_id, true)"
)

INSERT_APPROVAL_REQUEST = text(
    "INSERT INTO approval_requests ("
    "  workspace_id, user_id, task_id, subtask_id, tool_name, tool_args, payload_hash, "
    "  permission_level, action_description, payload_diff, status, expires_at, created_at, "
    "  idempotency_key"
    ") VALUES ("
    "  :workspace_id, :user_id, :task_id, :subtask_id, :tool_name, CAST(:tool_args AS jsonb), "
    "  :payload_hash, :tier, :action_description, CAST(:payload_diff AS jsonb), 'pending', "
    "  :expires_at, :requested_at, :idempotency_key"
    ") RETURNING id, created_at AS requested_at"
)

GET_APPROVAL_REQUEST = text(
    "SELECT id, workspace_id, user_id, task_id, subtask_id, tool_name, tool_args, "
    "       payload_hash, permission_level AS tier, action_description, payload_diff, status, "
    "       approval_token, token_jti, created_at AS requested_at, expires_at, decided_at, "
    "       consumed_at, decision_channel, claimed_by, claimed_at, started_at, completed_at, "
    "       failed_at, failure_reason, idempotency_key, execution_output "
    "FROM approval_requests "
    "WHERE workspace_id = :workspace_id AND id = :id"
)

LIST_APPROVAL_REQUESTS = text(
    "SELECT id, workspace_id, user_id, task_id, subtask_id, tool_name, tool_args, "
    "       payload_hash, permission_level AS tier, action_description, payload_diff, status, "
    "       approval_token, token_jti, created_at AS requested_at, expires_at, decided_at, "
    "       consumed_at, decision_channel, claimed_by, claimed_at, started_at, completed_at, "
    "       failed_at, failure_reason, idempotency_key, execution_output "
    "FROM approval_requests "
    "WHERE workspace_id = :workspace_id AND user_id = :user_id "
    "  AND (CAST(:status AS text) IS NULL OR status = CAST(:status AS text)) "
    "ORDER BY created_at DESC LIMIT :limit"
)

APPROVE_REQUEST = text(
    "UPDATE approval_requests "
    "SET status = 'approved', decided_at = :now, approval_token = :token, "
    "    token_jti = :jti, decision_channel = :channel "
    "WHERE workspace_id = :workspace_id AND user_id = :user_id AND id = :id "
    "  AND status = 'pending' AND expires_at > :now "
    "RETURNING id, expires_at"
)

REJECT_REQUEST = text(
    "UPDATE approval_requests "
    "SET status = 'rejected', decided_at = :now, decision_channel = :channel, "
    "    failure_reason = :reason "
    "WHERE workspace_id = :workspace_id AND user_id = :user_id AND id = :id "
    "  AND status = 'pending' "
    "RETURNING id"
)

REVOKE_REQUEST = text(
    "UPDATE approval_requests "
    "SET status = 'revoked', failure_reason = :reason "
    "WHERE workspace_id = :workspace_id AND user_id = :user_id AND id = :id "
    "  AND status IN ('pending', 'approved') "
    "RETURNING id"
)


@dataclass(frozen=True, slots=True)
class ApprovalRecord:
    id: UUID
    workspace_id: UUID
    user_id: UUID
    task_id: UUID
    subtask_id: UUID | None
    tool_name: str
    tool_args: dict[str, Any]
    payload_hash: str
    tier: Literal["APPROVAL_REQUIRED", "RESTRICTED"]
    action_description: str
    payload_diff: dict[str, Any] | list[Any] | None
    status: str
    approval_token: str | None
    token_jti: UUID | None
    requested_at: datetime
    expires_at: datetime
    decided_at: datetime | None
    consumed_at: datetime | None
    decision_channel: Literal["web", "api"] | None
    claimed_by: str | None = None
    claimed_at: datetime | None = None
    started_at: datetime | None = None
    completed_at: datetime | None = None
    failed_at: datetime | None = None
    failure_reason: str | None = None
    idempotency_key: str | None = None
    execution_output: dict[str, Any] | list[Any] | None = None


def _row_to_record(row: Any) -> ApprovalRecord:
    args = row.tool_args
    if isinstance(args, str):
        args = json.loads(args)
    diff = row.payload_diff
    if isinstance(diff, str):
        diff = json.loads(diff)
    output = getattr(row, "execution_output", None)
    if isinstance(output, str):
        output = json.loads(output)

    return ApprovalRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        user_id=row.user_id,
        task_id=row.task_id,
        subtask_id=row.subtask_id,
        tool_name=row.tool_name,
        tool_args=args or {},
        payload_hash=row.payload_hash,
        tier=row.tier,
        action_description=row.action_description,
        payload_diff=diff,
        status=row.status,
        approval_token=row.approval_token,
        token_jti=row.token_jti,
        requested_at=row.requested_at,
        expires_at=row.expires_at,
        decided_at=row.decided_at,
        consumed_at=row.consumed_at,
        decision_channel=row.decision_channel,
        claimed_by=getattr(row, "claimed_by", None),
        claimed_at=getattr(row, "claimed_at", None),
        started_at=getattr(row, "started_at", None),
        completed_at=getattr(row, "completed_at", None),
        failed_at=getattr(row, "failed_at", None),
        failure_reason=getattr(row, "failure_reason", None),
        idempotency_key=getattr(row, "idempotency_key", None),
        execution_output=output,
    )


class ApprovalRepository:
    def __init__(self, connection: AsyncConnection, workspace_id: UUID, user_id: UUID) -> None:
        self._conn = connection
        self._workspace_id = workspace_id
        self._user_id = user_id

    async def create_request(
        self,
        *,
        task_id: UUID,
        subtask_id: UUID | None,
        tool_name: str,
        tool_args: dict[str, Any],
        payload_hash: str,
        tier: str,
        action_description: str,
        payload_diff: dict[str, Any] | list[Any] | None,
        expires_at: datetime,
        idempotency_key: str | None = None,
        now: datetime | None = None,
    ) -> ApprovalRecord:
        requested_at = now if now is not None else datetime.now(UTC)
        params = {
            "workspace_id": self._workspace_id,
            "user_id": self._user_id,
            "task_id": task_id,
            "subtask_id": subtask_id,
            "tool_name": tool_name,
            "tool_args": json.dumps(tool_args),
            "payload_hash": payload_hash,
            "tier": tier,
            "action_description": action_description,
            "payload_diff": json.dumps(payload_diff) if payload_diff is not None else "{}",
            "expires_at": expires_at,
            "requested_at": requested_at,
            "idempotency_key": idempotency_key,
        }
        res = await self._conn.execute(INSERT_APPROVAL_REQUEST, params)
        row = res.one()

        return ApprovalRecord(
            id=row.id,
            workspace_id=self._workspace_id,
            user_id=self._user_id,
            task_id=task_id,
            subtask_id=subtask_id,
            tool_name=tool_name,
            tool_args=tool_args,
            payload_hash=payload_hash,
            tier=tier,  # type: ignore[arg-type]
            action_description=action_description,
            payload_diff=payload_diff,
            status="pending",
            approval_token=None,
            token_jti=None,
            requested_at=row.requested_at,
            expires_at=expires_at,
            decided_at=None,
            consumed_at=None,
            decision_channel=None,
            idempotency_key=idempotency_key,
        )

    async def get_request(self, request_id: UUID) -> ApprovalRecord | None:
        res = await self._conn.execute(
            GET_APPROVAL_REQUEST,
            {"workspace_id": self._workspace_id, "id": request_id},
        )
        row = res.first()
        return _row_to_record(row) if row is not None else None

    async def list_requests(
        self,
        status: str | None = None,
        limit: int = 20,
    ) -> list[ApprovalRecord]:
        res = await self._conn.execute(
            LIST_APPROVAL_REQUESTS,
            {
                "workspace_id": self._workspace_id,
                "user_id": self._user_id,
                "status": status,
                "limit": limit,
            },
        )
        return [_row_to_record(r) for r in res.all()]

    async def approve(
        self,
        request_id: UUID,
        *,
        approval_token: str,
        token_jti: UUID,
        channel: str = "web",
        now: datetime | None = None,
    ) -> datetime | None:
        """Mark request as approved and attach token.

        Returns expires_at if approved, None if already decided or expired.
        """
        current_time = now if now is not None else datetime.now(UTC)
        res = await self._conn.execute(
            APPROVE_REQUEST,
            {
                "workspace_id": self._workspace_id,
                "user_id": self._user_id,
                "id": request_id,
                "token": approval_token,
                "jti": token_jti,
                "channel": channel,
                "now": current_time,
            },
        )
        row = res.first()
        return row.expires_at if row is not None else None

    async def reject(
        self,
        request_id: UUID,
        *,
        channel: str = "web",
        reason: str | None = None,
        now: datetime | None = None,
    ) -> bool:
        current_time = now if now is not None else datetime.now(UTC)
        res = await self._conn.execute(
            REJECT_REQUEST,
            {
                "workspace_id": self._workspace_id,
                "user_id": self._user_id,
                "id": request_id,
                "channel": channel,
                "reason": reason,
                "now": current_time,
            },
        )
        return res.first() is not None

    async def revoke(
        self,
        request_id: UUID,
        *,
        reason: str = "Revoked by user",
    ) -> bool:
        res = await self._conn.execute(
            REVOKE_REQUEST,
            {
                "workspace_id": self._workspace_id,
                "user_id": self._user_id,
                "id": request_id,
                "reason": reason,
            },
        )
        return res.first() is not None
