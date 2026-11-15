"""API routes for Task Lifecycle Management (Phase 06)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Annotated
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from starlette.responses import StreamingResponse

from app.auth.dependencies import TenantDatabase, get_principal, get_tenant_database
from app.auth.models import Principal
from app.db.models import (
    Task,
)
from app.db.tenant import tenant_session
from app.gateway.model_gateway import ModelGateway
from app.schemas.tasks import (
    SubtaskResponse,
    TaskAbortResponse,
    TaskBulkDeleteRequest,
    TaskBulkDeleteResponse,
    TaskCreateRequest,
    TaskDetailResponse,
    TaskInputRequest,
    TaskInputResponse,
    TaskListItem,
    TaskListResponse,
    TaskSubmissionResponse,
)
from app.services.artifact_service import ArtifactService
from app.services.audit_service import AuditWriteError, record_audit
from app.services.task_deletion import delete_tasks, not_deleted
from app.services.task_submission import TaskRefusal, TaskSubmissionService
from app.tasks.events import (
    TaskEventPublisher,
    TaskEventType,
    get_event_channel,
)
from app.tasks.outbox import write_outbox_event
from app.tasks.queue import SubtaskQueue
from app.tasks.saga import SagaManager

logger = logging.getLogger(__name__)

router = APIRouter(prefix="/api/v1/tasks", tags=["tasks"])


# -------------------------------------------------------------------------
# Dependency points
# -------------------------------------------------------------------------


async def get_event_publisher(request: Request) -> TaskEventPublisher:
    """Return TaskEventPublisher bound to application Redis instance."""
    resources = getattr(request.app.state, "resources", None)
    redis_client = getattr(resources, "redis", None)
    return TaskEventPublisher(redis_client=redis_client)


async def get_model_gateway(request: Request) -> ModelGateway | None:
    resources = getattr(request.app.state, "resources", None)
    return getattr(resources, "model_gateway", None)


async def get_subtask_queue(request: Request) -> SubtaskQueue | None:
    resources = getattr(request.app.state, "resources", None)
    if resources is None:
        return None
    queue = getattr(resources, "subtask_queue", None)
    if isinstance(queue, SubtaskQueue):
        return queue
    return None


# -------------------------------------------------------------------------
# Endpoints
# -------------------------------------------------------------------------


@router.post(
    "",
    response_model=TaskSubmissionResponse,
    status_code=status.HTTP_201_CREATED,
    summary="Submit a new background task",
)
async def submit_task(
    payload: TaskCreateRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
    publisher: Annotated[TaskEventPublisher, Depends(get_event_publisher)],
    model_gateway: Annotated[ModelGateway | None, Depends(get_model_gateway)],
    response: Response,
    idempotency_key_header: Annotated[str | None, Header(alias="Idempotency-Key")] = None,
    queue: Annotated[SubtaskQueue | None, Depends(get_subtask_queue)] = None,
) -> TaskSubmissionResponse:
    """Submit a task with per-tenant idempotency enforcement.

    If an identical idempotency key is already recorded for this workspace,
    the existing task is returned immediately with HTTP 200 without duplicate execution.
    """
    idempotency_key = payload.idempotency_key or idempotency_key_header
    if not idempotency_key:
        raise HTTPException(
            status_code=status.HTTP_400_BAD_REQUEST,
            detail=(
                "An idempotency key must be provided in the request body or Idempotency-Key header."
            ),
        )

    submission = await TaskSubmissionService(
        engine=db.engine, gateway=model_gateway, queue=queue
    ).submit(
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        idempotency_key=idempotency_key,
        instruction=payload.instruction,
        title=payload.title,
        session_id=payload.session_id,
        budget_usd=payload.budget_usd,
        source="api",
        input_files=payload.input_files,
    )
    if isinstance(submission, TaskRefusal):
        # Nothing was created: say plainly what cannot be done and what to connect.
        raise HTTPException(
            status_code=status.HTTP_422_UNPROCESSABLE_ENTITY,
            detail=submission.as_result(),
        )
    if not submission.is_new:
        response.status_code = status.HTTP_200_OK
    return TaskSubmissionResponse(
        task_id=submission.task_id,
        status=submission.status,
        idempotency_key=idempotency_key,
        created_at=submission.created_at,
        is_new=submission.is_new,
    )


@router.get(
    "",
    response_model=TaskListResponse,
    summary="List tasks for current workspace",
)
async def list_tasks(
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
    task_status: Annotated[str | None, Query(alias="status")] = None,
    limit: Annotated[int, Query(ge=1, le=100)] = 20,
    offset: Annotated[int, Query(ge=0)] = 0,
) -> TaskListResponse:
    """Retrieve paginated list of tasks scoped to the current tenant."""
    from sqlalchemy import desc, func, select

    async with tenant_session(db.engine, principal.workspace_id, principal.user_id) as session:
        query = select(Task).where(Task.workspace_id == principal.workspace_id, not_deleted())
        if task_status is not None:
            query = query.where((Task.status == task_status) | (Task.plan_status == task_status))

        count_query = select(func.count()).select_from(query.subquery())
        total_res = await session.execute(count_query)
        total = total_res.scalar_one()

        items_query = query.order_by(desc(Task.created_at)).limit(limit).offset(offset)
        items_res = await session.execute(items_query)
        tasks = list(items_res.scalars().all())

        item_list = [
            TaskListItem(
                id=t.id,
                workspace_id=t.workspace_id,
                title=t.title,
                instruction=t.user_instruction or t.instruction or "",
                status=t.status or t.plan_status.value,
                plan_status=t.plan_status.value,
                budget_usd=t.budget_usd or Decimal("0.0"),
                spent_usd=t.spent_usd or Decimal("0.0"),
                created_at=t.created_at,
                completed_at=t.completed_at,
                subtask_count=len(t.active_plan) if t.active_plan else 0,
                partial=bool((t.shared_context or {}).get("partial")),
            )
            for t in tasks
        ]

        return TaskListResponse(
            items=item_list,
            total=total,
            limit=limit,
            offset=offset,
        )


@router.get(
    "/{task_id}",
    response_model=TaskDetailResponse,
    summary="Get full details for a task",
)
async def get_task(
    task_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
) -> TaskDetailResponse:
    """Fetch task details, subtasks, DAG nodes, and budget."""
    from sqlalchemy import select
    from sqlalchemy.orm import selectinload

    async with tenant_session(db.engine, principal.workspace_id, principal.user_id) as session:
        stmt = (
            select(Task)
            .where(Task.id == task_id, Task.workspace_id == principal.workspace_id, not_deleted())
            .options(selectinload(Task.subtasks))
        )
        res = await session.execute(stmt)
        task = res.scalars().first()
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task {task_id} not found in workspace",
            )

        subtask_responses = [
            SubtaskResponse(
                id=st.id,
                task_id=st.task_id,
                assigned_agent=st.assigned_agent.value,
                description=st.description,
                dependencies=st.dependencies,
                status=st.status.value,
                input_data=st.input_data,
                output_result=st.output_result,
                verification_proof=st.verification_proof,
                error_message=st.error_message,
                retry_count=st.retry_count,
                created_at=st.created_at,
                updated_at=st.updated_at,
                completed_at=st.completed_at,
            )
            for st in task.subtasks
        ]

        return TaskDetailResponse(
            id=task.id,
            workspace_id=task.workspace_id,
            user_id=task.user_id,
            session_id=str(task.session_id),
            title=task.title,
            instruction=task.user_instruction or task.instruction or "",
            idempotency_key=task.idempotency_key,
            status=task.status or task.plan_status.value,
            plan_status=task.plan_status.value,
            budget_usd=task.budget_usd or Decimal("0.0"),
            spent_usd=task.spent_usd or Decimal("0.0"),
            tool_turns=task.tool_turns or 0,
            created_at=task.created_at,
            updated_at=task.updated_at,
            completed_at=task.completed_at,
            active_plan=task.active_plan or [],
            subtasks=subtask_responses,
            warnings=[str(w) for w in (task.shared_context or {}).get("warnings") or []],
            recovered=list((task.shared_context or {}).get("healing") or []),
            error=((task.shared_context or {}).get("failure") or {}).get("message"),
            partial=bool((task.shared_context or {}).get("partial")),
            failed_steps=list((task.shared_context or {}).get("failed_steps") or []),
        )


@router.post(
    "/{task_id}/abort",
    response_model=TaskAbortResponse,
    summary="Abort an active task and run saga compensations",
)
async def abort_task(
    task_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
) -> TaskAbortResponse:
    """Abort an in-flight task, cancelling operations and executing reverse-order compensations."""
    from sqlalchemy import select

    async with tenant_session(db.engine, principal.workspace_id, principal.user_id) as session:
        stmt = (
            select(Task)
            .where(Task.id == task_id, Task.workspace_id == principal.workspace_id, not_deleted())
            .limit(1)
        )
        res = await session.execute(stmt)
        task = res.scalars().first()
        if task is None:
            raise HTTPException(
                status_code=status.HTTP_404_NOT_FOUND,
                detail=f"Task {task_id} not found in workspace",
            )

        # Execute saga rollbacks
        rollback_result = await SagaManager.execute_rollback(
            session=session,
            workspace_id=principal.workspace_id,
            task_id=task_id,
        )

        # Write outbox event for cancellation
        await write_outbox_event(
            session=session,
            workspace_id=principal.workspace_id,
            task_id=task_id,
            event_type=TaskEventType.TASK_CANCELLED,
            payload={
                "task_id": str(task_id),
                "status": "cancelled",
                "reason": "aborted_by_user",
                "compensations": rollback_result.executed,
            },
        )

        try:
            await record_audit(
                session,
                workspace_id=principal.workspace_id,
                user_ref=str(principal.user_id),
                status="aborted",
                action="task.aborted",
                task_id=task_id,
                output={"compensations": rollback_result.total_compensations},
            )
        except AuditWriteError:
            logger.error("abort_audit_write_failed task=%s", task_id)

        return TaskAbortResponse(
            task_id=task_id,
            status="cancelled",
            compensations_triggered=rollback_result.total_compensations,
            executed=rollback_result.executed,
            failed=rollback_result.failed,
            notifications=rollback_result.notifications,
        )


@router.post(
    "/{task_id}/input",
    response_model=TaskInputResponse,
    summary="Answer the question of a step that paused for a detail",
)
async def answer_task_input(
    task_id: UUID,
    payload: TaskInputRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
    queue: Annotated[SubtaskQueue | None, Depends(get_subtask_queue)] = None,
) -> TaskInputResponse:
    """Resume a paused step (``needs_input``) from the task card, same path as the chat card."""
    message = await TaskSubmissionService(
        engine=db.engine, gateway=None, queue=queue
    ).resume_with_input(
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        task_id=task_id,
        subtask_id=payload.subtask_id,
        answers=[a.model_dump() for a in payload.answers],
    )
    return TaskInputResponse(message=message)


_artifacts = ArtifactService()
_CANCEL_FIRST = {"error": "task_not_finished", "message": "Cancel the task first"}


@router.post(
    "/delete",
    response_model=TaskBulkDeleteResponse,
    summary="Delete several finished tasks (at most 100)",
)
async def delete_tasks_bulk(
    payload: TaskBulkDeleteRequest,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
) -> TaskBulkDeleteResponse:
    """Delete the caller's finished tasks. Unfinished ones are listed in ``not_finished`` and
    left alone; ids that are unknown, foreign or already deleted are ``not_found``."""
    outcome = await delete_tasks(
        db.engine,
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        task_ids=payload.ids,
        storage_root=_artifacts.storage_root,
    )
    return TaskBulkDeleteResponse(
        deleted=outcome.deleted,
        not_found=outcome.not_found,
        not_finished=outcome.not_finished,
        files_removed=outcome.files_removed,
    )


@router.delete(
    "/{task_id}",
    status_code=status.HTTP_204_NO_CONTENT,
    summary="Delete one finished task from history",
)
async def delete_task(
    task_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
) -> None:
    """204 when deleted; 404 for an unknown, foreign or already deleted task; 409
    ``task_not_finished`` ("Cancel the task first") while it is still running or waiting."""
    outcome = await delete_tasks(
        db.engine,
        workspace_id=principal.workspace_id,
        user_id=principal.user_id,
        task_ids=[task_id],
        storage_root=_artifacts.storage_root,
    )
    if outcome.not_finished:
        raise HTTPException(status_code=status.HTTP_409_CONFLICT, detail=_CANCEL_FIRST)
    if outcome.not_found:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found in workspace",
        )


@router.get(
    "/{task_id}/events",
    summary="SSE live progress stream for a task",
)
async def task_events_stream(
    task_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    db: Annotated[TenantDatabase, Depends(get_tenant_database)],
    publisher: Annotated[TaskEventPublisher, Depends(get_event_publisher)],
) -> StreamingResponse:
    """Stream live task and subtask progress events via Server-Sent Events (SSE)."""
    from sqlalchemy import select

    async with tenant_session(db.engine, principal.workspace_id, principal.user_id) as session:
        found = (
            await session.execute(
                select(Task.id).where(
                    Task.id == task_id,
                    Task.workspace_id == principal.workspace_id,
                    not_deleted(),
                )
            )
        ).first()
    if found is None:
        raise HTTPException(
            status_code=status.HTTP_404_NOT_FOUND,
            detail=f"Task {task_id} not found in workspace",
        )

    async def event_generator() -> AsyncIterator[str]:
        # Initial connect ping
        initial_data = json.dumps(
            {
                "event_type": "connected",
                "task_id": str(task_id),
                "message": "SSE connection established",
            }
        )
        yield f"data: {initial_data}\n\n"

        channel = get_event_channel(workspace_id=principal.workspace_id)
        if publisher.redis_client is None:
            # Fallback mock keep-alive for disconnected/mock modes
            yield f"data: {json.dumps({'event_type': 'heartbeat', 'task_id': str(task_id)})}\n\n"
            return

        pubsub = publisher.redis_client.pubsub()
        await pubsub.subscribe(channel)

        try:
            while True:
                message = await pubsub.get_message(
                    ignore_subscribe_messages=True,
                    timeout=1.0,
                )
                if message is not None:
                    raw_data = message["data"]
                    if isinstance(raw_data, bytes):
                        raw_data = raw_data.decode("utf-8")
                    try:
                        event_payload = json.loads(raw_data)
                        # Filter to the requested task if specified in session_id or payload
                        evt_task_id = event_payload.get("session_id") or event_payload.get(
                            "payload", {}
                        ).get("task_id")
                        if evt_task_id is None or str(evt_task_id) == str(task_id):
                            yield f"data: {raw_data}\n\n"
                    except Exception:
                        yield f"data: {raw_data}\n\n"
                else:
                    # SSE keep-alive heartbeat comment
                    yield ": ping\n\n"
                    await asyncio.sleep(1.0)
        except asyncio.CancelledError:
            logger.debug("SSE client disconnected for task %s", task_id)
        finally:
            await pubsub.unsubscribe(channel)
            await pubsub.close()

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache, no-transform",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
