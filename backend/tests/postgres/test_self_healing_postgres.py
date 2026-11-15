"""Self-healing task execution through the real runtime and worker on PostgreSQL.

Fake agents and a fake model stand in for Gemini, Gmail, Calendar and Notion; the database, the
runtime, the worker, the permission gate, the MCP gateway, the document agent and its verifiers
are real. Owner report: a ``pm`` step that outlines slides failed with ``all_sources_failed``,
and a ``save_file`` step failed with "not classified and is denied by default", each killing a
task whose research and documents were fine.
"""

from __future__ import annotations

import asyncio
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest
from pptx import Presentation
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.planner import PlanDecompositionResponse, SubTaskPlanItem, build_subtasks
from app.agents.registry import AgentFleetRegistry
from app.agents.schemas import AgentExecutionStatus, SpecialistAgentResult, SpecialistAgentRole
from app.agents.specialized.document import DocumentAgent
from app.agents.state import persisted_input_data
from app.auth.dependencies import TenantDatabase, get_principal, get_tenant_database
from app.auth.models import Principal
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
from app.main import create_app
from app.mcp.client_manager import MCPClientManager
from app.mcp.gateway import McpGateway, builtin_spec
from app.mcp.permissions import ToolPermissionRegistry
from app.tasks.dispatch import BlockSpec, DocumentSpec, FleetSupport, SlideSpec
from app.tasks.idempotency import TaskIdempotencyService
from app.tasks.queue import SubtaskJob, SubtaskQueue
from app.tasks.runtime import TaskRuntime
from app.tasks.worker import NonRetryableSubtaskError, SubtaskWorker, resolve_subtask_uuid
from tests.support.postgres import SeededTenant
from tests.support.settings import JWT_KEY, make_settings

pytestmark = pytest.mark.postgres

OUTLINE_TEXT = "Slide 1 intro, slides 2-6 one per scooter, slide 7 comparison, slide 8 verdict."
CALENDAR_UNAVAILABLE = "Neither Google Calendar nor Notion is connected."


def _out(sub: Subtask) -> dict[str, Any]:
    assert sub.output_result is not None
    return sub.output_result


def _proof(sub: Subtask) -> dict[str, Any]:
    assert sub.verification_proof is not None
    return sub.verification_proof


class _Publisher:
    async def publish(self, event: Any) -> bool:
        return True


class _Research:
    execution_timeout_seconds: float | None = None

    def availability_error(self) -> str | None:
        return None

    async def execute_subtask(self, job: SubtaskJob, ctx: dict[str, Any]) -> SpecialistAgentResult:
        return SpecialistAgentResult(
            agent_role=SpecialistAgentRole.RESEARCH,
            status=AgentExecutionStatus.SUCCESS,
            summary="# Scooters\n\nOla S1 Pro has a 195 km range [1].\n\n- Ather 450X is quick\n"
            "- TVS iQube is practical",
            verification_proof={"status": "unverified"},
            citations=[{"title": "Review", "url": "https://example.test/review", "snippet": ""}],
        )


class _Pm:
    """The real PM agent's two failure modes, without any credential."""

    execution_timeout_seconds: float | None = None

    def __init__(self, *, unavailable: bool = True) -> None:
        self.unavailable = unavailable
        self.calls = 0

    def availability_error(self) -> str | None:
        return CALENDAR_UNAVAILABLE if self.unavailable else None

    async def execute_subtask(self, job: SubtaskJob, ctx: dict[str, Any]) -> SpecialistAgentResult:
        self.calls += 1
        return SpecialistAgentResult(
            agent_role=SpecialistAgentRole.PM,
            status=AgentExecutionStatus.FAILED,
            summary="The daily briefing could not be built. Notion: 404",
            verification_proof={"error": "all_sources_failed"},
        )


class _Email:
    execution_timeout_seconds: float | None = None

    def __init__(self) -> None:
        self.calls = 0

    def availability_error(self) -> str | None:
        return "Gmail is not connected. Set MAYA_GOOGLE_ACCESS_TOKEN and restart the backend."

    async def execute_subtask(self, job: SubtaskJob, ctx: dict[str, Any]) -> SpecialistAgentResult:
        self.calls += 1
        raise AssertionError("must not run")


