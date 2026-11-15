"""Task runtime against real PostgreSQL as the restricted ``maya_app`` role (FORCE RLS).

The earlier mocked tests could not notice that background loops without a tenant context see
zero rows. These tests run the worker, outbox relay and reconciler through ``TaskRuntime`` on
the non-owner role, so a missing tenant context fails here.
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import PlanStatus, Subtask, SubtaskAgent, SubtaskStatus, Task, TaskOutboxEvent
from app.db.tenant import tenant_session
from app.tasks.idempotency import TaskIdempotencyService
from app.tasks.outbox import write_outbox_event
from app.tasks.queue import SubtaskJob, SubtaskQueue
from app.tasks.runtime import TaskRuntime
from app.tasks.worker import SubtaskWorker, resolve_subtask_uuid
from tests.support.postgres import SeededTenant

pytestmark = pytest.mark.postgres


class _RecordingPublisher:
    def __init__(self) -> None:
        self.events: list[Any] = []

    async def publish(self, event: Any) -> bool:
        self.events.append(event)
        return True


async def _ok_handler(job: SubtaskJob, input_data: dict[str, Any]) -> dict[str, Any]:
    return {"result": {"content": "done"}, "proof": {"status": "unverified"}}


async def _boom_handler(job: SubtaskJob, input_data: dict[str, Any]) -> dict[str, Any]:
    raise RuntimeError("gemini exploded")


def _runtime(
    engine: AsyncEngine, handler: Any
) -> tuple[TaskRuntime, SubtaskQueue, _RecordingPublisher]:
    queue = SubtaskQueue()
    publisher = _RecordingPublisher()
    runtime = TaskRuntime(
        engine=engine,
        queue=queue,
        publisher=publisher,  # type: ignore[arg-type]
        worker=SubtaskWorker(queue=queue, jwt_signing_key="k" * 32),
        agent_handler=handler,
    )
    return runtime, queue, publisher


async def _seed_task(
    engine: AsyncEngine,
    tenant: SeededTenant,
    *,
    steps: int = 1,
    in_progress: bool = False,
) -> tuple[Task, list[Subtask]]:
    """Commit a task with a linear chain of ``steps`` subtasks (first one runnable)."""
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        task, _ = await TaskIdempotencyService.get_or_create_task(
            session=session,
            workspace_id=tenant.workspace_id,
            user_id=tenant.user_id,
            idempotency_key=f"rt-{uuid4().hex}",
            user_instruction="Summarise Java notes",
        )
        task.status = "executing"
        task.plan_status = PlanStatus.EXECUTING
        subtasks: list[Subtask] = []
        previous: str | None = None
        for index in range(steps):
            sub = Subtask(
                id=resolve_subtask_uuid(task.id, f"step_{index}"),
                task_id=task.id,
                workspace_id=tenant.workspace_id,
                assigned_agent=SubtaskAgent.RESEARCH,
                description=f"Step {index}",
                dependencies=[previous] if previous else [],
                status=SubtaskStatus.IN_PROGRESS
                if (in_progress and index == 0)
                else SubtaskStatus.PENDING,
                input_data={},
            )
            session.add(sub)
            subtasks.append(sub)
            previous = str(sub.id)
        await write_outbox_event(
            session, tenant.workspace_id, task.id, "task_plan_created", {"task_id": str(task.id)}
        )
        return task, subtasks


def _job(tenant: SeededTenant, task: Task, sub: Subtask) -> SubtaskJob:
    return SubtaskJob(
        workspace_id=tenant.workspace_id,
        user_id=tenant.user_id,
        task_id=task.id,
        subtask_id=sub.id,
        assigned_agent="research",
    )


async def _reload(
    engine: AsyncEngine, tenant: SeededTenant, task_id: Any
) -> tuple[Task, dict[Any, Subtask]]:
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        subs = (await session.execute(select(Subtask).where(Subtask.task_id == task_id))).scalars()
        return task, {s.id: s for s in subs}


@pytest.mark.asyncio
async def test_worker_completes_subtask_and_relays_events_under_rls(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    task, subs = await _seed_task(app_engine, tenant, steps=2)
    runtime, queue, publisher = _runtime(app_engine, _ok_handler)

    # Without a tenant context the restricted role sees nothing: this is the original bug.
    async with app_engine.connect() as conn:
        assert (await conn.execute(text("SELECT count(*) FROM subtasks"))).scalar_one() == 0

    await runtime.process_job(_job(tenant, task, subs[0]))

    reloaded_task, by_id = await _reload(app_engine, tenant, task.id)
    first = by_id[subs[0].id]
    assert first.status == SubtaskStatus.COMPLETED
    assert first.output_result == {"content": "done"}
    assert first.verification_proof == {"status": "unverified"}
    assert reloaded_task.status == "executing"
    # The dependent became runnable and was queued only after the commit.
    assert queue.qsize() == 1
    nxt = await queue.dequeue(timeout_seconds=1.0)
    assert nxt is not None and nxt.subtask_id == subs[1].id
    await runtime.process_job(nxt)
    final_task, _ = await _reload(app_engine, tenant, task.id)
    assert final_task.status == "completed"

    dispatched = await runtime.relay_outbox_once()
    assert dispatched >= 3
    assert {e.event_type.value for e in publisher.events} >= {
        "task_plan_created",
        "subtask_status_changed",
        "task_completed",
    }
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        pending = (
            await session.execute(
                select(TaskOutboxEvent).where(TaskOutboxEvent.status == "pending")
            )
        ).scalars()
        assert list(pending) == []


@pytest.mark.asyncio
async def test_handler_failure_retries_then_fails_with_real_error(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    task, subs = await _seed_task(app_engine, tenant)
    runtime, queue, _ = _runtime(app_engine, _boom_handler)
    job = _job(tenant, task, subs[0])

    await runtime.process_job(job)
    _, by_id = await _reload(app_engine, tenant, task.id)
    sub = by_id[subs[0].id]
    assert sub.status == SubtaskStatus.PENDING  # never stuck in_progress
    assert sub.retry_count == 1
    assert "gemini exploded" in (sub.error_message or "")
    for pending in list(runtime._delayed):
        pending.cancel()

    await runtime.process_job(job)
    await runtime.process_job(job)
    failed_task, by_id = await _reload(app_engine, tenant, task.id)
    sub = by_id[subs[0].id]
    assert sub.status == SubtaskStatus.FAILED
    assert sub.retry_count == 3
    assert "gemini exploded" in (sub.error_message or "")
    assert failed_task.status == "failed"
    for pending in list(runtime._delayed):
        pending.cancel()
    # A finished subtask is not run again by a duplicate job.
    await runtime.process_job(job)
    assert (await _reload(app_engine, tenant, task.id))[1][subs[0].id].retry_count == 3


@pytest.mark.asyncio
async def test_reconciler_requeues_open_work_after_restart(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, other = fresh_tenants
    ready, ready_subs = await _seed_task(app_engine, tenant, steps=2)
    interrupted, interrupted_subs = await _seed_task(app_engine, tenant, in_progress=True)
    await _seed_task(app_engine, other)  # other tenant is not registered; must not be touched

    # Simulate a restart: a fresh runtime with an empty queue.
    # Process memory is empty: workspaces come from public.list_workspaces_with_open_work().
    runtime, queue, _ = _runtime(app_engine, _ok_handler)
    assert tenant.workspace_id in await runtime.list_workspaces_with_open_work()

    enqueued = await runtime.reconcile()
    queued = {(await queue.dequeue(1.0)).subtask_id for _ in range(enqueued)}  # type: ignore[union-attr]
    # Fixture-seeded rows in the workspace may also be runnable; ours must all be present and
    # the step that still waits on its dependency must not be.
    assert {ready_subs[0].id, interrupted_subs[0].id} <= queued
    assert ready_subs[1].id not in queued

    _, by_id = await _reload(app_engine, tenant, interrupted.id)
    assert by_id[interrupted_subs[0].id].status == SubtaskStatus.PENDING
    assert by_id[interrupted_subs[0].id].retry_count == 1
    _, by_id = await _reload(app_engine, tenant, ready.id)
    assert by_id[ready_subs[1].id].status == SubtaskStatus.PENDING


@pytest.mark.asyncio
async def test_missing_executor_fails_without_fabricating_success(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    task, subs = await _seed_task(app_engine, tenant)
    runtime, _, _ = _runtime(app_engine, _ok_handler)
    deferred: list[SubtaskJob] = []
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        status = await runtime.worker.execute_job(
            session=session, job=_job(tenant, task, subs[0]), deferred_jobs=deferred
        )
    assert status == SubtaskStatus.FAILED
    _, by_id = await _reload(app_engine, tenant, task.id)
    assert "No executor is configured" in (by_id[subs[0].id].error_message or "")
    assert by_id[subs[0].id].output_result is None


def test_submit_enqueues_only_after_commit_and_sse_404s_for_unknown_task(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    from unittest.mock import AsyncMock, patch

    from fastapi.testclient import TestClient
    from sqlalchemy.ext.asyncio import create_async_engine

    from app.api.v1.tasks import get_subtask_queue
    from app.auth.dependencies import TenantDatabase, get_principal, get_tenant_database
    from app.auth.models import Principal
    from app.main import create_app
    from tests.support.settings import make_settings

    tenant, _ = fresh_tenants
    principal = Principal(
        user_id=tenant.user_id, workspace_id=tenant.workspace_id, role="member", session_id=uuid4()
    )
    seen_at_enqueue: list[bool] = []

    class _VisibilityQueue(SubtaskQueue):
        async def enqueue(self, job: SubtaskJob) -> None:
            # A separate connection only sees committed rows (as a worker would).
            engine = create_async_engine(app_engine.url)
            try:
                async with tenant_session(engine, job.workspace_id, job.user_id) as session:
                    found = (
                        await session.execute(select(Subtask).where(Subtask.id == job.subtask_id))
                    ).first()
                seen_at_enqueue.append(found is not None)
            finally:
                await engine.dispose()
            await super().enqueue(job)

    app = create_app(make_settings())
    queue = _VisibilityQueue()
    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[get_subtask_queue] = lambda: queue
    app.dependency_overrides[get_tenant_database] = lambda: TenantDatabase(
        engine=app_engine, workspace_id=tenant.workspace_id
    )
    with patch("app.main.Resources") as factory:
        resources = factory.return_value
        resources.check = AsyncMock(return_value=None)
        resources.close = AsyncMock()
        resources.verify_runtime_role = AsyncMock(return_value=None)
        resources.redis = None
        resources.model_gateway = None
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer test-token"}
            resp = client.post(
                "/api/v1/tasks",
                json={"instruction": "Research Java", "idempotency_key": f"k-{uuid4().hex}"},
                headers=headers,
            )
            assert resp.status_code == 201, resp.text
            assert seen_at_enqueue == [True]
            assert client.get(f"/api/v1/tasks/{uuid4()}/events", headers=headers).status_code == 404


@pytest.mark.asyncio
async def test_task_run_writes_audit_rows_and_chain_stays_valid(
    app_engine: AsyncEngine,
    admin_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
) -> None:
    from app.db.models import AuditLog
    from app.services.audit_service import verify_chain

    tenant, _ = fresh_tenants
    # The shared fixture seeds one unchained placeholder audit row; start from an empty chain.
    async with admin_engine.begin() as conn:
        await conn.execute(
            text("DELETE FROM audit_log WHERE workspace_id = :w"), {"w": tenant.workspace_id}
        )
    task, subs = await _seed_task(app_engine, tenant)
    runtime, _, _ = _runtime(app_engine, _ok_handler)
    await runtime.process_job(_job(tenant, task, subs[0]))

    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        rows = (
            await session.execute(select(AuditLog).where(AuditLog.task_id == task.id))
        ).scalars()
        actions = {r.tool_name for r in rows}
        assert {"task.started", "task.completed"} <= actions
        result = await verify_chain(session, tenant.workspace_id)
        assert result.valid


@pytest.mark.asyncio
async def test_failed_task_audits_failure_and_tool_execution(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    from app.db.models import AuditLog

    tenant, _ = fresh_tenants
    task, subs = await _seed_task(app_engine, tenant)
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        sub = (await session.execute(select(Subtask).where(Subtask.id == subs[0].id))).scalar_one()
        sub.input_data = {"tool": "web_search", "server_name": "maya-tools"}
        await session.flush()
    runtime, _, _ = _runtime(app_engine, _boom_handler)
    job = _job(tenant, task, subs[0])
    for _ in range(3):
        await runtime.process_job(job)
    for pending in list(runtime._delayed):
        pending.cancel()

    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        rows = list(
            (await session.execute(select(AuditLog).where(AuditLog.task_id == task.id))).scalars()
        )
    assert "task.failed" in {r.tool_name for r in rows}
    tool_rows = [r for r in rows if r.server_name == "maya-tools"]
    assert tool_rows and all(r.status == "failed" for r in tool_rows)
    assert tool_rows[0].duration_ms is not None


def test_approval_decisions_are_audited_and_voice_is_denied(
    app_engine: AsyncEngine,
    admin_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
) -> None:
    import asyncio
    from datetime import UTC, datetime, timedelta
    from unittest.mock import AsyncMock, patch

    from fastapi.testclient import TestClient

    from app.auth.dependencies import TenantDatabase, get_principal, get_tenant_database
    from app.auth.models import Principal
    from app.db.models import AuditLog
    from app.db.repositories.approvals import ApprovalRepository
    from app.db.tenant import tenant_transaction
    from app.main import create_app
    from app.services.audit_service import verify_chain
    from tests.support.settings import make_settings

    tenant, _ = fresh_tenants
    principal = Principal(
        user_id=tenant.user_id, workspace_id=tenant.workspace_id, role="member", session_id=uuid4()
    )

    async def seed() -> list[Any]:
        async with admin_engine.begin() as conn:
            await conn.execute(
                text("DELETE FROM audit_log WHERE workspace_id = :w"), {"w": tenant.workspace_id}
            )
        seeded_task, _ = await _seed_task(app_engine, tenant)
        ids = []
        for index in range(2):
            async with tenant_transaction(app_engine, tenant.workspace_id) as conn:
                repo = ApprovalRepository(conn, tenant.workspace_id, tenant.user_id)
                created = await repo.create_request(
                    task_id=seeded_task.id,
                    subtask_id=None,
                    tool_name="gmail_send",
                    tool_args={"to": "a@example.com"},
                    payload_hash=f"{index:064x}",
                    tier="APPROVAL_REQUIRED",
                    action_description="Send mail",
                    payload_diff=None,
                    expires_at=datetime.now(UTC) + timedelta(days=1),
                    idempotency_key=f"idem-{uuid4().hex}",
                )
                ids.append(getattr(created, "id", created))
        return ids

    first, second = asyncio.run(seed())

    app = create_app(make_settings())
    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[get_tenant_database] = lambda: TenantDatabase(
        engine=app_engine, workspace_id=tenant.workspace_id
    )
    with patch("app.main.Resources") as factory:
        resources = factory.return_value
        resources.check = AsyncMock(return_value=None)
        resources.close = AsyncMock()
        resources.verify_runtime_role = AsyncMock(return_value=None)
        with TestClient(app) as client:
            headers = {"Authorization": "Bearer test-token"}
            voice = client.post(
                f"/api/v1/approvals/{first}/decide",
                json={"action": "approve"},
                headers={**headers, "X-MAYA-Channel": "voice"},
            )
            assert voice.status_code == 403
            reject = client.post(
                f"/api/v1/approvals/{first}/decide", json={"action": "reject"}, headers=headers
            )
            assert reject.status_code == 200, reject.text
            approve = client.post(
                f"/api/v1/approvals/{second}/decide", json={"action": "approve"}, headers=headers
            )
            assert approve.status_code == 200, approve.text

    async def check() -> None:
        async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
            rows = list((await session.execute(select(AuditLog).order_by(AuditLog.id))).scalars())
            assert [(r.tool_name, r.status) for r in rows] == [
                ("approval.denied", "denied"),
                ("approval.rejected", "rejected"),
                ("approval.approved", "success"),
            ]
            assert "eyJ" not in str([r.input_redacted for r in rows])
            assert (await verify_chain(session, tenant.workspace_id)).valid

    asyncio.run(check())
