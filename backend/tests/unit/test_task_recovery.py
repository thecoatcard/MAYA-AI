"""Recovery helpers of the task runtime: where-an-error-was-raised, queue lookup, plain failure.

The database behaviour (sweep, retry then fail plainly, the Notion chain) is covered on real
PostgreSQL in ``tests/postgres/test_book_notion_chain_postgres.py``.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

from app.db.models import SubtaskStatus
from app.tasks.queue import SubtaskJob, SubtaskQueue
from app.tasks.runtime import error_where
from app.tasks.worker import SubtaskWorker


def _boom() -> None:
    raise ValueError("secret-value-123")


def test_error_where_names_file_line_and_function_but_never_values() -> None:
    try:
        _boom()
    except ValueError as error:
        where = error_where(error)
    assert "test_task_recovery.py:" in where and ":_boom" in where
    assert "secret-value-123" not in where


async def test_the_queue_knows_which_subtasks_wait_in_it() -> None:
    queue = SubtaskQueue()
    job = SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="pm",
    )
    assert queue.contains(job.subtask_id) is False
    await queue.enqueue(job)
    assert queue.contains(job.subtask_id) is True
    assert await queue.dequeue(timeout_seconds=1.0) is job
    assert queue.contains(job.subtask_id) is False


def test_only_the_worker_written_recovery_mark_makes_a_side_effect_step_tolerable() -> None:
    worker = SubtaskWorker()
    base: dict[str, Any] = {
        "assigned_agent": "pm",
        "status": SubtaskStatus.FAILED,
        "verification_proof": None,
    }
    planner_claim = SimpleNamespace(
        **base, input_data={"tool": "notion_create_page_from_job", "critical": False}
    )
    assert worker._is_critical(planner_claim) is True, "a planner cannot waive a gated step"  # type: ignore[arg-type]
    recovered = SimpleNamespace(
        **{**base, "verification_proof": {"recovered_failure": True}},
        input_data={"tool": "notion_create_page_from_job"},
    )
    assert worker._is_critical(recovered) is False  # type: ignore[arg-type]
    assert worker._satisfied(recovered) is True  # type: ignore[arg-type]