class _Capture:
    """A document-role stand-in that records the context it was given."""

    execution_timeout_seconds: float | None = None

    def __init__(self) -> None:
        self.contexts: list[dict[str, Any]] = []

    def availability_error(self) -> str | None:
        return None

    async def execute_subtask(self, job: SubtaskJob, ctx: dict[str, Any]) -> SpecialistAgentResult:
        self.contexts.append(ctx)
        return SpecialistAgentResult(
            agent_role=SpecialistAgentRole.RESEARCH,
            status=AgentExecutionStatus.SUCCESS,
            summary="done",
            verification_proof={"status": "unverified"},
        )


class _Model:
    """Fake Gemini: a pdf spec whose blocks have NO ``type`` key, or an 8-slide deck."""

    client = None

    async def generate_structured(self, **kwargs: Any) -> Any:
        assert kwargs["response_schema"] is DocumentSpec
        prompt = str(kwargs["contents"])
        if "8-slide" in prompt:
            return DocumentSpec(
                file_type="pptx",
                filename="Scooters.pptx",
                title="Scooters",
                slides=[SlideSpec(layout="title", title="Scooters", subtitle="India")]
                + [
                    SlideSpec(layout="bullets", title=f"Point {i}", bullets=["a", "b"])
                    for i in range(1, 8)
                ],
            )
        return DocumentSpec(
            file_type="pdf",
            filename="Scooters.pdf",
            title="Scooters",
            blocks=[
                BlockSpec(type="", text="Top scooters"),  # a heading written without its type
                BlockSpec(type="p", text="Ola, Ather and TVS compared."),
                BlockSpec(type="ul", items=["Range", "Price"]),
            ],
        )


class _Rig:
    def __init__(self, engine: AsyncEngine, tmp_path: Path, *, concurrency: int = 3) -> None:
        self.engine = engine
        self.queue = SubtaskQueue()
        self.registry = ToolPermissionRegistry()
        self.gateway = McpGateway(MCPClientManager(permission_registry=self.registry))
        self.gateway.register_spec(builtin_spec(github_token=None, google_search=None))
        self.fleet = AgentFleetRegistry()
        self.pm = _Pm()
        self.email = _Email()
        self.capture = _Capture()
        self.fleet.register_agent("research", _Research())  # type: ignore[arg-type]
        self.fleet.register_agent("pm", self.pm)  # type: ignore[arg-type]
        self.fleet.register_agent("email", self.email)  # type: ignore[arg-type]
        self.fleet.register_agent(
            "document",
            DocumentAgent(artifacts_base_dir=tmp_path, permission_registry=self.registry),
        )
        self.generic_calls: list[tuple[str, dict[str, Any]]] = []
        self.generic_error: Exception | None = None
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
                fleet_support=FleetSupport(gateway=_Model(), artifacts_dir=tmp_path),  # type: ignore[arg-type]
            ),
            agent_handler=self._generic,
            concurrency=concurrency,
        )

    async def _generic(self, job: SubtaskJob, data: dict[str, Any]) -> dict[str, Any]:
        self.generic_calls.append((job.assigned_agent, data))
        if self.generic_error is not None:
            raise self.generic_error
        return {
            "result": {"summary": "Completed", "content": OUTLINE_TEXT},
            "proof": {"status": "unverified", "kind": "llm_text"},
        }

    async def run(
        self, tenant: SeededTenant, task: Task, ready: list[Subtask], *, settle: bool = False
    ) -> None:
        await self.runtime.start(reconcile=False)
        try:
            for sub in ready:
                role = str(getattr(sub.assigned_agent, "value", sub.assigned_agent))
                await self.runtime.queue.enqueue(
                    SubtaskJob(
                        workspace_id=tenant.workspace_id,
                        user_id=tenant.user_id,
                        task_id=task.id,
                        subtask_id=sub.id,
                        assigned_agent=role,
                    )
                )
            await _wait_done(self.engine, tenant, task.id)
            if settle:  # also wait for steps still running when the task already failed
                async with asyncio.timeout(30):
                    while any(  # noqa: ASYNC110
                        st.status in (SubtaskStatus.PENDING, SubtaskStatus.IN_PROGRESS)
                        for st in (await _state(self.engine, tenant, task.id))[1].values()
                    ):
                        await asyncio.sleep(0.1)
        finally:
            for pending in list(self.runtime._delayed):
                pending.cancel()
            await self.runtime.stop()


