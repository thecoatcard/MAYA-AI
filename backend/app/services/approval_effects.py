"""Task-state effects of an approval decision that the approvals route must apply."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.db.models import PlanStatus, Subtask, SubtaskStatus, Task
from app.tasks.events import TaskEventType
from app.tasks.outbox import write_outbox_event


async def fail_subtask_after_rejection(
    conn: AsyncConnection, *, workspace_id: UUID, subtask_id: UUID, reason: str | None
) -> bool:
    """A rejected approval ends the paused subtask and its task, honestly.

    Without this the subtask would stay ``awaiting_approval`` forever after a human said no.
    Runs inside the caller's tenant transaction; returns whether a paused subtask was failed.
    """
    message = "Approval rejected" + (f": {reason}" if reason else "")
    async with AsyncSession(bind=conn, expire_on_commit=False) as session:
        subtask = await session.get(Subtask, subtask_id)
        if (
            subtask is None
            or subtask.workspace_id != workspace_id
            or subtask.status != SubtaskStatus.AWAITING_APPROVAL
        ):
            return False
        subtask.status = SubtaskStatus.FAILED
        subtask.error_message = message
        task = await session.get(Task, subtask.task_id)
        if task is not None and task.status != "cancelled":
            task.status = "failed"
            task.plan_status = PlanStatus.FAILED
        for event_type, payload in (
            (TaskEventType.SUBTASK_STATUS_CHANGED, {"status": "failed", "error": message}),
            (TaskEventType.TASK_FAILED, {"error": "approval_rejected", "message": message}),
        ):
            await write_outbox_event(
                session,
                workspace_id,
                subtask.task_id,
                event_type,
                {"task_id": str(subtask.task_id), "subtask_id": str(subtask_id), **payload},
            )
        await session.flush()
    return True
