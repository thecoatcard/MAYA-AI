"""Several tasks side by side: independence, exactly-once claims and rate-limit isolation.

Owner report: "one task should not fail because a new task arrived". The root causes found were
a single in-process worker (a second task waited behind, and was interleaved after, the whole of
the first), a first provider quota error failing a task with no retry, and parallel siblings
being unable to see each other's completion. These tests run the real runtime against
PostgreSQL as the restricted ``maya_app`` role with slow fake agents (no Gemini).
"""

from __future__ import annotations

import asyncio
import time
from collections import Counter
from typing import Any
from uuid import UUID, uuid4

import pytest
from google.genai import errors as genai_errors
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import PlanStatus, Subtask, SubtaskAgent, SubtaskStatus, Task
from app.db.tenant import tenant_session
from app.tasks.idempotency import TaskIdempotencyService
from app.tasks.outbox import write_outbox_event
from app.tasks.queue import SubtaskJob, SubtaskQueue
from app.tasks.runtime import TaskRuntime
from app.tasks.worker import (
    RATE_LIMITED_MESSAGE,
    NonRetryableSubtaskError,
    SubtaskWorker,
    resolve_subtask_uuid,
)
from tests.support.postgres import SeededTenant

pytestmark = pytest.mark.postgres


class _Publisher:
    async def publish(self, event: Any) -> bool:
        return True


def _runtime(engine: AsyncEngine, handler: Any, *, concurrency: int) -> TaskRuntime:
    queue = SubtaskQueue()
    return TaskRuntime(
        engine=engine,
        queue=queue,
        publisher=_Publisher(),  # type: ignore[arg-type]
        worker=SubtaskWorker(queue=queue, jwt_signing_key="k" * 32),
        agent_handler=handler,
        concurrency=concurrency,
    )


async def _seed(
    engine: AsyncEngine,
    tenant: SeededTenant,
    shape: dict[str, list[str]],
    *,
    status: str = "executing",
) -> tuple[Task, dict[str, Subtask]]:
    """Commit a task whose subtasks follow ``shape`` (name -> names it depends on)."""
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        task, _ = await TaskIdempotencyService.get_or_create_task(
            session=session,
            workspace_id=tenant.workspace_id,
            user_id=tenant.user_id,
            idempotency_key=f"cc-{uuid4().hex}",
            user_instruction="Run side by side",
        )
        task.status = status
        task.plan_status = PlanStatus.EXECUTING
        subs: dict[str, Subtask] = {}
        for name, deps in shape.items():
            sub = Subtask(
                id=resolve_subtask_uuid(task.id, name),
                task_id=task.id,
                workspace_id=tenant.workspace_id,
                assigned_agent=SubtaskAgent.RESEARCH,
                description=name,
                dependencies=[str(resolve_subtask_uuid(task.id, d)) for d in deps],
                status=SubtaskStatus.PENDING,
                input_data={},
            )
            session.add(sub)
            subs[name] = sub
        await write_outbox_event(
            session, tenant.workspace_id, task.id, "task_plan_created", {"task_id": str(task.id)}
        )
        return task, subs


def _job(tenant: SeededTenant, task: Task, sub: Subtask, **kwargs: Any) -> SubtaskJob:
    return SubtaskJob(
        workspace_id=tenant.workspace_id,
        user_id=tenant.user_id,
        task_id=task.id,
        subtask_id=sub.id,
        assigned_agent="research",
        **kwargs,
    )


async def _state(
    engine: AsyncEngine, tenant: SeededTenant, task_id: UUID
) -> tuple[Task, dict[UUID, Subtask]]:
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        subs = (await session.execute(select(Subtask).where(Subtask.task_id == task_id))).scalars()
        return task, {s.id: s for s in subs}


async def _wait_for(check: Any, *, timeout: float = 15.0) -> None:  # noqa: ASYNC109
    try:
        async with asyncio.timeout(timeout):
            while not await check():  # noqa: ASYNC110
                await asyncio.sleep(0.05)
    except TimeoutError:
        raise AssertionError("condition not reached in time") from None


