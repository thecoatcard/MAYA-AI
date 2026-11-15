"""Approval API endpoints (Decision D4, Decision D5)."""

from __future__ import annotations

import hashlib
import hmac
from datetime import UTC, datetime
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, status
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.auth.dependencies import TenantDatabase, get_principal, get_tenant_database
from app.auth.models import Principal
from app.core.config import Settings
from app.core.totp import TOTPLockedOutError, TOTPReplayError, verify_totp_hardened
from app.db.repositories.approvals import ApprovalRecord, ApprovalRepository
from app.mcp.tokens import mint_approval_token
from app.schemas.approvals import (
    ApprovalDecisionRequest,
    ApprovalDecisionResponse,
    ApprovalRequestOut,
)
from app.services.approval_effects import fail_subtask_after_rejection
from app.services.audit_service import AuditWriteError, record_audit
from app.tasks.queue import SubtaskJob, SubtaskQueue

router = APIRouter(prefix="/api/v1/approvals", tags=["approvals"])


def _require_settings(request: Request) -> Settings:
    """Return the application settings or fail closed; never fall back to a fixed key."""
    configured: Settings | None = getattr(request.app.state, "settings", None)
    if configured is None:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "server_misconfigured", "message": "Signing keys are not configured."},
        )
    return configured


async def _audit_decision(
    conn: AsyncConnection,
    principal: Principal,
    record: ApprovalRecord,
    *,
    action: str,
    status_value: str,
) -> None:
    """Record the decision in the audit chain; fail closed (503, decision rolled back)."""
    try:
        async with AsyncSession(bind=conn, expire_on_commit=False) as audit_session:
            await record_audit(
                audit_session,
                workspace_id=principal.workspace_id,
                user_ref=str(principal.user_id),
                status=status_value,
                action=action,
                task_id=record.task_id,
                subtask_id=record.subtask_id,
                approval_id=record.id,
                input={"tool": record.tool_name, "tier": record.tier},
            )
    except AuditWriteError as exc:
        raise HTTPException(
            status_code=status.HTTP_503_SERVICE_UNAVAILABLE,
            detail={"error": "audit_unavailable"},
        ) from exc


def _derive_user_totp_secret(otp_hmac_key: str, user_id: UUID) -> bytes:
    """Derive a deterministic, isolated TOTP secret for a user from the server HMAC key."""
    return hmac.new(otp_hmac_key.encode("utf-8"), user_id.bytes, hashlib.sha256).digest()


def _to_schema(record: ApprovalRecord) -> ApprovalRequestOut:
    return ApprovalRequestOut(
        id=record.id,
        workspace_id=record.workspace_id,
        user_id=record.user_id,
        task_id=record.task_id,
        subtask_id=record.subtask_id,
        tool_name=record.tool_name,
        tool_args=record.tool_args,
        payload_hash=record.payload_hash,
        tier=record.tier,
        action_description=record.action_description,
        payload_diff=record.payload_diff,
        status=record.status,
        approval_token=record.approval_token,
        requested_at=record.requested_at,
        expires_at=record.expires_at,
        decided_at=record.decided_at,
        consumed_at=record.consumed_at,
        decision_channel=record.decision_channel,
        auto_approved=record.decision_channel == "api",
        claimed_by=record.claimed_by,
        claimed_at=record.claimed_at,
        started_at=record.started_at,
        completed_at=record.completed_at,
        failed_at=record.failed_at,
        failure_reason=record.failure_reason,
        idempotency_key=record.idempotency_key,
        execution_output=record.execution_output,
    )


@router.get("", response_model=list[ApprovalRequestOut])
async def list_approvals(
    principal: Annotated[Principal, Depends(get_principal)],
    database: Annotated[TenantDatabase, Depends(get_tenant_database)],
    status_filter: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
) -> list[ApprovalRequestOut]:
    """List approval requests for the authenticated user and workspace."""
    async with database.transaction() as conn:
        repo = ApprovalRepository(conn, principal.workspace_id, principal.user_id)
        records = await repo.list_requests(status=status_filter, limit=limit)
    return [_to_schema(r) for r in records]


@router.get("/{request_id}", response_model=ApprovalRequestOut)
async def get_approval(
    request_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    database: Annotated[TenantDatabase, Depends(get_tenant_database)],
) -> ApprovalRequestOut:
    """Retrieve details for a single approval request."""
    async with database.transaction() as conn:
        repo = ApprovalRepository(conn, principal.workspace_id, principal.user_id)
        record = await repo.get_request(request_id)
    if record is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail={"error": "approval_request_not_found"},
        )
    return _to_schema(record)


