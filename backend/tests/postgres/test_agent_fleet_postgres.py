"""Specialist agents + MCP gate through the real worker on PostgreSQL (maya_app, FORCE RLS).

The model gateway and every external tool backend are fakes; the database, the worker, the
agent fleet, the MCP client manager/gateway, the permission gate, the approval route, the
document engines and the verifiers are real.
"""

from __future__ import annotations

import hashlib
from pathlib import Path
from types import SimpleNamespace
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import httpx
import pytest
from pptx import Presentation
from sqlalchemy import select, text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.registry import create_default_fleet
from app.auth.dependencies import TenantDatabase, get_principal, get_tenant_database
from app.auth.models import Principal
from app.db.models import (
    AuditLog,
    FileArtifact,
    PlanStatus,
    Subtask,
    SubtaskAgent,
    SubtaskStatus,
    Task,
)
from app.db.tenant import tenant_session
from app.main import create_app
from app.mcp.client_manager import MCPClientManager
from app.mcp.connectors.google_search import GoogleSearchConnector
from app.mcp.gateway import McpGateway, builtin_spec
from app.mcp.permissions import ToolPermissionRegistry
from app.mcp.sanitizer import sanitize_tool_output
from app.tasks.dispatch import DocumentSpec, FleetSupport, SlideSpec
from app.tasks.idempotency import TaskIdempotencyService
from app.tasks.queue import SubtaskJob, SubtaskQueue
from app.tasks.runtime import TaskRuntime
from app.tasks.worker import SubtaskWorker, resolve_subtask_uuid
from tests.support.postgres import SeededTenant
from tests.support.settings import JWT_KEY, make_settings

pytestmark = pytest.mark.postgres

GITHUB_SECRET = "-".join(["github", "placeholder", "value", "xyz"])


def _out(sub: Subtask) -> dict[str, Any]:
    assert sub.output_result is not None
    return sub.output_result


def _proof(sub: Subtask) -> dict[str, Any]:
    assert sub.verification_proof is not None
    return sub.verification_proof


class _Publisher:
    async def publish(self, event: Any) -> bool:
        return True


class _FakeModelGateway:
    """Fake Gemini gateway: returns a canned structured deliverable, records prompts."""

    client = None

    def __init__(self, slides: int = 8) -> None:
        self.slides = slides
        self.prompts: list[str] = []

    async def generate_structured(self, **kwargs: Any) -> Any:
        self.prompts.append(str(kwargs["contents"]))
        schema = kwargs["response_schema"]
        assert schema is DocumentSpec
        return DocumentSpec(
            file_type="pptx",
            filename="Quarterly Brief!!.pptx",
            title="Quarterly brief",
            slides=[
                SlideSpec(layout="title", title="Quarterly brief", subtitle="Q3"),
                *[
                    SlideSpec(layout="bullets", title=f"Point {i}", bullets=["alpha", "beta"])
                    for i in range(1, self.slides)
                ],
            ],
        )

    async def generate_text(self, **kwargs: Any) -> Any:
        # Only the research agent may call this: research is ONE google_search grounded call whose
        # answer carries the [n] markers; unknown markers are stripped by render_citations.
        if kwargs.get("enable_google_search"):
            return SimpleNamespace(
                text="Finding [1] and a made-up one [9].",
                citations=[{"title": "T", "url": "https://example.test/a", "snippet": ""}],
            )
        assert kwargs["alias"] == "lite", "fallback LLM executor must not run for an agent role"
        return SimpleNamespace(text="Finding [1] and a made-up one [9].", citations=[])


class _Fixture:
    def __init__(
        self,
        engine: AsyncEngine,
        tmp_path: Path,
        monkeypatch: pytest.MonkeyPatch,
        model: _FakeModelGateway,
        google_search: Any = None,
    ) -> None:
        self.engine = engine
        self.queue = SubtaskQueue()
        self.registry = ToolPermissionRegistry()
        self.pr_mock = AsyncMock(
            return_value={"html_url": "https://example.test/pr/1", "number": 1}
        )
        monkeypatch.setattr("app.mcp.gateway.GitHubConnector.create_pull_request", self.pr_mock)

        async def fake_grounded_search(query: str, count: int = 5, **_scope: Any) -> Any:
            assert GITHUB_SECRET not in query
            return SimpleNamespace(
                sanitized_result=sanitize_tool_output(
                    f"result for {query}", tool_name="web_search"
                ),
                citations=[{"title": "Example", "url": "https://example.test/src", "snippet": ""}],
            )

        grounded = SimpleNamespace(search=fake_grounded_search)
        self.gateway = McpGateway(MCPClientManager(permission_registry=self.registry))
        self.gateway.register_spec(
            builtin_spec(
                github_token=GITHUB_SECRET,
                google_search=cast(GoogleSearchConnector, grounded),  # test fake (search only)
            )
        )
        self.fleet = create_default_fleet(
            client_manager=self.gateway.manager,
            permission_registry=self.registry,
            artifacts_base_dir=tmp_path,
            model_gateway=model,
            google_search_connector=google_search,
        )
        self.runtime = TaskRuntime(
            engine=engine,
            queue=self.queue,
            publisher=_Publisher(),  # type: ignore[arg-type]
            worker=SubtaskWorker(
                queue=self.queue,
                permission_registry=self.registry,
                fleet_registry=self.fleet,
                jwt_signing_key=JWT_KEY,
                tool_invoker=self.gateway,
                fleet_support=FleetSupport(gateway=model, artifacts_dir=tmp_path),  # type: ignore[arg-type]
            ),
            agent_handler=AsyncMock(side_effect=AssertionError("generic executor must not run")),
        )