def _api(engine: AsyncEngine, tenant: SeededTenant) -> httpx.AsyncClient:
    app = create_app(make_settings())
    principal = Principal(
        user_id=tenant.user_id, workspace_id=tenant.workspace_id, role="member", session_id=uuid4()
    )
    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[get_tenant_database] = lambda: TenantDatabase(
        engine=engine, workspace_id=tenant.workspace_id
    )
    return httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test",
        headers={"Authorization": "Bearer test-token"},
    )


Node = tuple[str, SubtaskAgent, str, list[str], dict[str, Any]]


async def _seed(
    engine: AsyncEngine, tenant: SeededTenant, nodes: list[Node], status: str = "executing"
) -> tuple[Task, dict[str, Subtask]]:
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        task, _ = await TaskIdempotencyService.get_or_create_task(
            session=session,
            workspace_id=tenant.workspace_id,
            user_id=tenant.user_id,
            idempotency_key=f"heal-{uuid4().hex}",
            user_instruction="compare scooters",
        )
        task.status = status
        task.plan_status = PlanStatus.EXECUTING
        subs: dict[str, Subtask] = {}
        for name, agent, description, deps, data in nodes:
            sub = Subtask(
                id=resolve_subtask_uuid(task.id, name),
                task_id=task.id,
                workspace_id=tenant.workspace_id,
                assigned_agent=agent,
                description=description,
                dependencies=[str(resolve_subtask_uuid(task.id, d)) for d in deps],
                status=SubtaskStatus.PENDING,
                input_data=data,
            )
            session.add(sub)
            subs[name] = sub
        await session.flush()
        return task, subs


async def _state(
    engine: AsyncEngine, tenant: SeededTenant, task_id: UUID
) -> tuple[Task, dict[UUID, Subtask]]:
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        subs = (await session.execute(select(Subtask).where(Subtask.task_id == task_id))).scalars()
        return task, {s.id: s for s in subs}


async def _wait_done(
    engine: AsyncEngine,
    tenant: SeededTenant,
    task_id: UUID,
    *,
    timeout: float = 60.0,  # noqa: ASYNC109
) -> None:
    async with asyncio.timeout(timeout):
        while (await _state(engine, tenant, task_id))[0].status not in ("completed", "failed"):  # noqa: ASYNC110
            await asyncio.sleep(0.1)


def _ready(subs: dict[str, Subtask], *names: str) -> list[Subtask]:
    return [subs[n] for n in names]


async def _events(engine: AsyncEngine, tenant: SeededTenant, task_id: UUID) -> list[dict[str, Any]]:
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        rows = (
            await session.execute(select(TaskOutboxEvent).where(TaskOutboxEvent.task_id == task_id))
        ).scalars()
        return [{"type": r.event_type, **r.payload} for r in rows]


async def _artifacts(
    engine: AsyncEngine, tenant: SeededTenant, task_id: UUID
) -> list[FileArtifact]:
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        return list(
            (
                await session.execute(select(FileArtifact).where(FileArtifact.task_id == task_id))
            ).scalars()
        )


