"""Progress and cancel for a research run.

A run shows its work through the task progress machinery (the work-log UI): one step per phase,
updated in place ("Read 12 of 24 sources"). ``TaskProgress`` writes each step as its own short
tenant transaction and reads the task status to see a cancel; both are fail-soft (a progress write
never fails the run) except that a failed status read means "not cancelled".
"""

from __future__ import annotations

import logging
from typing import Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import Task
from app.db.tenant import tenant_session
from app.tasks.progress_steps import emit_task_step, subtask_step_id

logger = logging.getLogger(__name__)

STEP_PLAN = "dr-plan"
STEP_SEARCH = "dr-search"
STEP_READ = "dr-read"
STEP_SKIPPED = "dr-skipped"
STEP_ANALYSE = "dr-analyse"
STEP_WRITE = "dr-write"
STEP_CHECK = "dr-check"


class ProgressSink(Protocol):
    async def step(
        self, step_id: str, kind: str, label: str, state: str, detail: str | None = None
    ) -> None: ...

    async def cancelled(self) -> bool: ...


class NullProgress:
    """No progress and never cancelled (tests, and runs without a task)."""

    async def step(
        self, step_id: str, kind: str, label: str, state: str, detail: str | None = None
    ) -> None:
        return None

    async def cancelled(self) -> bool:
        return False


class TaskProgress:
    def __init__(
        self,
        engine: AsyncEngine,
        *,
        workspace_id: UUID,
        user_id: UUID,
        task_id: UUID,
        subtask_id: UUID,
    ) -> None:
        self._engine = engine
        self._workspace_id = workspace_id
        self._user_id = user_id
        self._task_id = task_id
        self._subtask_id = subtask_id

    async def step(
        self, step_id: str, kind: str, label: str, state: str, detail: str | None = None
    ) -> None:
        try:
            async with tenant_session(self._engine, self._workspace_id, self._user_id) as session:
                await emit_task_step(
                    session,
                    workspace_id=self._workspace_id,
                    task_id=self._task_id,
                    subtask_id=self._subtask_id,
                    step_id=step_id,
                    kind=kind,
                    label=label,
                    state=state,
                    detail=detail,
                    parent_id=subtask_step_id(self._subtask_id),
                )
        except Exception as error:  # noqa: BLE001 - progress never fails the run
            logger.warning("research_progress_not_written error=%s", type(error).__name__)

    async def cancelled(self) -> bool:
        try:
            async with tenant_session(self._engine, self._workspace_id, self._user_id) as session:
                status = (
                    await session.execute(
                        select(Task.status).where(
                            Task.id == self._task_id, Task.workspace_id == self._workspace_id
                        )
                    )
                ).scalar_one_or_none()
        except Exception as error:  # noqa: BLE001 - unknown means keep going
            logger.warning("research_cancel_check_failed error=%s", type(error).__name__)
            return False
        return status == "cancelled"
