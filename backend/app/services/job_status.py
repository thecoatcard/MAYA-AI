"""Reads one chat job's status from the existing task rows (ADR-0026, ``check_job``). Read only.

The job is a task created by ``TaskSubmissionService``, so its state, steps and files are the ones
the task ledger shows. Reads run in a tenant transaction (row-level security) and only for the
workspace and user that started the job.
"""

from __future__ import annotations

from typing import Any, Final
from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import FileArtifact, Subtask, SubtaskStatus, Task
from app.db.tenant import tenant_session
from app.services.chat_grounding import SessionJob, payload_hash
from app.services.document_tools import JobView
from app.services.task_deletion import is_deleted, not_deleted

_DONE: Final = frozenset({SubtaskStatus.COMPLETED, SubtaskStatus.FAILED})


class TaskJobReader:
    """``JobReader`` over the task, subtask and file artifact rows."""

    def __init__(self, engine: AsyncEngine) -> None:
        self._engine = engine

    async def read(self, *, workspace_id: UUID, user_id: UUID, job_id: UUID) -> JobView | None:
        async with tenant_session(self._engine, workspace_id, user_id) as session:
            task = await session.get(Task, job_id)
            if (
                task is None
                or task.workspace_id != workspace_id
                or task.user_id != user_id
                or is_deleted(task)
            ):
                return None
            subtasks = (
                (
                    await session.execute(
                        select(Subtask)
                        .where(Subtask.task_id == job_id, Subtask.workspace_id == workspace_id)
                        .order_by(Subtask.created_at)
                    )
                )
                .scalars()
                .all()
            )
            artifacts = (
                (
                    await session.execute(
                        select(FileArtifact)
                        .where(
                            FileArtifact.task_id == job_id,
                            FileArtifact.workspace_id == workspace_id,
                        )
                        .order_by(FileArtifact.created_at)
                    )
                )
                .scalars()
                .all()
            )
        current = next((s for s in subtasks if s.status not in _DONE), None)
        if current is None and subtasks:
            current = subtasks[-1]
        files: list[dict[str, Any]] = [
            {
                "file_id": str(artifact.id),
                "name": artifact.file_name,
                "file_type": artifact.file_type,
                "size_bytes": artifact.size_bytes,
                "verification_status": artifact.verification_status,
            }
            for artifact in artifacts
        ]
        return JobView(
            job_id=task.id,
            title=task.title or "",
            status=str(task.status or task.plan_status.value),
            current_step=current.description[:200] if current is not None else None,
            files=tuple(files),
            failed_steps=tuple(
                f"{s.description[:80]}: {(s.error_message or 'failed')[:120]}"
                for s in subtasks
                if s.status == SubtaskStatus.FAILED
            ),
        )

    async def session_activity(
        self, *, workspace_id: UUID, user_id: UUID, session_id: UUID, limit: int = 6
    ) -> list[SessionJob]:
        """The newest jobs of one chat session with their real status and, for a tool step, the
        tool and the hash of its exact arguments (to stop a duplicate approval). Read only."""
        async with tenant_session(self._engine, workspace_id, user_id) as session:
            tasks = (
                (
                    await session.execute(
                        select(Task)
                        .where(
                            Task.session_id == session_id,
                            Task.workspace_id == workspace_id,
                            Task.user_id == user_id,
                            not_deleted(),
                        )
                        .order_by(Task.created_at.desc())
                        .limit(limit)
                    )
                )
                .scalars()
                .all()
            )
            ids = [t.id for t in tasks]
            subtasks = (
                (
                    await session.execute(
                        select(Subtask)
                        .where(Subtask.task_id.in_(ids), Subtask.workspace_id == workspace_id)
                        .order_by(Subtask.created_at)
                    )
                )
                .scalars()
                .all()
                if ids
                else []
            )
        jobs: list[SessionJob] = []
        for task in reversed(tasks):  # oldest first
            own = [s for s in subtasks if s.task_id == task.id]
            step = next((s for s in own if s.input_data.get("tool")), None)
            failed = tuple(
                f"{s.description[:80]}: {(s.error_message or 'failed')[:120]}"
                for s in own
                if s.status == SubtaskStatus.FAILED
            )
            error = next((s.error_message for s in own if s.error_message), None) or ""
            jobs.append(
                SessionJob(
                    task_id=task.id,
                    title=task.title or task.instruction or "",
                    status=str(task.status or task.plan_status.value),
                    tool=str(step.input_data["tool"]) if step is not None else "",
                    payload_hash=(
                        payload_hash(
                            str(step.input_data["tool"]),
                            dict(step.input_data.get("tool_args") or {}),
                        )
                        if step is not None
                        else ""
                    ),
                    error=error[:160],
                    failed_steps=failed,
                )
            )
        return jobs


__all__ = ["TaskJobReader"]
