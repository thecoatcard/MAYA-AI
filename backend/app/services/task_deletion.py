"""Delete finished tasks from the user's history.

A deleted task is a tombstone, not a removed row. ``task_outbox_events`` references ``tasks`` with
``ON DELETE CASCADE`` and the audit trail must never be lost, so the row stays (marked in
``shared_context``, no migration) while everything user-facing goes:

Removed: the task's title, instruction, plan, results and trace, its steps (subtasks), its
generated file records and MAYA's stored copies of those files. Pending approvals are closed
(``revoked``) so none is left waiting for a task that no longer shows.

Kept: the audit log and the outbox history (they keep the task id), and any copy of a file that
was saved into the user's own connected folder.

Only a finished task (completed, failed or cancelled) can be deleted; a running one must be
cancelled first.
"""

from __future__ import annotations

import asyncio
import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime
from pathlib import Path
from typing import Any
from uuid import UUID

from sqlalchemy import delete, select, update
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import ApprovalRequest, FileArtifact, Subtask, Task
from app.db.tenant import tenant_session
from app.services.audit_service import AuditWriteError, record_audit

logger = logging.getLogger(__name__)

DELETED_KEY = "deleted_at"
MAX_BULK_DELETE = 100
FINISHED_STATUSES = frozenset({"completed", "failed", "cancelled", "canceled", "aborted"})
DELETED_TITLE = "Deleted task"


def not_deleted() -> Any:
    """SQL condition: the task has not been deleted by its owner."""
    return ~Task.shared_context.has_key(DELETED_KEY)


def is_deleted(task: Task) -> bool:
    return DELETED_KEY in (task.shared_context or {})


def task_state(task: Task) -> str:
    return str(task.status or task.plan_status.value)


@dataclass(slots=True)
class DeleteOutcome:
    deleted: list[UUID] = field(default_factory=list)
    not_found: list[UUID] = field(default_factory=list)
    not_finished: list[UUID] = field(default_factory=list)
    files_removed: int = 0


def managed_file(storage_uri: str, storage_root: Path) -> Path | None:
    """The resolved path when it lies inside MAYA's own storage, else ``None`` (never deleted).

    ``resolve`` follows ``..`` and symlinks first, so a link that leads outside the root is
    refused."""
    try:
        resolved = Path(storage_uri).resolve()
        resolved.relative_to(storage_root.resolve())
    except (ValueError, OSError):
        return None
    return resolved


def remove_file(path: Path) -> bool:
    """Best effort: the record is already gone, so a missing or busy file is not an error."""
    try:
        path.unlink(missing_ok=True)
    except OSError:
        return False
    return True


async def delete_tasks(
    engine: AsyncEngine,
    *,
    workspace_id: UUID,
    user_id: UUID,
    task_ids: list[UUID],
    storage_root: Path,
) -> DeleteOutcome:
    """Delete the caller's finished tasks. Unknown, foreign and already deleted ids are
    ``not_found`` (one answer, no information about other users); unfinished ones are refused."""
    outcome = DeleteOutcome()
    paths: list[Path] = []
    async with tenant_session(engine, workspace_id, user_id) as session:
        rows = (
            (
                await session.execute(
                    select(Task).where(
                        Task.id.in_(task_ids),
                        Task.workspace_id == workspace_id,
                        Task.user_id == user_id,
                        not_deleted(),
                    )
                )
            )
            .scalars()
            .all()
        )
        by_id = {task.id: task for task in rows}
        for task_id in dict.fromkeys(task_ids):
            task = by_id.get(task_id)
            if task is None:
                outcome.not_found.append(task_id)
            elif task_state(task) not in FINISHED_STATUSES:
                outcome.not_finished.append(task_id)
            else:
                await _remove_one(session, task, workspace_id, user_id, storage_root, paths)
                outcome.deleted.append(task_id)
        await session.commit()
    for path in paths:
        if await asyncio.to_thread(remove_file, path):
            outcome.files_removed += 1
    return outcome


async def _remove_one(
    session: Any,
    task: Task,
    workspace_id: UUID,
    user_id: UUID,
    storage_root: Path,
    paths: list[Path],
) -> None:
    now = datetime.now(UTC)
    await session.execute(
        update(ApprovalRequest)
        .where(
            ApprovalRequest.task_id == task.id,
            ApprovalRequest.workspace_id == workspace_id,
            ApprovalRequest.status == "pending",
        )
        .values(status="revoked", decided_at=now, failure_reason="task_deleted")
    )
    artifacts = (
        (
            await session.execute(
                select(FileArtifact).where(
                    FileArtifact.task_id == task.id, FileArtifact.workspace_id == workspace_id
                )
            )
        )
        .scalars()
        .all()
    )
    for artifact in artifacts:
        found = managed_file(artifact.storage_uri, storage_root)
        if found is not None:
            paths.append(found)
        await session.delete(artifact)
    await session.execute(
        delete(Subtask).where(Subtask.task_id == task.id, Subtask.workspace_id == workspace_id)
    )
    task.title = DELETED_TITLE
    task.user_instruction = ""
    task.instruction = None
    task.dag_plan = None
    task.active_plan = []
    task.execution_trace = []
    task.idempotency_key = None
    task.shared_context = {DELETED_KEY: now.isoformat()}
    try:
        await record_audit(
            session,
            workspace_id=workspace_id,
            user_ref=str(user_id),
            status="success",
            action="task.deleted",
            task_id=task.id,
            output={"files_removed": len(artifacts)},
        )
    except AuditWriteError:
        logger.error("task_delete_audit_write_failed task=%s", task.id)


async def delete_session_content(
    engine: AsyncEngine,
    *,
    workspace_id: UUID,
    user_id: UUID,
    session_id: UUID,
    storage_root: Path,
) -> DeleteOutcome:
    """Delete a conversation's finished tasks and every file MAYA stored for it in its chat
    folder. Unfinished tasks are left alone. Nothing outside ``storage_root`` is touched."""
    async with tenant_session(engine, workspace_id, user_id) as session:
        ids = list(
            (
                await session.execute(
                    select(Task.id).where(
                        Task.session_id == session_id,
                        Task.workspace_id == workspace_id,
                        Task.user_id == user_id,
                        not_deleted(),
                    )
                )
            )
            .scalars()
            .all()
        )
    outcome = DeleteOutcome()
    for start in range(0, len(ids), MAX_BULK_DELETE):
        part = await delete_tasks(
            engine,
            workspace_id=workspace_id,
            user_id=user_id,
            task_ids=ids[start : start + MAX_BULK_DELETE],
            storage_root=storage_root,
        )
        outcome.deleted += part.deleted
        outcome.not_finished += part.not_finished
        outcome.files_removed += part.files_removed
    folder = managed_file(
        str(storage_root / str(workspace_id) / "chat" / str(session_id)), storage_root
    )
    if folder is not None and folder.is_dir() and not folder.is_symlink():
        outcome.files_removed += await asyncio.to_thread(_remove_folder, folder)
    return outcome


def _remove_folder(folder: Path) -> int:
    """Remove the regular files directly inside a chat folder, then the folder if empty."""
    count = 0
    for entry in folder.iterdir():
        if entry.is_file() and not entry.is_symlink() and remove_file(entry):
            count += 1
    try:
        folder.rmdir()
    except OSError:
        pass
    return count