@pytest.fixture
async def rig(app_engine: AsyncEngine, tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> Any:
    fixture = _Fixture(app_engine, tmp_path, monkeypatch, _FakeModelGateway())
    yield fixture
    await fixture.gateway.close()


async def _seed(
    engine: AsyncEngine,
    tenant: SeededTenant,
    agent: SubtaskAgent,
    description: str,
    input_data: dict[str, Any] | None = None,
) -> tuple[Task, Subtask]:
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        task, _ = await TaskIdempotencyService.get_or_create_task(
            session=session,
            workspace_id=tenant.workspace_id,
            user_id=tenant.user_id,
            idempotency_key=f"fleet-{uuid4().hex}",
            user_instruction=description,
        )
        task.status = "executing"
        task.plan_status = PlanStatus.EXECUTING
        sub = Subtask(
            id=resolve_subtask_uuid(task.id, "step_1"),
            task_id=task.id,
            workspace_id=tenant.workspace_id,
            assigned_agent=agent,
            description=description,
            dependencies=[],
            status=SubtaskStatus.PENDING,
            input_data=input_data or {},
        )
        session.add(sub)
        await session.flush()
        return task, sub


def _job(tenant: SeededTenant, task: Task, sub: Subtask, agent: str) -> SubtaskJob:
    return SubtaskJob(
        workspace_id=tenant.workspace_id,
        user_id=tenant.user_id,
        task_id=task.id,
        subtask_id=sub.id,
        assigned_agent=agent,
    )


async def _load(engine: AsyncEngine, tenant: SeededTenant, task_id: UUID) -> tuple[Task, Subtask]:
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        sub = (
            await session.execute(select(Subtask).where(Subtask.task_id == task_id))
        ).scalar_one()
        return task, sub


async def _audit_rows(engine: AsyncEngine, tenant: SeededTenant, task_id: UUID) -> list[AuditLog]:
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        return list(
            (await session.execute(select(AuditLog).where(AuditLog.task_id == task_id))).scalars()
        )


async def _approval_id(engine: AsyncEngine, tenant: SeededTenant, subtask_id: UUID) -> UUID:
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        row = (
            await session.execute(
                text("SELECT id, status, payload_hash FROM approval_requests WHERE subtask_id=:s"),
                {"s": subtask_id},
            )
        ).one()
        assert row.status == "pending" and len(row.payload_hash) == 64
        return row.id  # type: ignore[no-any-return]


def _api(engine: AsyncEngine, tenant: SeededTenant, queue: SubtaskQueue) -> httpx.AsyncClient:
    app = create_app(make_settings())
    principal = Principal(
        user_id=tenant.user_id, workspace_id=tenant.workspace_id, role="member", session_id=uuid4()
    )
    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[get_tenant_database] = lambda: TenantDatabase(
        engine=engine, workspace_id=tenant.workspace_id
    )
    app.state.resources = SimpleNamespace(subtask_queue=queue)
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer test-token"},
    )


def _check_pptx_on_disk(path: Path, sha256: str, root: Path) -> None:
    assert path.is_file() and path.resolve().is_relative_to(root.resolve())
    assert hashlib.sha256(path.read_bytes()).hexdigest() == sha256
    assert len(Presentation(str(path)).slides) == 8  # real python-pptx output, 8 slides


# --------------------------------------------------------------------------- workflow 7


