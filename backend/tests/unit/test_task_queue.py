"""Unit tests for SubtaskQueue and SubtaskJob (Decision D8)."""

from uuid import UUID, uuid4

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


def _job(workspace_id: UUID, task_id: UUID) -> SubtaskJob:
    return SubtaskJob(
        workspace_id=workspace_id,
        user_id=uuid4(),
        task_id=task_id,
        subtask_id=uuid4(),
        assigned_agent="research",
    )


@pytest.mark.asyncio
async def test_queue_serves_workspaces_fairly_one_busy_workspace_cannot_starve_another() -> None:
    queue = SubtaskQueue()
    busy, quiet = uuid4(), uuid4()
    busy_jobs = [_job(busy, uuid4()) for _ in range(6)]
    quiet_job = _job(quiet, uuid4())
    for job in busy_jobs:
        await queue.enqueue(job)
    await queue.enqueue(quiet_job)  # arrives last, behind six jobs of the busy workspace

    order = [await queue.dequeue(timeout_seconds=1) for _ in range(3)]
    for picked in order:
        assert picked is not None
        queue.task_done(picked)
    # The quiet workspace is served within the first two picks, not after all six.
    assert quiet_job in order[:2]


@pytest.mark.asyncio
async def test_queue_prefers_the_workspace_with_fewer_running_jobs() -> None:
    queue = SubtaskQueue()
    busy, quiet = uuid4(), uuid4()
    running = _job(busy, uuid4())
    await queue.enqueue(running)
    assert await queue.dequeue(timeout_seconds=1) is running  # busy now has one running job
    await queue.enqueue(_job(busy, uuid4()))
    waiting_quiet = _job(quiet, uuid4())
    await queue.enqueue(waiting_quiet)
    assert await queue.dequeue(timeout_seconds=1) is waiting_quiet
    assert queue.running_count(busy) == 1
    queue.task_done(running)
    assert queue.running_count(busy) == 0


@pytest.mark.asyncio
async def test_queue_interleaves_tasks_of_one_workspace_and_keeps_task_order() -> None:
    queue = SubtaskQueue()
    workspace, task_a, task_b = uuid4(), uuid4(), uuid4()
    a1, a2, a3 = (_job(workspace, task_a) for _ in range(3))
    b1 = _job(workspace, task_b)
    for job in (a1, a2, a3, b1):
        await queue.enqueue(job)
    popped = [await queue.dequeue(timeout_seconds=1) for _ in range(4)]
    assert popped == [a1, b1, a2, a3]  # B is not stuck behind A's whole backlog; A stays FIFO
    assert queue.is_empty
