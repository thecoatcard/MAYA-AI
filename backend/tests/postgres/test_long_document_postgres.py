"""The long-document pipeline through the real runtime, worker and PostgreSQL (fake model only).

The owner's request ("notes on java ... pdf of 50 pages") becomes outline -> 9 chapter steps ->
assemble. A fake model writes realistic chapters; the database, the runtime with its bounded
worker pool, the retry logic, the document agent, the browser renderer and the PDF verifier are
real. Skipped without Edge/Chrome (the page count needs a real print).
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any, cast
from uuid import UUID, uuid4

import pytest
from pypdf import PdfReader
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.long_document_plan import build_long_document_plan
from app.agents.registry import AgentFleetRegistry
from app.agents.specialized.document import DocumentAgent
from app.agents.state import SubTask, persisted_input_data
from app.automation.acceptance import extract_acceptance
from app.db.models import (
    FileArtifact,
    PlanStatus,
    Subtask,
    SubtaskAgent,
    SubtaskStatus,
    Task,
    TaskOutboxEvent,
)
from app.db.tenant import tenant_session
from app.gateway.model_gateway import ModelGateway
from app.mcp.permissions import ToolPermissionRegistry
from app.tasks import worker as worker_module
from app.tasks.dispatch import FleetSupport
from app.tasks.executor import execute_subtask_with_agent
from app.tasks.idempotency import TaskIdempotencyService
from app.tasks.pipeline import DRAFT_KEY
from app.tasks.queue import SubtaskJob, SubtaskQueue
from app.tasks.runtime import TaskRuntime
from app.tasks.worker import SubtaskWorker, resolve_subtask_uuid
from tests.support.documents import requires_browser
from tests.support.longdoc import FakeLongDocGateway
from tests.support.postgres import SeededTenant
from tests.support.settings import JWT_KEY

pytestmark = [pytest.mark.postgres, requires_browser, pytest.mark.usefixtures("nine_chapters")]

REQUEST = (
    "make a notes on java from basic to advance make sure pdf is of 50 pages and well "
    "formatted and well documented"
)


def _out(sub: Subtask) -> dict[str, Any]:
    assert sub.output_result is not None
    return sub.output_result


def _proof(sub: Subtask) -> dict[str, Any]:
    assert sub.verification_proof is not None
    return sub.verification_proof


class _Publisher:
    async def publish(self, event: Any) -> bool:
        return True


async def _seed(
    engine: AsyncEngine, tenant: SeededTenant, nodes: list[SubTask]
) -> tuple[Task, dict[str, Subtask]]:
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        task, _ = await TaskIdempotencyService.get_or_create_task(
            session=session,
            workspace_id=tenant.workspace_id,
            user_id=tenant.user_id,
            idempotency_key=f"longdoc-{uuid4().hex}",
            user_instruction=REQUEST,
        )
        task.status = "executing"
        task.plan_status = PlanStatus.EXECUTING
        task.active_plan = [n.model_dump() for n in nodes]
        subs: dict[str, Subtask] = {}
        for node in nodes:
            sub = Subtask(
                id=resolve_subtask_uuid(task.id, node.id),
                task_id=task.id,
                workspace_id=tenant.workspace_id,
                assigned_agent=SubtaskAgent(node.assigned_agent),
                description=node.description,
                dependencies=[str(resolve_subtask_uuid(task.id, d)) for d in node.dependencies],
                status=SubtaskStatus.PENDING,
                input_data=persisted_input_data(node),
            )
            session.add(sub)
            subs[node.id] = sub
        await session.flush()
        return task, subs


async def _state(
    engine: AsyncEngine, tenant: SeededTenant, task_id: UUID
) -> tuple[Task, dict[UUID, Subtask]]:
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        subs = (await session.execute(select(Subtask).where(Subtask.task_id == task_id))).scalars()
        return task, {s.id: s for s in subs}


async def _run(
    engine: AsyncEngine,
    tenant: SeededTenant,
    tmp_path: Path,
    gateway: FakeLongDocGateway,
    *,
    concurrency: int = 3,
) -> tuple[Task, dict[str, Subtask], dict[UUID, Subtask]]:
    registry = ToolPermissionRegistry()
    fleet = AgentFleetRegistry()
    fleet.register_agent(
        "document", DocumentAgent(artifacts_base_dir=tmp_path, permission_registry=registry)
    )
    queue = SubtaskQueue()

    async def handler(job: SubtaskJob, data: dict[str, Any]) -> dict[str, Any]:
        fake_gateway = cast(ModelGateway, gateway)  # test fake standing in for the gateway
        return await execute_subtask_with_agent(job, data, gateway=fake_gateway)

    runtime = TaskRuntime(
        engine=engine,
        queue=queue,
        publisher=_Publisher(),  # type: ignore[arg-type]
        worker=SubtaskWorker(
            queue=queue,
            permission_registry=registry,
            fleet_registry=fleet,
            jwt_signing_key=JWT_KEY,
            fleet_support=FleetSupport(
                gateway=cast(ModelGateway, gateway),
                artifacts_dir=tmp_path,
                expand_concurrency=concurrency,
            ),
        ),
        agent_handler=handler,
        concurrency=concurrency,
    )
    _, nodes, _ = build_long_document_plan(REQUEST, extract_acceptance(REQUEST))
    task, subs = await _seed(engine, tenant, nodes)
    await runtime.start(reconcile=False)
    try:
        await runtime.queue.enqueue(
            SubtaskJob(
                workspace_id=tenant.workspace_id,
                user_id=tenant.user_id,
                task_id=task.id,
                subtask_id=subs["outline"].id,
                assigned_agent="file",
            )
        )
        async with asyncio.timeout(180):
            while (await _state(engine, tenant, task.id))[0].status not in ("completed", "failed"):  # noqa: ASYNC110
                await asyncio.sleep(0.2)
    finally:
        for pending in list(runtime._delayed):
            pending.cancel()
        await runtime.stop()
    done, by_id = await _state(engine, tenant, task.id)
    return done, subs, by_id


@pytest.mark.asyncio
async def test_a_50_page_request_runs_outline_chapters_assemble_and_yields_a_verified_pdf(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
) -> None:
    tenant, _ = fresh_tenants
    gateway = FakeLongDocGateway(chapters=9)
    task, subs, by_id = await _run(app_engine, tenant, tmp_path, gateway)

    errors = {s.description[:40]: s.error_message for s in by_id.values() if s.error_message}
    assert task.status == "completed", errors
    assert not (task.shared_context or {}).get("warnings")  # nothing to apologise for

    # The chapter steps got their real titles once the outline existed.
    titles = {s.description for s in by_id.values()}
    assert "Chapter 3: Control flow and methods" in titles
    plan_titles = {n["description"] for n in task.active_plan}
    assert "Chapter 3: Control flow and methods" in plan_titles
    # Chapters ran in parallel, never more at once than the worker pool.
    assert 1 < gateway.max_active <= 3
    # The heavy draft stays out of the stored plan; the step row keeps it.
    assert all(DRAFT_KEY not in (n.get("output_result") or {}) for n in task.active_plan)
    assert DRAFT_KEY in _out(by_id[subs["chapter_2"].id])

    assemble = by_id[subs["assemble"].id]
    acceptance = _proof(assemble)["report"]["acceptance"]
    assert acceptance["met"] is True and acceptance["requested"] == {"pages": 50}
    assert 45 <= acceptance["actual"]["pages"] <= 63
    assert "Length check passed" in _out(assemble)["summary"]

    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        artifacts = list(
            (await session.execute(select(FileArtifact).where(FileArtifact.task_id == task.id)))
            .scalars()
            .all()
        )
        events = [
            e.payload
            for e in (
                await session.execute(
                    select(TaskOutboxEvent).where(TaskOutboxEvent.task_id == task.id)
                )
            ).scalars()
        ]
    assert len(artifacts) == 1 and artifacts[0].verification_status == "verified"
    assert len(PdfReader(artifacts[0].storage_uri).pages) == acceptance["actual"]["pages"]
    assert all(DRAFT_KEY not in (e.get("output") or {}) for e in events)  # events stay light


@pytest.mark.asyncio
async def test_a_failing_chapter_is_retried_then_reported_missing_without_sinking_the_task(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(worker_module, "RETRY_BACKOFF_BASE_SECONDS", 0.01)
    tenant, _ = fresh_tenants
    gateway = FakeLongDocGateway(chapters=9, always_fail_chapters={4})
    task, subs, by_id = await _run(app_engine, tenant, tmp_path, gateway)

    assert task.status == "completed", {s.description[:30]: s.error_message for s in by_id.values()}
    failed = by_id[subs["chapter_4"].id]
    assert (
        failed.status == SubtaskStatus.FAILED and failed.retry_count >= 2
    )  # retried, then given up
    assert gateway.chapter_attempts[4] >= 2
    # The document was assembled without it, and the task result says which chapter is missing.
    warnings = (task.shared_context or {}).get("warnings") or []
    assert any("Chapter 4" in w and "failed" in w for w in warnings)
    assemble = by_id[subs["assemble"].id]
    proof_warnings = _proof(assemble)["report"]["warnings"]
    assert any("Missing from the document" in w and "Chapter 4" in w for w in proof_warnings)
    path = (await _artifact_paths(app_engine, tenant, task.id))[0]
    text = "\n".join(page.extract_text() or "" for page in PdfReader(path).pages[:3])
    assert "Chapter 3:" in text and "Chapter 4:" not in text
    assert "Chapter 5:" in text


async def _artifact_paths(engine: AsyncEngine, tenant: SeededTenant, task_id: UUID) -> list[str]:
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        rows = (
            await session.execute(select(FileArtifact).where(FileArtifact.task_id == task_id))
        ).scalars()
        return [r.storage_uri for r in rows]