@pytest.mark.asyncio
async def test_brief_to_pptx_runs_through_document_agent_and_registers_verified_artifact(
    rig: _Fixture, fresh_tenants: tuple[SeededTenant, SeededTenant], tmp_path: Path
) -> None:
    tenant, _ = fresh_tenants
    task, sub = await _seed(
        rig.engine, tenant, SubtaskAgent.DOCUMENT, "Turn the Q3 brief into an 8-slide deck"
    )
    await rig.runtime.process_job(_job(tenant, task, sub, "document"))

    done_task, done = await _load(rig.engine, tenant, task.id)
    assert done.status == SubtaskStatus.COMPLETED, done.error_message
    assert done_task.status == "completed"
    proof = _proof(done)
    assert proof["status"] == "verified" and proof["file_type"] == "pptx"

    _check_pptx_on_disk(Path(proof["storage_uri"]), proof["sha256"], tmp_path)

    async with tenant_session(rig.engine, tenant.workspace_id, tenant.user_id) as session:
        artifacts = list(
            (
                await session.execute(select(FileArtifact).where(FileArtifact.task_id == task.id))
            ).scalars()
        )
    assert len(artifacts) == 1
    assert artifacts[0].verification_status == "verified"
    assert artifacts[0].sha256 == proof["sha256"] and artifacts[0].file_type == "pptx"