# The exact plan the owner's task produced (pm outline node, document nodes, invented save step).
def _owner_plan() -> list[Node]:
    return [
        ("r1", SubtaskAgent.RESEARCH, "Research the top 5 electric scooters in India", [], {}),
        (
            "o1",
            SubtaskAgent.PM,
            "Outline the 8-slide presentation structure for comparing the top 5 electric scooters",
            ["r1"],
            {"critical": False},
        ),
        (
            "d1",
            SubtaskAgent.DOCUMENT,
            "Create a 2-page PDF report comparing the scooters",
            ["r1"],
            {},
        ),
        (
            "d2",
            SubtaskAgent.DOCUMENT,
            "Create the 8-slide deck comparing the scooters",
            ["r1", "o1"],
            {},
        ),
        (
            "s1",
            SubtaskAgent.FILE,
            "Save the generated 2-page PDF report to the designated workspace",
            ["d1"],
            {
                "tool": "save_file",
                "tool_args": {"path": "workspace/report.pdf", "destination": "workspace"},
            },
        ),
    ]


@pytest.mark.asyncio
async def test_owner_task_heals_pm_misuse_and_the_invented_save_tool_and_delivers_both_files(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
) -> None:
    tenant, _ = fresh_tenants
    rig = _Rig(app_engine, tmp_path)
    task, subs = await _seed(app_engine, tenant, _owner_plan())
    await rig.run(tenant, task, _ready(subs, "r1"))

    done_task, by_id = await _state(app_engine, tenant, task.id)
    errors = {s.description[:30]: s.error_message for s in by_id.values() if s.error_message}
    assert done_task.status == "completed", errors
    outline = by_id[subs["o1"].id]
    save = by_id[subs["s1"].id]
    # 1. The pm outline ran as the generic executor instead, and says so.
    assert outline.status == SubtaskStatus.COMPLETED
    assert outline.assigned_agent == SubtaskAgent.FILE
    assert _out(outline)["recovered"].startswith("Recovered: ran as executor instead of pm")
    assert _proof(outline)["healing"][0]["from"] == "pm"
    assert rig.pm.calls == 0  # the pm agent was never called without its credentials
    assert [c[1].get("executor") for c in rig.generic_calls] == ["text"]
    # 2. The invented save tool was skipped: the document step already stored the artifact.
    assert save.status == SubtaskStatus.COMPLETED
    assert _out(save)["skipped"] is True
    assert "already saved by" in _out(save)["reason"]
    assert _proof(save)["status"] == "skipped"
    assert "Recovered:" in _out(save)["recovered"]
    # 3. Both deliverables exist and are verified.
    artifacts = {a.file_type: a for a in await _artifacts(app_engine, tenant, task.id)}
    assert set(artifacts) == {"pdf", "pptx"}
    assert all(a.verification_status == "verified" for a in artifacts.values())
    assert len(Presentation(artifacts["pptx"].storage_uri).slides) == 8
    # 4. The pdf spec had type-less / aliased blocks: normalised, recorded in the proof.
    pdf_step = by_id[subs["d1"].id]
    attempts = [h["attempt"] for h in _proof(pdf_step)["healing"]]
    assert "normalise" in attempts
    # 5. The events and the task result carry the trail.
    events = await _events(app_engine, tenant, task.id)
    assert any(e.get("healing", {}).get("to") == "executor" for e in events)
    assert any(e.get("skipped") for e in events)
    completed = [e for e in events if e["type"] == "task_completed"][0]
    assert {h["subtask_id"] for h in completed["healing"]} == {str(outline.id), str(save.id)}
    assert done_task.shared_context["healing"]


