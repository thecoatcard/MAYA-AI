"""Reads the state of the task a scheduled run started (and cancels it on a timeout)."""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import FileArtifact, Subtask, Task
from app.db.tenant import tenant_session
from app.services.progress import clean_text

logger = logging.getLogger("maya.schedules")

State = Literal["running", "completed", "failed", "needs_attention", "cancelled", "missing"]
_ATTENTION = frozenset({"awaiting_approval", "awaiting_input"})


@dataclass(frozen=True)
class TaskOutcome:
    state: State
    report: str = ""
    artifact_ids: tuple[str, ...] = ()
    reason: str = ""
    sources: int = 0


class TaskProbe(Protocol):
    async def check(self, workspace_id: UUID, user_id: UUID, task_id: str) -> TaskOutcome: ...

    async def cancel(self, workspace_id: UUID, user_id: UUID, task_id: str) -> None: ...


def _text(result: object) -> str:
    if isinstance(result, dict):
        value = result.get("summary") or result.get("result") or ""
        return str(value)
    return ""


class DbTaskProbe:
    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def check(self, workspace_id: UUID, user_id: UUID, task_id: str) -> TaskOutcome:
        try:
            tid = UUID(task_id)
        except ValueError:
            return TaskOutcome("missing", reason="The task id was not valid.")
        async with tenant_session(self._engine, workspace_id, user_id) as session:
            task = (await session.execute(select(Task).where(Task.id == tid))).scalars().first()
            if task is None:
                return TaskOutcome("missing", reason="The task no longer exists.")
            status = str(task.status or "")
            if status in _ATTENTION:
                return TaskOutcome("needs_attention", reason=_attention_reason(status))
            if status == "cancelled":
                return TaskOutcome("cancelled", reason="The task was cancelled.")
            if status == "failed":
                return TaskOutcome("failed", reason=await _failure_reason(session, tid))
            if status != "completed":
                return TaskOutcome("running")
            subtasks = (
                (
                    await session.execute(
                        select(Subtask).where(Subtask.task_id == tid).order_by(Subtask.created_at)
                    )
                )
                .scalars()
                .all()
            )
            ids = (
                (await session.execute(select(FileArtifact.id).where(FileArtifact.task_id == tid)))
                .scalars()
                .all()
            )
        report = ""
        sources = 0
        for sub in subtasks:
            agent = str(getattr(sub.assigned_agent, "value", sub.assigned_agent))
            if agent == "research" and _text(sub.output_result):
                report = _text(sub.output_result)
                citations = (sub.output_result or {}).get("citations")
                sources = len(citations) if isinstance(citations, list) else 0
        if not report:
            for sub in reversed(subtasks):
                if _text(sub.output_result):
                    report = _text(sub.output_result)
                    break
        return TaskOutcome(
            "completed", report=report, artifact_ids=tuple(str(i) for i in ids), sources=sources
        )

    async def cancel(self, workspace_id: UUID, user_id: UUID, task_id: str) -> None:
        try:
            tid = UUID(task_id)
        except ValueError:
            return
        async with tenant_session(self._engine, workspace_id, user_id) as session:
            task = (await session.execute(select(Task).where(Task.id == tid))).scalars().first()
            if task is not None and task.status in ("executing", "pending"):
                task.status = "cancelled"
            await session.commit()


def _attention_reason(status: str) -> str:
    if status == "awaiting_approval":
        return "A step needs your approval. The approval card is waiting in Tasks."
    return "A step needs an answer from you. The question is waiting in Tasks."


async def _failure_reason(session: object, task_id: UUID) -> str:
    from sqlalchemy.ext.asyncio import AsyncSession

    assert isinstance(session, AsyncSession)
    rows = (
        (
            await session.execute(
                select(Subtask.error_message).where(
                    Subtask.task_id == task_id, Subtask.error_message.is_not(None)
                )
            )
        )
        .scalars()
        .all()
    )
    detail = clean_text(str(rows[0]), 160) if rows else ""
    return f"The task failed. {detail}".strip()
