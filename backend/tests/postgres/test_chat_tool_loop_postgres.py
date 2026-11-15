"""Chat tool loop against real PostgreSQL and Redis (ADR-0026 Phase 1, MAYA_CHAT_TOOL_LOOP on).

start_job goes through the real task submission path and creates a real task row; the next turn's
check_job reads that row's status through TaskJobReader, and the status reaches the model. The
working state (the job id) travels in the first reply's message metadata. Only the Gemini client
is a fake and the subtask queue records instead of running workers.
"""

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text

from app.tasks.queue import SubtaskJob, SubtaskQueue
from tests.postgres import test_chat_api_postgres as base
from tests.postgres.test_chat_api_postgres import (
    Stack,
    events,
    names,
    onboard,
    post,
    redis_url,
)
from tests.support.settings import make_settings

stack = base.stack  # the shared fixture: real PostgreSQL, real Redis, fake Gemini

pytestmark = [pytest.mark.postgres, pytest.mark.redis]


class RecordingQueue(SubtaskQueue):
    """Keeps the jobs it is given; nothing runs, so the task stays in its first state."""

    def __init__(self) -> None:
        super().__init__()
        self.enqueued: list[SubtaskJob] = []

    async def enqueue(self, job: SubtaskJob) -> None:
        self.enqueued.append(job)


@pytest.fixture(autouse=True)
def loop_on(stack: Stack) -> None:
    stack.app.state.settings = make_settings(redis_url=redis_url(), chat_tool_loop=True)
    stack.app.state.resources.subtask_queue = RecordingQueue()


def stream_calls(stack: Stack) -> list[dict[str, Any]]:
    return [c for c in stack.fake.aio.models.calls if c["method"] == "generate_content_stream"]


async def test_start_job_creates_a_real_task_and_check_job_reads_its_status_next_turn(
    stack: Stack,
) -> None:
    alice = await onboard(stack)
    stack.models.queued_stream_scripts = [
        [
            {"text": "On it."},
            {"call": {"name": "start_job", "args": {"instruction": "Write a 300-page book"}}},
        ],
        [{"text": "The book is started in the background."}],
    ]
    first = events(await post(stack, alice, "Write a 300-page book on solar energy"))
    (created,) = [f.data for f in first if f.event == "task.created"]
    job_id = UUID(created["task_id"])
    session_id = first[0].data["session_id"]

    async with stack.admin.connect() as connection:
        stored = (
            await connection.execute(
                text(
                    "SELECT count(*) AS n, coalesce(max(status), max(plan_status::text)) AS s "
                    "FROM tasks WHERE id = :id AND workspace_id = :w"
                ),
                {"id": job_id, "w": alice.workspace_id},
            )
        ).one()
    assert stored.n == 1  # the task row exists: start_job used the real submission path
    db_status = str(stored.s)

    stack.models.queued_stream_scripts = [
        [{"call": {"name": "check_job", "args": {"job_id": str(job_id)}}}],
        [{"text": "It is still running."}],
    ]
    second = events(await post(stack, alice, "How is the book going?", session_id=session_id))
    (finished,) = [f.data for f in second if f.event == "tool.finished"]
    assert finished["name"] == "check_job" and finished["status"] == "ok"
    # The status read from the task row is what the model was told.
    assert db_status in str(stream_calls(stack)[-1]["contents"])
    assert names(second)[-1] == "done"
