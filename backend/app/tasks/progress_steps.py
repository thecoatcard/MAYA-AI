"""Progress steps for background tasks, queued as transactional outbox events.

A task step is a ``ProgressStep`` (see ``app.services.progress``) written as a ``task_progress``
outbox event in the same transaction as the work it describes. The outbox relay publishes it on the
workspace task channel, where clients match it to the task through ``task_id``. A chat turn that
started a task receives the same task id in its ``task.created`` event.

Writing a step never fails the work: it runs inside a savepoint, so a failed write rolls back only
itself, and the error is logged and dropped.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID

from sqlalchemy.ext.asyncio import AsyncSession

from app.services.progress import ProgressStep, clean_text
from app.tasks.events import TaskEventType
from app.tasks.outbox import write_outbox_event

logger = logging.getLogger(__name__)


def subtask_step_id(subtask_id: UUID) -> str:
    """Stable id of a subtask's step, so each state change updates the same step."""
    return f"sub-{subtask_id}"


def agent_label(agent: str | None) -> str:
    """A short human label for a worker agent, for example ``Research agent``."""
    base = clean_text(str(agent or "task").replace("_", " ").replace("-", " "), 40) or "task"
    return f"{base[:1].upper()}{base[1:]} agent"


def step_label(input_data: dict[str, Any] | None, agent: str | None, title: str = "") -> str:
    """The label a step shows on the task card: the plan's human ``step_label`` ("Plan the book",
    "Chapter 3 of 10", "Create the Notion page"), with the chapter ``title`` when it is known;
    steps of other plans keep the worker agent label (``Research agent``)."""
    raw = (input_data or {}).get("step_label")
    label = clean_text(raw, 80) if isinstance(raw, str) else ""
    if not label:
        return agent_label(agent)
    chapter = clean_text(title, 60)
    return f"{label}: {chapter}" if chapter and label.startswith("Chapter ") else label


async def emit_task_step(
    session: AsyncSession,
    *,
    workspace_id: UUID,
    task_id: UUID,
    subtask_id: UUID | None,
    step_id: str,
    kind: str,
    label: str,
    state: str,
    detail: str | None = None,
    parent_id: str | None = None,
) -> None:
    """Queue one step event for a task. Never raises: the task's own work must still succeed."""
    try:
        step = ProgressStep(
            run_id=str(task_id),
            step_id=step_id,
            parent_id=parent_id,
            kind=kind,
            label=label,
            detail=detail,
            state=state,
        )
        payload: dict[str, Any] = {
            "task_id": str(task_id),
            "subtask_id": str(subtask_id) if subtask_id is not None else None,
            "step": step.model_dump(mode="json", exclude_none=True),
        }
        async with session.begin_nested():
            await write_outbox_event(
                session=session,
                workspace_id=workspace_id,
                task_id=task_id,
                event_type=TaskEventType.TASK_PROGRESS,
                payload=payload,
            )
    except Exception as error:
        logger.warning(
            "task_progress_step_not_queued step=%s error=%s", step_id, type(error).__name__
        )