@router.post("/{request_id}/decide", response_model=ApprovalDecisionResponse)
async def decide_approval(
    request_id: UUID,
    decision: ApprovalDecisionRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    database: Annotated[TenantDatabase, Depends(get_tenant_database)],
    request: Request,
    x_maya_channel: Annotated[str | None, Header()] = None,
) -> ApprovalDecisionResponse:
    """Approve or deny an approval request. Voice channel is explicitly barred from approving."""
    # 1. Enforce Channel Isolation: Voice channel cannot grant approval
    if decision.action == "approve" and x_maya_channel and x_maya_channel.lower() == "voice":
        try:
            async with database.transaction() as audit_conn:
                async with AsyncSession(bind=audit_conn, expire_on_commit=False) as audit_session:
                    await record_audit(
                        audit_session,
                        workspace_id=principal.workspace_id,
                        user_ref=str(principal.user_id),
                        status="denied",
                        action="approval.denied",
                        approval_id=request_id,
                        input={"channel": "voice"},
                    )
        except AuditWriteError:
            pass  # the request is denied either way
        raise HTTPException(
            status_code=status.HTTP_403_FORBIDDEN,
            detail={
                "error": "voice_channel_cannot_approve",
                "message": "Voice audio channel is barred from granting approvals. Use the Web UI.",
            },
        )

    now = datetime.now(UTC)
    resume_job: SubtaskJob | None = None
    response_body: ApprovalDecisionResponse

    async with database.transaction() as conn:
        repo = ApprovalRepository(conn, principal.workspace_id, principal.user_id)
        record = await repo.get_request(request_id)

        if record is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail={"error": "approval_request_not_found"},
            )

        if record.status != "pending":
            raise HTTPException(
                status_code=status.HTTP_409_CONFLICT,
                detail={"error": "approval_already_decided", "status": record.status},
            )

        if record.expires_at <= now:
            raise HTTPException(
                status_code=status.HTTP_410_GONE,
                detail={"error": "approval_request_expired"},
            )

        # 2. Rejection / Revocation branches
        if decision.action == "reject":
            await repo.reject(
                request_id,
                channel="web",
                reason=decision.rejection_reason,
                now=now,
            )
            await _audit_decision(
                conn, principal, record, action="approval.rejected", status_value="rejected"
            )
            if record.subtask_id is not None:
                await fail_subtask_after_rejection(
                    conn,
                    workspace_id=principal.workspace_id,
                    subtask_id=record.subtask_id,
                    reason=decision.rejection_reason,
                )
            return ApprovalDecisionResponse(id=request_id, status="denied")

        if decision.action == "revoke":
            await repo.revoke(
                request_id,
                reason=decision.rejection_reason or "Revoked by user",
            )
            await _audit_decision(
                conn, principal, record, action="approval.revoked", status_value="aborted"
            )
            return ApprovalDecisionResponse(id=request_id, status="revoked")

        # 3. Approval branch
        conn_settings = _require_settings(request)

        # 3a. Step-Up Authentication for RESTRICTED tier
        if record.tier == "RESTRICTED":
            if not decision.totp_code:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "error": "totp_required",
                        "message": (
                            "Step-up authentication (6-digit TOTP code) required "
                            "for RESTRICTED actions."
                        ),
                    },
                )

            otp_key = conn_settings.otp_hmac_key.get_secret_value()
            user_totp_secret = _derive_user_totp_secret(otp_key, principal.user_id)
            try:
                valid = verify_totp_hardened(
                    decision.totp_code,
                    user_totp_secret,
                    user_id=principal.user_id,
                )
                if not valid:
                    raise HTTPException(
                        status_code=status.HTTP_401_UNAUTHORIZED,
                        detail={
                            "error": "invalid_totp",
                            "message": "Invalid or expired step-up authentication code.",
                        },
                    )
            except TOTPLockedOutError as exc:
                raise HTTPException(
                    status_code=status.HTTP_429_TOO_MANY_REQUESTS,
                    detail={
                        "error": "totp_locked_out",
                        "message": str(exc),
                    },
                ) from exc
            except TOTPReplayError as exc:
                raise HTTPException(
                    status_code=status.HTTP_401_UNAUTHORIZED,
                    detail={
                        "error": "totp_replayed",
                        "message": str(exc),
                    },
                ) from exc

        # 3b. Mint cryptographically signed single-use JWT approval token
        jwt_key = conn_settings.jwt_signing_key.get_secret_value()

        approval_token, token_jti = mint_approval_token(
            jwt_key,
            user_id=principal.user_id,
            workspace_id=principal.workspace_id,
            request_id=request_id,
            tool_name=record.tool_name,
            payload_hash=record.payload_hash,
            ttl_seconds=300,
            now=now,
        )

        expires_at = await repo.approve(
            request_id,
            approval_token=approval_token,
            token_jti=token_jti,
            channel="web",
            now=now,
        )

        await _audit_decision(
            conn, principal, record, action="approval.approved", status_value="success"
        )

        # 3c. If associated with a subtask, update input_data with approval_token and resume
        if record.subtask_id is not None:
            update_subtask = text(
                "UPDATE subtasks "
                "SET status = 'pending', "
                "    input_data = jsonb_set("
                "        COALESCE(input_data, '{}'::jsonb), "
                "        '{approval_token}', "
                "        to_jsonb(CAST(:token_text AS text))"
                "    ) "
                "WHERE workspace_id = :workspace_id AND id = :subtask_id "
                "RETURNING assigned_agent, task_id"
            )
            sub_res = await conn.execute(
                update_subtask,
                {
                    "workspace_id": principal.workspace_id,
                    "subtask_id": record.subtask_id,
                    "token_text": approval_token,
                },
            )
            sub_row = sub_res.first()
            if sub_row is not None:
                resume_job = SubtaskJob(
                    workspace_id=principal.workspace_id,
                    user_id=principal.user_id,
                    task_id=sub_row.task_id,
                    subtask_id=record.subtask_id,
                    assigned_agent=str(sub_row.assigned_agent),
                )

        response_body = ApprovalDecisionResponse(
            id=request_id,
            status="approved",
            approval_token=approval_token,
            expires_at=expires_at,
        )

    # Enqueue only after the approval and the subtask reset have committed.
    if resume_job is not None:
        resources = getattr(request.app.state, "resources", None)
        queue = getattr(resources, "subtask_queue", None)
        if isinstance(queue, SubtaskQueue):
            await queue.enqueue(resume_job)
    return response_body