@pytest.mark.asyncio
async def test_the_same_owner_plan_after_planner_repair_needs_no_healing(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
) -> None:
    tenant, _ = fresh_tenants
    rig = _Rig(app_engine, tmp_path)
    plan = PlanDecompositionResponse(
        intent="compare scooters",
        subtasks=[
            SubTaskPlanItem(id="r1", assigned_agent="research", description="Research scooters"),
            SubTaskPlanItem(
                id="o1",
                assigned_agent="pm",
                description="Outline the 8-slide presentation structure for the scooters",
                dependencies=["r1"],
            ),
            SubTaskPlanItem(
                id="d1",
                assigned_agent="document",
                description="Create a 2-page PDF report comparing the scooters",
                dependencies=["r1"],
            ),
            SubTaskPlanItem(
                id="d2",
                assigned_agent="document",
                description="Create the 8-slide deck comparing the scooters",
                dependencies=["r1", "o1"],
            ),
            SubTaskPlanItem(
                id="s1",
                assigned_agent="file",
                description="Save the generated 2-page PDF report to the designated workspace",
                dependencies=["d1"],
                tool="save_file",
                tool_args={"path": "workspace/report.pdf", "destination": "workspace"},
            ),
        ],
    )
    nodes: list[Node] = []
    for st in build_subtasks(plan.subtasks):
        nodes.append(
            (
                st.id,
                SubtaskAgent(st.assigned_agent),
                st.description,
                st.dependencies,
                persisted_input_data(st),
            )
        )
    assert {n[0] for n in nodes} == {"r1", "o1", "d1", "d2"}  # the save step is gone
    assert dict((n[0], n[1]) for n in nodes)["o1"] == SubtaskAgent.FILE  # pm -> executor
    task, subs = await _seed(app_engine, tenant, nodes)
    await rig.run(tenant, task, _ready(subs, "r1"))

    done_task, by_id = await _state(app_engine, tenant, task.id)
    assert done_task.status == "completed", [s.error_message for s in by_id.values()]
    assert not (done_task.shared_context or {}).get("healing")  # nothing needed healing
    artifacts = await _artifacts(app_engine, tenant, task.id)
    assert sorted(a.file_type for a in artifacts) == ["pdf", "pptx"]


@pytest.mark.asyncio
async def test_an_invented_tool_is_retried_without_the_tool_and_succeeds(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
) -> None:
    tenant, _ = fresh_tenants
    rig = _Rig(app_engine, tmp_path)
    task, subs = await _seed(
        app_engine,
        tenant,
        [
            (
                "r1",
                SubtaskAgent.RESEARCH,
                "Research electric scooters",
                [],
                {"tool": "mystery_search", "tool_args": {"q": "scooters"}},
            )
        ],
    )
    await rig.run(tenant, task, _ready(subs, "r1"))

    done_task, by_id = await _state(app_engine, tenant, task.id)
    step = by_id[subs["r1"].id]
    assert done_task.status == "completed" and step.status == SubtaskStatus.COMPLETED
    assert "tool" not in step.input_data  # the invented tool is gone
    assert _proof(step)["healing"][0] == {
        "attempt": 1,
        "from": "mystery_search",
        "to": "no tool",
        "reason": "tool 'mystery_search' is not available or not classified",
    }
    assert "ran without the tool 'mystery_search'" in _out(step)["recovered"]


@pytest.mark.asyncio
async def test_unsuitable_pm_content_step_is_rerouted_to_the_executor(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
) -> None:
    tenant, _ = fresh_tenants
    rig = _Rig(app_engine, tmp_path)
    rig.pm.unavailable = False  # the agent runs and fails with all_sources_failed
    task, subs = await _seed(
        app_engine,
        tenant,
        [("o1", SubtaskAgent.PM, "Outline the slide structure for the comparison", [], {})],
    )
    await rig.run(tenant, task, _ready(subs, "o1"))

    done_task, by_id = await _state(app_engine, tenant, task.id)
    step = by_id[subs["o1"].id]
    assert done_task.status == "completed" and step.status == SubtaskStatus.COMPLETED
    assert rig.pm.calls == 1  # it was tried once, then the executor took over
    assert _out(step)["content"] == OUTLINE_TEXT


@pytest.mark.asyncio
async def test_email_without_credentials_is_not_rerouted_and_fails_with_the_actionable_text(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
) -> None:
    tenant, _ = fresh_tenants
    rig = _Rig(app_engine, tmp_path)
    task, subs = await _seed(
        app_engine,
        tenant,
        [
            (
                "e1",
                SubtaskAgent.EMAIL,
                "Write and send the comparison to boss@example.test",
                [],
                {"requires_approval": True, "critical": False},  # a side effect is always critical
            )
        ],
    )
    await rig.run(tenant, task, _ready(subs, "e1"))

    done_task, by_id = await _state(app_engine, tenant, task.id)
    step = by_id[subs["e1"].id]
    assert done_task.status == "failed" and step.status == SubtaskStatus.FAILED
    assert "MAYA_GOOGLE_ACCESS_TOKEN" in (step.error_message or "")
    assert rig.generic_calls == [] and rig.email.calls == 0
    assert done_task.shared_context["failure"]["message"] == step.error_message


