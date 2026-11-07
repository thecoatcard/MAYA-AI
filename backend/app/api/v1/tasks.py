"""API routes for Task Lifecycle Management (Phase 06)."""

from __future__ import annotations

import asyncio
import json
import logging
from collections.abc import AsyncIterator
from decimal import Decimal
from typing import Annotated, Any
from uuid import UUID

from fastapi import APIRouter, Depends, Header, HTTPException, Query, Request, Response, status
from starlette.responses import StreamingResponse

from app.agents.planner import DAGPlanner
from app.agents.state import SubTask
from app.auth.dependencies import TenantDatabase, get_principal, get_tenant_database
from app.auth.models import Principal
from app.db.models import (
    PlanStatus,
    Subtask,
    SubtaskAgent,
    SubtaskStatus,
    Task,
)
from app.db.tenant import tenant_session
from app.gateway.model_gateway import ModelGateway
from app.schemas.tasks import (
    SubtaskResponse,
    TaskAbortResponse,
    TaskCreateRequest,
    TaskDetailResponse,
    TaskListItem,
    TaskListResponse,
    TaskSubmissionResponse,
)
from app.services.projection_service import resolve_subtask_uuid
from app.tasks.events import (
    TaskEventPublisher,
    TaskEventType,
    get_event_channel,
)
from app.tasks.idempotency import TaskIdempotencyService
from app.tasks.outbox import write_outbox_event
from app.tasks.queue import SubtaskJob, SubtaskQueue
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

    async with tenant_session(db.engine, principal.workspace_id, principal.user_id) as session:
        task, is_new = await TaskIdempotencyService.get_or_create_task(
            session=session,
            workspace_id=principal.workspace_id,
            user_id=principal.user_id,
            idempotency_key=idempotency_key,
            user_instruction=payload.instruction,
            title=payload.title,
            budget_usd=payload.budget_usd,
            session_id=str(payload.session_id) if payload.session_id else None,
        )

        if not is_new:
            response.status_code = status.HTTP_200_OK
            return TaskSubmissionResponse(
                task_id=task.id,
                status=task.status or "pending",
                idempotency_key=idempotency_key,
                created_at=task.created_at,
                is_new=False,
            )

        # Generate DAG plan for new task
        subtask_nodes: list[Any] = []
        if model_gateway is not None:
            planner = DAGPlanner(gateway=model_gateway)
            try:
                _intent, subtask_nodes = await planner.plan(
                    instruction=payload.instruction,
                    workspace_id=principal.workspace_id,
                    task_id=task.id,
                )
            except Exception as exc:
                logger.warning("Model planner failed, using root subtask fallback: %s", exc)
                subtask_nodes = [
                    SubTask(
                        id="step_1",
                        assigned_agent="research",
                        description=payload.instruction,
                        dependencies=[],
                        status="pending",
                        input_data={},
                    )
                ]
        else:
            subtask_nodes = [
                SubTask(
                    id="step_1",
                    assigned_agent="research",
                    description=payload.instruction,
                    dependencies=[],
                    status="pending",
                    input_data={},
                )
            ]

        # Project initial agent execution state to relational tables
        subtask_dicts = [node.model_dump() for node in subtask_nodes]
        task.active_plan = subtask_dicts
        task.status = "executing"
        task.plan_status = PlanStatus.EXECUTING

        ready_subtasks: list[tuple[UUID, str]] = []
        for node in subtask_nodes:
            agent_str = str(getattr(node.assigned_agent, "value", node.assigned_agent))
            agent_enum = (
                SubtaskAgent(agent_str)
                if agent_str in SubtaskAgent._value2member_map_
                else SubtaskAgent.RESEARCH
            )
            st_uuid = resolve_subtask_uuid(task.id, str(node.id))
            dep_uuids = [str(resolve_subtask_uuid(task.id, str(dep))) for dep in node.dependencies]
            st = Subtask(
                id=st_uuid,
                task_id=task.id,
                workspace_id=principal.workspace_id,
                assigned_agent=agent_enum,
                description=node.description,
                dependencies=dep_uuids,
                status=SubtaskStatus.PENDING,
                input_data=node.input_data,
            )
            session.add(st)
            if not node.dependencies:
                ready_subtasks.append((st_uuid, agent_str))

        task.execution_trace = [{"action": "plan_generated", "subtask_count": len(subtask_nodes)}]

        # Write outbox event atomically
        await write_outbox_event(
            session=session,
            workspace_id=principal.workspace_id,
            task_id=task.id,
            event_type=TaskEventType.TASK_PLAN_CREATED,
            payload={
                "task_id": str(task.id),
                "instruction": payload.instruction,
                "subtasks_count": len(subtask_nodes),
            },
        )

        # Enqueue ready root subtasks to execution queue if available
        if queue is not None:
            for ready_id, agent_name in ready_subtasks:
                await queue.enqueue(
                    SubtaskJob(
                        workspace_id=principal.workspace_id,
                        user_id=principal.user_id,
                        task_id=task.id,
                        subtask_id=ready_id,
                        assigned_agent=agent_name,
                    )
                )

        return TaskSubmissionResponse(
            task_id=task.id,
            status="executing",
            idempotency_key=idempotency_key,
            created_at=task.created_at,
            is_new=True,
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
        query = select(Task).where(Task.workspace_id == principal.workspace_id)
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
            .where(Task.id == task_id, Task.workspace_id == principal.workspace_id)
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
            .where(Task.id == task_id, Task.workspace_id == principal.workspace_id)
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
            event_type=TaskEventType.TASK_FAILED,
            payload={
                "task_id": str(task_id),
                "status": "cancelled",
                "reason": "aborted_by_user",
                "compensations": rollback_result.executed,
            },
        )

        return TaskAbortResponse(
            task_id=task_id,
            status="cancelled",
            compensations_triggered=rollback_result.total_compensations,
            executed=rollback_result.executed,
            failed=rollback_result.failed,
            notifications=rollback_result.notifications,
        )


@router.get(
    "/{task_id}/events",
    summary="SSE live progress stream for a task",
)
async def task_events_stream(
    task_id: UUID,
    principal: Annotated[Principal, Depends(get_principal)],
    publisher: Annotated[TaskEventPublisher, Depends(get_event_publisher)],
) -> StreamingResponse:
    """Stream live task and subtask progress events via Server-Sent Events (SSE)."""

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
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )
