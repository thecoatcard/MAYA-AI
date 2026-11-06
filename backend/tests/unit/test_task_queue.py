"""Unit tests for SubtaskQueue and SubtaskJob (Decision D8)."""

from uuid import uuid4

import pytest

from app.tasks.queue import SubtaskJob, SubtaskQueue


@pytest.mark.asyncio
async def test_subtask_job_serialization() -> None:
    ws_id = uuid4()
    u_id = uuid4()
    t_id = uuid4()
    st_id = uuid4()

    job = SubtaskJob(
        workspace_id=ws_id,
        user_id=u_id,
        task_id=t_id,
        subtask_id=st_id,
        assigned_agent="coding",
        timeout_seconds=120,
    )

    data = job.to_dict()
    assert data["workspace_id"] == str(ws_id)
    assert data["user_id"] == str(u_id)
    assert data["task_id"] == str(t_id)
    assert data["subtask_id"] == str(st_id)
    assert data["assigned_agent"] == "coding"
    assert data["timeout_seconds"] == 120

    deserialized = SubtaskJob.from_dict(data)
    assert deserialized == job


@pytest.mark.asyncio
async def test_subtask_queue_enqueue_dequeue() -> None:
    queue = SubtaskQueue()
    assert queue.is_empty
    assert queue.qsize() == 0

    job1 = SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="research",
    )
    job2 = SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="document",
    )

    await queue.enqueue(job1)
    await queue.enqueue(job2)
    assert queue.qsize() == 2
    assert not queue.is_empty

    popped1 = await queue.dequeue()
    assert popped1 == job1
    queue.task_done()

    popped2 = await queue.dequeue()
    assert popped2 == job2
    queue.task_done()

    assert queue.is_empty


@pytest.mark.asyncio
async def test_subtask_queue_dequeue_timeout() -> None:
    queue = SubtaskQueue()
    popped = await queue.dequeue(timeout_seconds=0.05)
    assert popped is None