@pytest.mark.asyncio
async def test_pm_asked_for_the_real_calendar_is_not_pretended_to_work(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
) -> None:
    tenant, _ = fresh_tenants
    rig = _Rig(app_engine, tmp_path)
    task, subs = await _seed(
        app_engine,
        tenant,
        [("p1", SubtaskAgent.PM, "Summarise my calendar meetings for today", [], {})],
    )
    await rig.run(tenant, task, _ready(subs, "p1"))
    done_task, by_id = await _state(app_engine, tenant, task.id)
    assert done_task.status == "failed"
    assert by_id[subs["p1"].id].error_message == CALENDAR_UNAVAILABLE
    assert rig.generic_calls == []


@pytest.mark.asyncio
async def test_a_failed_non_critical_step_lets_the_task_finish_with_warnings(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
) -> None:
    tenant, _ = fresh_tenants
    rig = _Rig(app_engine, tmp_path)
    rig.generic_error = NonRetryableSubtaskError("the outline model call failed")
    task, subs = await _seed(
        app_engine,
        tenant,
        [
            (
                "o1",
                SubtaskAgent.FILE,
                "Draft the optional outline",
                [],
                {"executor": "text", "critical": False},
            ),
            ("d1", SubtaskAgent.DOCUMENT, "Create the PDF report", ["o1"], {}),
        ],
    )
    # Replace the document agent with a capture so the context the dependent got is visible.
    rig.fleet.register_agent("document", rig.capture)  # type: ignore[arg-type]
    await rig.run(tenant, task, _ready(subs, "o1"))

    done_task, by_id = await _state(app_engine, tenant, task.id)
    assert by_id[subs["o1"].id].status == SubtaskStatus.FAILED
    assert by_id[subs["d1"].id].status == SubtaskStatus.COMPLETED
    assert done_task.status == "completed"
    warning = done_task.shared_context["warnings"][0]
    assert "optional outline" in warning and "the outline model call failed" in warning
    ctx = rig.capture.contexts[0]
    assert ctx["missing_dependencies"] == [str(subs["o1"].id)]
    assert ctx["dependency_outputs"][str(subs["o1"].id)]["missing"] is True
    completed = [
        e for e in await _events(app_engine, tenant, task.id) if e["type"] == "task_completed"
    ]
    assert completed[0]["warnings"] == done_task.shared_context["warnings"]
    async with _api(app_engine, tenant) as client:
        body = (await client.get(f"/api/v1/tasks/{task.id}")).json()
    assert body["status"] == "completed" and body["warnings"] == [warning]


@pytest.mark.asyncio
async def test_a_failed_critical_step_fails_the_task_and_shows_the_first_real_cause(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
) -> None:
    tenant, _ = fresh_tenants
    rig = _Rig(app_engine, tmp_path)
    rig.generic_error = NonRetryableSubtaskError("the model call failed")
    task, subs = await _seed(
        app_engine,
        tenant,
        [
            ("o1", SubtaskAgent.FILE, "Draft the outline", [], {"executor": "text"}),
            ("d1", SubtaskAgent.DOCUMENT, "Create the PDF report", ["o1"], {}),
        ],
    )
    await rig.run(tenant, task, _ready(subs, "o1"))
    done_task, by_id = await _state(app_engine, tenant, task.id)
    assert done_task.status == "failed"
    assert by_id[subs["d1"].id].status == SubtaskStatus.PENDING  # never ran
    assert done_task.shared_context["failure"]["message"] == "the model call failed"
    async with _api(app_engine, tenant) as client:
        body = (await client.get(f"/api/v1/tasks/{task.id}")).json()
    assert body["status"] == "failed" and body["error"] == "the model call failed"