@pytest.mark.asyncio
async def test_slide_count_mismatch_completes_with_a_note_instead_of_failing(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, _ = fresh_tenants
    rig = _Fixture(app_engine, tmp_path, monkeypatch, _FakeModelGateway(slides=6))
    task, sub = await _seed(
        rig.engine, tenant, SubtaskAgent.DOCUMENT, "Make an 8-slide deck about Q3"
    )
    job = _job(tenant, task, sub, "document")
    await rig.runtime.process_job(job)

    done_task, done = await _load(rig.engine, tenant, task.id)
    # Count policy: a deck with fewer/more slides than asked is delivered with a note, never
    # rejected (a real verification failure, e.g. an unreadable file, still fails the subtask).
    assert done.status == SubtaskStatus.COMPLETED
    assert done_task.status == "completed"
    async with tenant_session(rig.engine, tenant.workspace_id, tenant.user_id) as session:
        count = (
            (await session.execute(select(FileArtifact).where(FileArtifact.task_id == task.id)))
            .scalars()
            .all()
        )
    assert len(count) == 1


# --------------------------------------------------------------------------- agents + tools


@pytest.mark.asyncio
async def test_research_subtask_runs_through_research_agent_with_citations(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, _ = fresh_tenants
    rig = _Fixture(app_engine, tmp_path, monkeypatch, _FakeModelGateway())
    task, sub = await _seed(rig.engine, tenant, SubtaskAgent.RESEARCH, "Research MCP servers")
    await rig.runtime.process_job(_job(tenant, task, sub, "research"))

    _, done = await _load(rig.engine, tenant, task.id)
    assert done.status == SubtaskStatus.COMPLETED
    assert "Finding [1]" in _out(done)["summary"]
    assert "[9]" not in _out(done)["summary"]  # fabricated citation stripped
    assert "1. T - https://example.test/a" in _out(done)["summary"]
    assert [c["url"] for c in _out(done)["citations"]] == ["https://example.test/a"]
    assert _proof(done)["status"] == "unverified"  # honest: no independent verifier


@pytest.mark.asyncio
async def test_read_tool_runs_via_mcp_gate_with_secret_kept_out_of_audit(
    rig: _Fixture, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    task, sub = await _seed(
        rig.engine,
        tenant,
        # A research node that names web_search runs the research pipeline (see the worker);
        # the explicit tool path is exercised through a step of another role.
        SubtaskAgent.FILE,
        "Search the web",
        {"tool": "web_search", "tool_args": {"query": "mcp pinning"}},
    )
    await rig.runtime.process_job(_job(tenant, task, sub, "file"))

    _, done = await _load(rig.engine, tenant, task.id)
    assert done.status == SubtaskStatus.COMPLETED
    assert "result for mcp pinning" in _out(done)["output"]
    assert "Sources:\n1. Example - https://example.test/src" in _out(done)["output"]
    assert [c["url"] for c in _out(done)["citations"]] == ["https://example.test/src"]
    assert _out(done)["untrusted_content"] is True
    rows = await _audit_rows(rig.engine, tenant, task.id)
    tool_rows = [r for r in rows if r.tool_name == "web_search"]
    assert tool_rows and tool_rows[0].status == "success"
    assert tool_rows[0].server_name == "maya-tools" and tool_rows[0].permission_level == "READ"
    assert GITHUB_SECRET not in str([(r.input_redacted, r.output_redacted) for r in rows])
    assert GITHUB_SECRET not in str(done.output_result) + str(done.verification_proof)


@pytest.mark.asyncio
async def test_missing_credential_fails_subtask_clearly_never_fake_success(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    tenant, _ = fresh_tenants
    rig = _Fixture(app_engine, tmp_path, monkeypatch, _FakeModelGateway())
    bare = McpGateway(MCPClientManager(permission_registry=rig.registry))
    bare.register_spec(builtin_spec(github_token=None))  # no credentials
    rig.runtime.worker.tool_invoker = bare
    task, sub = await _seed(
        rig.engine,
        tenant,
        SubtaskAgent.FILE,
        "Search",
        {"tool": "web_search", "tool_args": {"query": "x"}},
    )
    await rig.runtime.process_job(_job(tenant, task, sub, "file"))

    done_task, done = await _load(rig.engine, tenant, task.id)
    await bare.close()
    assert done.status == SubtaskStatus.FAILED and done_task.status == "failed"
    assert "no backend" in (done.error_message or "")
    assert done.output_result in (None, {})


@pytest.mark.asyncio
async def test_unclassified_and_restricted_tools_are_denied_and_audited(
    rig: _Fixture, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    for tool, level in (("mystery_shell", None), ("database_ddl", "RESTRICTED")):
        task, sub = await _seed(
            rig.engine, tenant, SubtaskAgent.CODING, "Do it", {"tool": tool, "tool_args": {}}
        )
        await rig.runtime.process_job(_job(tenant, task, sub, "coding"))
        done_task, done = await _load(rig.engine, tenant, task.id)
        assert done.status == SubtaskStatus.FAILED and done_task.status == "failed"
        rows = await _audit_rows(rig.engine, tenant, task.id)
        denied = [r for r in rows if r.tool_name == tool]
        assert denied and denied[0].status == "denied"
        assert denied[0].permission_level == level
        async with tenant_session(rig.engine, tenant.workspace_id, tenant.user_id) as session:
            pending = (
                await session.execute(
                    text("SELECT count(*) FROM approval_requests WHERE subtask_id=:s"),
                    {"s": sub.id},
                )
            ).scalar_one()
        assert pending == 0  # RESTRICTED stays blocked: no approval can be requested


# --------------------------------------------------------------------------- workflow 5 gate

_PR_ARGS = {"repo": "o/r", "title": "Fix", "head": "fix", "base": "main", "body": "b"}


async def _paused_pr_task(rig: _Fixture, tenant: SeededTenant) -> tuple[Task, Subtask, UUID]:
    task, sub = await _seed(
        rig.engine,
        tenant,
        SubtaskAgent.CODING,
        "Open the PR",
        {"tool": "github_create_pull_request", "tool_args": _PR_ARGS},
    )
    await rig.runtime.process_job(_job(tenant, task, sub, "coding"))
    paused_task, paused = await _load(rig.engine, tenant, task.id)
    assert paused.status == SubtaskStatus.AWAITING_APPROVAL
    assert paused_task.status == "awaiting_approval"
    rig.pr_mock.assert_not_awaited()  # nothing mutated before approval
    return task, sub, await _approval_id(rig.engine, tenant, sub.id)


@pytest.mark.asyncio
async def test_approval_required_tool_pauses_then_approve_resumes_with_bound_token(
    rig: _Fixture, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    task, sub, approval_id = await _paused_pr_task(rig, tenant)

    async with _api(rig.engine, tenant, rig.queue) as client:
        response = await client.post(
            f"/api/v1/approvals/{approval_id}/decide", json={"action": "approve"}
        )
    assert response.status_code == 200, response.text
    resume = await rig.queue.dequeue(timeout_seconds=1.0)
    assert resume is not None and resume.subtask_id == sub.id
    await rig.runtime.process_job(resume)

    done_task, done = await _load(rig.engine, tenant, task.id)
    assert done.status == SubtaskStatus.COMPLETED, done.error_message
    assert done_task.status == "completed"
    rig.pr_mock.assert_awaited_once()
    pr_call = rig.pr_mock.await_args
    assert pr_call is not None
    assert pr_call.kwargs["has_valid_approval_token"] is True
    assert "approval_token" not in str(done.output_result)
    rows = await _audit_rows(rig.engine, tenant, task.id)
    statuses = {(r.tool_name, r.status) for r in rows}
    assert ("github_create_pull_request", "success") in statuses
    assert ("approval.approved", "success") in statuses


@pytest.mark.asyncio
async def test_denying_the_approval_fails_the_subtask_honestly(
    rig: _Fixture, fresh_tenants: tuple[SeededTenant, SeededTenant]
) -> None:
    tenant, _ = fresh_tenants
    task, _sub, approval_id = await _paused_pr_task(rig, tenant)

    async with _api(rig.engine, tenant, rig.queue) as client:
        response = await client.post(
            f"/api/v1/approvals/{approval_id}/decide",
            json={"action": "reject", "rejection_reason": "not now"},
        )
    assert response.status_code == 200, response.text
    assert rig.queue.is_empty

    done_task, done = await _load(rig.engine, tenant, task.id)
    assert done.status == SubtaskStatus.FAILED and "rejected" in (done.error_message or "")
    assert done_task.status == "failed"
    rig.pr_mock.assert_not_awaited()