async def _wait_status(
    engine: AsyncEngine, tenant: SeededTenant, task_id: UUID, expected: str
) -> None:
    async def check() -> bool:
        return (await _state(engine, tenant, task_id))[0].status == expected

    await _wait_for(check)


class _Probe:
    """A slow fake agent that records overlap, per-subtask call counts and timing."""

    def __init__(self, delay: float = 0.3) -> None:
        self.delay = delay
        self.calls: Counter[UUID] = Counter()
        self.running = 0
        self.max_running = 0
        self.started: dict[UUID, float] = {}
        self.finished: dict[UUID, float] = {}

    async def __call__(self, job: SubtaskJob, data: dict[str, Any]) -> dict[str, Any]:
        self.calls[job.subtask_id] += 1
        self.started[job.subtask_id] = time.monotonic()
        self.running += 1
        self.max_running = max(self.max_running, self.running)
        try:
            await asyncio.sleep(self.delay)
        finally:
            self.running -= 1
        self.finished[job.subtask_id] = time.monotonic()
        return {"result": {"ok": True}, "proof": {"status": "unverified"}}


@pytest.mark.asyncio
async def test_root_cause_one_worker_makes_a_new_task_wait_behind_the_running_one(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    """Regression for the owner report. With one worker task B cannot start until A's step ends
    (head-of-line blocking); with the pool B starts beside A and neither is disturbed."""
    tenant, _ = fresh_tenants

    async def run(concurrency: int) -> tuple[_Probe, Task, Task, dict[str, Subtask]]:
        a, a_subs = await _seed(app_engine, tenant, {"a1": []})
        b, b_subs = await _seed(app_engine, tenant, {"b1": []})
        probe = _Probe(delay=0.4)
        runtime = _runtime(app_engine, probe, concurrency=concurrency)
        await runtime.start(reconcile=False)
        try:
            await runtime.queue.enqueue(_job(tenant, a, a_subs["a1"]))
            await asyncio.sleep(0.1)  # A is running when B arrives
            await runtime.queue.enqueue(_job(tenant, b, b_subs["b1"]))
            await _wait_status(app_engine, tenant, a.id, "completed")
            await _wait_status(app_engine, tenant, b.id, "completed")
        finally:
            await runtime.stop()
        return probe, a, b, {**a_subs, **b_subs}

    probe, a, b, subs = await run(concurrency=1)
    assert probe.max_running == 1
    assert probe.started[subs["b1"].id] >= probe.finished[subs["a1"].id]  # B waited behind A

    probe, a, b, subs = await run(concurrency=3)
    assert probe.max_running == 2
    assert probe.started[subs["b1"].id] < probe.finished[subs["a1"].id]  # B ran beside A
    for task in (a, b):
        assert (await _state(app_engine, tenant, task.id))[0].status == "completed"


@pytest.mark.asyncio
async def test_every_subtask_is_claimed_exactly_once_under_parallel_workers(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    shape = {"s1": [], "s2": ["s1"], "s3": ["s1"], "s4": ["s2", "s3"]}  # fork then join
    tasks = [await _seed(app_engine, tenant, shape) for _ in range(2)]
    probe = _Probe(delay=0.15)
    runtime = _runtime(app_engine, probe, concurrency=4)
    await runtime.start(reconcile=False)
    try:
        for task, subs in tasks:
            first = _job(tenant, task, subs["s1"])
            await runtime.queue.enqueue(first)
            await runtime.queue.enqueue(_job(tenant, task, subs["s1"]))  # duplicate job
        for task, _ in tasks:
            await _wait_status(app_engine, tenant, task.id, "completed")
    finally:
        await runtime.stop()

    for task, subs in tasks:
        for sub in subs.values():
            assert probe.calls[sub.id] == 1, "a subtask ran more than once"
        _, by_id = await _state(app_engine, tenant, task.id)
        assert all(s.status == SubtaskStatus.COMPLETED for s in by_id.values())
        # The join never started before both of its parents finished.
        assert probe.started[subs["s4"].id] >= probe.finished[subs["s2"].id]
        assert probe.started[subs["s4"].id] >= probe.finished[subs["s3"].id]
    assert probe.max_running >= 2  # tasks (and the fork) really ran side by side
    assert probe.max_running <= 4  # and never beyond the pool size


@pytest.mark.asyncio
async def test_one_task_failing_leaves_the_other_untouched(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    bad, bad_subs = await _seed(app_engine, tenant, {"x1": []})
    good, good_subs = await _seed(app_engine, tenant, {"g1": [], "g2": ["g1"]})
    probe = _Probe(delay=0.2)

    async def handler(job: SubtaskJob, data: dict[str, Any]) -> dict[str, Any]:
        if job.task_id == bad.id:
            raise NonRetryableSubtaskError("this task cannot run")
        return await probe(job, data)

    runtime = _runtime(app_engine, handler, concurrency=3)
    await runtime.start(reconcile=False)
    try:
        await runtime.queue.enqueue(_job(tenant, bad, bad_subs["x1"]))
        await runtime.queue.enqueue(_job(tenant, good, good_subs["g1"]))
        await _wait_status(app_engine, tenant, good.id, "completed")
        await _wait_status(app_engine, tenant, bad.id, "failed")
    finally:
        await runtime.stop()
    bad_task, bad_by_id = await _state(app_engine, tenant, bad.id)
    assert "this task cannot run" in (bad_by_id[bad_subs["x1"].id].error_message or "")
    _, good_by_id = await _state(app_engine, tenant, good.id)
    assert all(s.status == SubtaskStatus.COMPLETED for s in good_by_id.values())
    assert all(s.error_message is None for s in good_by_id.values())


@pytest.mark.asyncio
async def test_aborting_one_task_does_not_touch_the_other(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    cancelled, c_subs = await _seed(app_engine, tenant, {"c1": []}, status="cancelled")
    alive, a_subs = await _seed(app_engine, tenant, {"a1": []})
    probe = _Probe(delay=0.1)
    runtime = _runtime(app_engine, probe, concurrency=2)
    await runtime.start(reconcile=False)
    try:
        await runtime.queue.enqueue(_job(tenant, cancelled, c_subs["c1"]))
        await runtime.queue.enqueue(_job(tenant, alive, a_subs["a1"]))
        await _wait_status(app_engine, tenant, alive.id, "completed")
    finally:
        await runtime.stop()
    assert probe.calls[c_subs["c1"].id] == 0
    task, by_id = await _state(app_engine, tenant, cancelled.id)
    assert task.status == "cancelled"
    assert by_id[c_subs["c1"].id].status == SubtaskStatus.PENDING


@pytest.mark.asyncio
async def test_a_timeout_in_one_subtask_does_not_time_out_the_ones_waiting(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    slow, slow_subs = await _seed(app_engine, tenant, {"s1": []})
    quick, quick_subs = await _seed(app_engine, tenant, {"q1": []})
    probe = _Probe(delay=0.1)

    async def handler(job: SubtaskJob, data: dict[str, Any]) -> dict[str, Any]:
        if job.task_id == slow.id:
            await asyncio.sleep(30)
        return await probe(job, data)

    runtime = _runtime(app_engine, handler, concurrency=1)  # B waits behind A's timeout
    await runtime.start(reconcile=False)
    try:
        await runtime.queue.enqueue(_job(tenant, slow, slow_subs["s1"], timeout_seconds=1))
        await runtime.queue.enqueue(_job(tenant, quick, quick_subs["q1"]))
        await _wait_status(app_engine, tenant, quick.id, "completed")
    finally:
        for pending in list(runtime._delayed):
            pending.cancel()
        await runtime.stop()
    _, by_id = await _state(app_engine, tenant, slow.id)
    assert "timed out" in (by_id[slow_subs["s1"].id].error_message or "")
    assert (await _state(app_engine, tenant, quick.id))[0].status == "completed"


def _quota_error() -> Exception:
    return genai_errors.ClientError(429, {"error": {"code": 429, "message": "quota"}})


@pytest.mark.asyncio
async def test_rate_limit_retries_with_a_pause_and_does_not_fail_the_sibling_task(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    limited, l_subs = await _seed(app_engine, tenant, {"l1": []})
    sibling, s_subs = await _seed(app_engine, tenant, {"s1": []})
    probe = _Probe(delay=0.1)

    async def handler(job: SubtaskJob, data: dict[str, Any]) -> dict[str, Any]:
        if job.task_id == limited.id:
            try:
                raise _quota_error()
            except Exception as err:  # the gateway wraps an exhausted chain like this
                raise RuntimeError("fallback chain exhausted") from err
        return await probe(job, data)

    runtime = _runtime(app_engine, handler, concurrency=2)
    await runtime.start(reconcile=False)
    try:
        await runtime.queue.enqueue(_job(tenant, limited, l_subs["l1"]))
        await runtime.queue.enqueue(_job(tenant, sibling, s_subs["s1"]))
        await _wait_status(app_engine, tenant, sibling.id, "completed")

        async def retried() -> bool:
            _, by_id = await _state(app_engine, tenant, limited.id)
            return by_id[l_subs["l1"].id].retry_count == 1

        await _wait_for(retried)
    finally:
        delayed = list(runtime._delayed)
        await runtime.stop()
    task, by_id = await _state(app_engine, tenant, limited.id)
    sub = by_id[l_subs["l1"].id]
    # First quota error: waits and retries (pending, long pause), the task is NOT failed.
    assert sub.status == SubtaskStatus.PENDING
    assert task.status == "executing"
    assert delayed, "a delayed retry must be scheduled"

    # After the bounded retries it fails with an honest, actionable message.
    runtime = _runtime(app_engine, handler, concurrency=1)
    job = _job(tenant, limited, l_subs["l1"])
    await runtime.process_job(job)
    await runtime.process_job(job)
    for pending in list(runtime._delayed):
        pending.cancel()
    task, by_id = await _state(app_engine, tenant, limited.id)
    sub = by_id[l_subs["l1"].id]
    assert sub.status == SubtaskStatus.FAILED
    assert sub.error_message == RATE_LIMITED_MESSAGE
    assert task.status == "failed"
    assert (await _state(app_engine, tenant, sibling.id))[0].status == "completed"


@pytest.mark.asyncio
async def test_two_workspaces_both_progress_and_stop_cleans_up_workers(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    first, second = fresh_tenants
    one, one_subs = await _seed(app_engine, first, {"a": [], "b": ["a"]})
    two, two_subs = await _seed(app_engine, second, {"a": []})
    probe = _Probe(delay=0.1)
    runtime = _runtime(app_engine, probe, concurrency=2)
    await runtime.start(reconcile=False)
    assert len(runtime._tasks) == 4  # two workers, the outbox loop and the recovery sweeper
    try:
        await runtime.queue.enqueue(_job(first, one, one_subs["a"]))
        await runtime.queue.enqueue(_job(second, two, two_subs["a"]))
        await _wait_status(app_engine, first, one.id, "completed")
        await _wait_status(app_engine, second, two.id, "completed")
    finally:
        await runtime.stop()
    assert runtime._tasks == []


def test_worker_concurrency_is_bounded() -> None:
    queue = SubtaskQueue()

    def make(value: int) -> TaskRuntime:
        return TaskRuntime(
            engine=None,  # type: ignore[arg-type]
            queue=queue,
            publisher=_Publisher(),  # type: ignore[arg-type]
            worker=SubtaskWorker(queue=queue),
            agent_handler=None,  # type: ignore[arg-type]
            concurrency=value,
        )

    assert make(0).concurrency == 1
    assert make(1).concurrency == 1
    assert make(5).concurrency == 5
    assert make(99).concurrency == 8
    assert TaskRuntime.__init__.__kwdefaults__["concurrency"] == 3  # type: ignore[index]