@pytest.mark.asyncio
async def test_healing_is_bounded_and_never_loops(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
) -> None:
    tenant, _ = fresh_tenants
    rig = _Rig(app_engine, tmp_path)
    rig.generic_error = NonRetryableSubtaskError("executor failed too")
    task, subs = await _seed(
        app_engine,
        tenant,
        [
            ("o1", SubtaskAgent.PM, "Outline the slide structure", [], {}),
            (
                "x1",
                SubtaskAgent.RESEARCH,
                "Research scooters",
                [],
                {
                    "tool": "mystery",
                    "healing": [
                        {"attempt": 1, "from": "a", "to": "b", "reason": "r"},
                        {"attempt": 2, "from": "b", "to": "c", "reason": "r"},
                    ],
                },
            ),
        ],
    )
    await rig.run(tenant, task, _ready(subs, "o1", "x1"), settle=True)
    _, by_id = await _state(app_engine, tenant, task.id)
    # pm -> executor (one hop), the executor failed: failed, not bounced around again.
    assert by_id[subs["o1"].id].status == SubtaskStatus.FAILED
    assert len(by_id[subs["o1"].id].input_data["healing"]) == 1
    assert len(rig.generic_calls) == 1
    # A step that already used its two hops is denied outright.
    assert by_id[subs["x1"].id].status == SubtaskStatus.FAILED
    assert "denied by default" in (by_id[subs["x1"].id].error_message or "")
    assert len(by_id[subs["x1"].id].input_data["healing"]) == 2


@pytest.mark.asyncio
async def test_restricted_and_approval_steps_are_never_healed_even_when_non_critical(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
) -> None:
    tenant, _ = fresh_tenants
    rig = _Rig(app_engine, tmp_path)
    task, subs = await _seed(
        app_engine,
        tenant,
        [
            (
                "x1",
                SubtaskAgent.FILE,
                "Drop the table",
                [],
                {"tool": "database_ddl", "tool_args": {}, "critical": False},
            )
        ],
    )
    await rig.run(tenant, task, _ready(subs, "x1"))
    done_task, by_id = await _state(app_engine, tenant, task.id)
    assert done_task.status == "failed"  # a RESTRICTED denial is not tolerated either
    assert "RESTRICTED" in (by_id[subs["x1"].id].error_message or "")
    assert rig.generic_calls == []

    task2, subs2 = await _seed(
        app_engine,
        tenant,
        [
            (
                "p1",
                SubtaskAgent.FILE,
                "Open the pull request",
                [],
                {
                    "tool": "github_create_pull_request",
                    "tool_args": {"repo": "o/r", "title": "t", "head": "h", "base": "main"},
                },
            )
        ],
    )
    await rig.runtime.process_job(
        SubtaskJob(
            workspace_id=tenant.workspace_id,
            user_id=tenant.user_id,
            task_id=task2.id,
            subtask_id=subs2["p1"].id,
            assigned_agent="file",
        )
    )
    paused_task, paused = await _state(app_engine, tenant, task2.id)
    assert paused[subs2["p1"].id].status == SubtaskStatus.AWAITING_APPROVAL
    assert paused_task.status == "awaiting_approval"
    assert "healing" not in paused[subs2["p1"].id].input_data


@pytest.mark.asyncio
async def test_a_cancelled_task_is_never_healed(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
) -> None:
    tenant, _ = fresh_tenants
    rig = _Rig(app_engine, tmp_path)
    task, subs = await _seed(
        app_engine,
        tenant,
        [("r1", SubtaskAgent.RESEARCH, "Research scooters", [], {"tool": "mystery"})],
        status="cancelled",
    )
    await rig.runtime.process_job(
        SubtaskJob(
            workspace_id=tenant.workspace_id,
            user_id=tenant.user_id,
            task_id=task.id,
            subtask_id=subs["r1"].id,
            assigned_agent="research",
        )
    )
    done_task, by_id = await _state(app_engine, tenant, task.id)
    assert done_task.status == "cancelled"
    assert "healing" not in by_id[subs["r1"].id].input_data
    assert rig.queue.qsize() == 0  # nothing was re-queued
