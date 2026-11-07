"""Tamper-evident Audit Trail REST API (Phase 07/12, Decisions D4, D5)."""

from __future__ import annotations

from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import and_, case, desc, func, or_, select

from app.auth.dependencies import TenantDatabase, get_principal, get_tenant_database
from app.auth.models import Principal
from app.db.models import AuditLog
from app.db.tenant import tenant_session
from app.schemas.audit import AuditEventResponse, AuditListResponse
from app.services.audit_service import (
    AUDIT_STATUSES,
    CATEGORIES,
    INTERNAL_SERVER,
    verify_chain,
)

router = APIRouter(prefix="/api/v1/audit", tags=["audit"])


_INTERNAL_PREFIXES = ("auth", "task", "approval", "security")

# Single source of truth for the category, evaluated in SQL so filtering happens before paging.
_CATEGORY_SQL = case(
    (
        and_(
            AuditLog.server_name == INTERNAL_SERVER,
            func.split_part(AuditLog.tool_name, ".", 1).in_(_INTERNAL_PREFIXES),
        ),
        func.split_part(AuditLog.tool_name, ".", 1),
    ),
    (AuditLog.approval_id.is_not(None), "approval"),
    (or_(AuditLog.tool_name.is_not(None), AuditLog.server_name.is_not(None)), "tool"),
    (AuditLog.task_id.is_not(None), "task"),
    else_="security",
)


def _map_record_to_schema(rec: AuditLog, category: str) -> AuditEventResponse:
    """Normalize database AuditLog record to frontend AuditEventResponse."""
    prev_h = rec.prev_hash.hex() if rec.prev_hash else "0" * 64
    row_h = rec.row_hash.hex()
    status = rec.status.lower()

    if rec.server_name == INTERNAL_SERVER and rec.tool_name:
        event_type = rec.tool_name
        details = f"{rec.tool_name} by '{rec.user_ref}' [{status.upper()}]."
    elif category == "approval":
        event_type = f"approval.{status}"
        details = (
            f"Approval request '{rec.approval_id}' for tool '{rec.tool_name or 'action'}' "
            f"[{status.upper()}]."
        )
    elif category == "tool":
        event_type = "tool.invoked"
        details = (
            f"Invoked tool '{rec.tool_name or 'unspecified'}' on server "
            f"'{rec.server_name or 'builtin'}' ({status})."
        )
    elif category == "task":
        event_type = f"task.{status}"
        details = f"Autonomous task '{rec.task_id}' state transitioned to {status}."
    else:
        event_type = f"{category}.audit_event"
        details = f"{category.capitalize()} audit event for '{rec.user_ref}' [{status.upper()}]."

    return AuditEventResponse(
        id=f"audit-{rec.id}",
        timestamp=rec.created_at,
        event_type=event_type,
        category=category,
        actor=rec.user_ref,
        details=details,
        status=status,
        task_id=str(rec.task_id) if rec.task_id else None,
        subtask_id=str(rec.subtask_id) if rec.subtask_id else None,
        approval_id=str(rec.approval_id) if rec.approval_id else None,
        server_name=rec.server_name,
        tool_name=rec.tool_name,
        permission_level=rec.permission_level,
        prev_hash=prev_h,
        row_hash=row_h,
        duration_ms=rec.duration_ms,
        input_payload=rec.input_redacted,
        output_payload=rec.output_redacted,
    )


@router.get("", response_model=AuditListResponse)
async def list_audit_logs(
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
    category: Annotated[str | None, Query(description="Filter by audit category")] = None,
    task_id: Annotated[UUID | None, Query(description="Filter by task ID")] = None,
    status: Annotated[str | None, Query(description="Filter by status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 50,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> AuditListResponse:
    """Retrieve hash-chained audit log records under strict tenant workspace RLS isolation."""
    cat = category.lower() if category else None
    if cat == "all":
        cat = None
    if cat is not None and cat not in CATEGORIES:
        raise HTTPException(status_code=422, detail="unknown category")
    st = status.lower() if status else None
    if st is not None and st not in AUDIT_STATUSES:
        raise HTTPException(status_code=422, detail="unknown status")

    filters = [AuditLog.workspace_id == principal.workspace_id]
    if task_id is not None:
        filters.append(AuditLog.task_id == task_id)
    if st is not None:
        filters.append(AuditLog.status == st)
    if cat is not None:
        filters.append(_CATEGORY_SQL == cat)

    async with tenant_session(db.engine, principal.workspace_id, principal.user_id) as session:
        total = (
            await session.execute(select(func.count()).select_from(AuditLog).where(*filters))
        ).scalar_one()
        result = await session.execute(
            select(AuditLog, _CATEGORY_SQL.label("category"))
            .where(*filters)
            .order_by(desc(AuditLog.created_at), desc(AuditLog.id))
            .limit(limit)
            .offset(offset)
        )
        items = [_map_record_to_schema(rec, str(cat_value)) for rec, cat_value in result.all()]
        verification = await verify_chain(session, principal.workspace_id)

    return AuditListResponse(
        items=items,
        total=int(total),
        limit=limit,
        offset=offset,
        chain_valid=verification.valid,
        chain_first_bad_id=(
            f"audit-{verification.first_bad_row_id}" if verification.first_bad_row_id else None
        ),
        chain_length=verification.count,
        statuses=sorted(AUDIT_STATUSES),
    )
