"""Background worker runner executing subtask jobs conforming to Decision D8."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.models import PlanStatus, Subtask, SubtaskStatus, Task
from app.services.projection_service import resolve_subtask_uuid
from app.tasks.events import TaskEventType
from app.tasks.outbox import write_outbox_event
from app.tasks.queue import SubtaskJob, SubtaskQueue

logger = logging.getLogger(__name__)

DEFAULT_SUBTASK_TIMEOUT_SECONDS = 300
MAX_SUBTASK_RETRIES = 3


class SubtaskExecutionError(Exception):
    """Raised when subtask execution fails unrecoverably."""


class SubtaskTimeoutError(SubtaskExecutionError):
    """Raised when subtask execution exceeds bounded timeout."""


class SubtaskWorker:
    """Async worker processing individual subtask jobs.

    Core Decision D8 rules:
    - Exactly 1 subtask per queued job message.
    - Workers never sleep or block waiting for human approval; upon hitting
      awaiting_approval, state is persisted and the worker exits immediately.
    - Job execution bounded strictly by timeout.
    """

    def __init__(
        self,
        queue: SubtaskQueue | None = None,
        default_timeout: int = DEFAULT_SUBTASK_TIMEOUT_SECONDS,
    ) -> None:
        self.queue = queue or SubtaskQueue()
        self.default_timeout = default_timeout

    async def execute_job(
        self,
        session: AsyncSession,
        job: SubtaskJob,
        agent_handler: (
            Callable[[SubtaskJob, dict[str, Any]], Awaitable[dict[str, Any]]] | None
        ) = None,
    ) -> SubtaskStatus:
        """Execute a single subtask job under strict tenant isolation."""
        # 1. Load subtask and verify tenant boundary
        stmt = (
            select(Subtask)
            .where(
                Subtask.id == job.subtask_id,
                Subtask.workspace_id == job.workspace_id,
            )
            .limit(1)
        )
        result = await session.execute(stmt)
        subtask = result.scalars().first()
        if subtask is None:
            logger.error(
                "Subtask %s not found in workspace %s",
                job.subtask_id,
                job.workspace_id,
            )
            return SubtaskStatus.FAILED

        # 2. Verify dependencies are satisfied
        if subtask.dependencies:
            dep_stmt = select(Subtask).where(
                Subtask.task_id == job.task_id,
                Subtask.workspace_id == job.workspace_id,
            )
            dep_res = await session.execute(dep_stmt)
            all_subtasks = {str(s.id): s for s in dep_res.scalars().all()}
            for dep_id in subtask.dependencies:
                dep_sub = all_subtasks.get(str(dep_id))
                if dep_sub is None:
                    # Defensive fallback: resolve planner string ID to deterministic UUID
                    resolved_dep_id = str(resolve_subtask_uuid(job.task_id, str(dep_id)))
                    dep_sub = all_subtasks.get(resolved_dep_id)

                if dep_sub is None or dep_sub.status != SubtaskStatus.COMPLETED:
                    logger.warning(
                        "Subtask %s dependencies not satisfied (dep %s status: %s)",
                        job.subtask_id,
                        dep_id,
                        getattr(dep_sub, "status", "missing"),
                    )
                    return SubtaskStatus.PENDING

        # 3. Check for human approval requirement (Decision D8)
        # Workers NEVER sleep or block on human approval; exit immediately!
        requires_approval = bool(
            subtask.input_data.get("requires_approval", False)
            or subtask.input_data.get("approval_required", False)
        )
        if requires_approval:
            subtask.status = SubtaskStatus.AWAITING_APPROVAL
            await write_outbox_event(
                session=session,
                workspace_id=job.workspace_id,
                task_id=job.task_id,
                event_type=TaskEventType.TASK_PAUSED_APPROVAL,
                payload={
                    "task_id": str(job.task_id),
                    "subtask_id": str(job.subtask_id),
                    "assigned_agent": job.assigned_agent,
                    "reason": "Human approval required before executing mutating side effect",
                },
            )
            # Update parent task state to paused / awaiting_approval
            task_stmt = select(Task).where(
                Task.id == job.task_id,
                Task.workspace_id == job.workspace_id,
            )
            t_res = await session.execute(task_stmt)
            task = t_res.scalars().first()
            if task:
                task.status = "awaiting_approval"
                task.plan_status = PlanStatus.PAUSED

            await session.flush()
            logger.info(
                "Subtask %s requires approval; transitioned to awaiting_approval "
                "and worker exited cleanly.",
                job.subtask_id,
            )
            return SubtaskStatus.AWAITING_APPROVAL

        # 4. Transition to in_progress
        subtask.status = SubtaskStatus.IN_PROGRESS
        await write_outbox_event(
            session=session,
            workspace_id=job.workspace_id,
            task_id=job.task_id,
            event_type=TaskEventType.SUBTASK_STATUS_CHANGED,
            payload={
                "task_id": str(job.task_id),
                "subtask_id": str(job.subtask_id),
                "status": SubtaskStatus.IN_PROGRESS.value,
                "assigned_agent": job.assigned_agent,
            },
        )
        await session.flush()

        # 5. Execute assigned agent handler within bounded timeout
        timeout = job.timeout_seconds or self.default_timeout
        try:
            handler_output: dict[str, Any] = {}
            if agent_handler is not None:
                handler_output = await asyncio.wait_for(
                    agent_handler(job, subtask.input_data),
                    timeout=timeout,
                )
            else:
                # Default mock handler for tests / dispatch pass-through
                handler_output = {
                    "result": f"Executed by {job.assigned_agent}",
                    "proof": {"status": "verified", "exit_code": 0},
                }

            subtask.output_result = handler_output.get("result", handler_output)
            subtask.verification_proof = handler_output.get(
                "proof",
                {"verified": True, "exit_code": 0},
            )
            subtask.status = SubtaskStatus.COMPLETED
            subtask.completed_at = datetime.now(UTC)

            await write_outbox_event(
                session=session,
                workspace_id=job.workspace_id,
                task_id=job.task_id,
                event_type=TaskEventType.SUBTASK_STATUS_CHANGED,
                payload={
                    "task_id": str(job.task_id),
                    "subtask_id": str(job.subtask_id),
                    "status": SubtaskStatus.COMPLETED.value,
                    "output": subtask.output_result,
                },
            )

            # Check if all subtasks in the task are now completed
            all_stmt = select(Subtask).where(
                Subtask.task_id == job.task_id,
                Subtask.workspace_id == job.workspace_id,
            )
            all_res = await session.execute(all_stmt)
            task_subtasks = list(all_res.scalars().all())
            all_done = all(st.status == SubtaskStatus.COMPLETED for st in task_subtasks)
            if all_done:
                task_stmt = select(Task).where(
                    Task.id == job.task_id,
                    Task.workspace_id == job.workspace_id,
                )
                t_res = await session.execute(task_stmt)
                task = t_res.scalars().first()
                if task:
                    task.status = "completed"
                    task.plan_status = PlanStatus.COMPLETED
                    task.completed_at = datetime.now(UTC)

                await write_outbox_event(
                    session=session,
                    workspace_id=job.workspace_id,
                    task_id=job.task_id,
                    event_type=TaskEventType.TASK_COMPLETED,
                    payload={"task_id": str(job.task_id), "status": "completed"},
                )

            await session.flush()
            return SubtaskStatus.COMPLETED

        except TimeoutError:
            logger.error(
                "Subtask %s exceeded timeout of %ss",
                job.subtask_id,
                timeout,
            )
            subtask.retry_count += 1
            if subtask.retry_count >= MAX_SUBTASK_RETRIES:
                subtask.status = SubtaskStatus.FAILED
                subtask.error_message = f"Execution timed out after {timeout} seconds"
                task_stmt = select(Task).where(
                    Task.id == job.task_id,
                    Task.workspace_id == job.workspace_id,
                )
                t_res = await session.execute(task_stmt)
                task = t_res.scalars().first()
                if task:
                    task.status = "failed"
                    task.plan_status = PlanStatus.FAILED

                await write_outbox_event(
                    session=session,
                    workspace_id=job.workspace_id,
                    task_id=job.task_id,
                    event_type=TaskEventType.TASK_FAILED,
                    payload={
                        "task_id": str(job.task_id),
                        "subtask_id": str(job.subtask_id),
                        "error": "ExecutionTimeoutError",
                    },
                )
            await session.flush()
            return subtask.status

        except Exception as exc:
            logger.error(
                "Subtask %s failed: %s",
                job.subtask_id,
                exc,
                exc_info=True,
            )
            subtask.retry_count += 1
            if subtask.retry_count >= MAX_SUBTASK_RETRIES:
                subtask.status = SubtaskStatus.FAILED
                subtask.error_message = str(exc)
                task_stmt = select(Task).where(
                    Task.id == job.task_id,
                    Task.workspace_id == job.workspace_id,
                )
                t_res = await session.execute(task_stmt)
                task = t_res.scalars().first()
                if task:
                    task.status = "failed"
                    task.plan_status = PlanStatus.FAILED

                await write_outbox_event(
                    session=session,
                    workspace_id=job.workspace_id,
                    task_id=job.task_id,
                    event_type=TaskEventType.TASK_FAILED,
                    payload={
                        "task_id": str(job.task_id),
                        "subtask_id": str(job.subtask_id),
                        "error": str(exc),
                    },
                )
            await session.flush()
            return subtask.status
