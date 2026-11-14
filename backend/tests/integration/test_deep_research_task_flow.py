"""Deep research as a TASK, and two tasks at once. Offline: fake gateway, search, pages; the
database is replaced by recording fakes.

Owner bug (Tasks page): "Deep research: ..." ran two plain planned steps (one grounded search, one
synthesis) instead of the deep-research engine, because the chat passes an EMPTY spec ``{}`` when
the user asked for no site count and the submission tested that dict for truthiness. A second task
started while the first one ran must not change the first one's result.
"""

from __future__ import annotations

import asyncio
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.agents.model_policy import configure, reset
from app.agents.schemas import AgentExecutionStatus, SpecialistAgentRole
from app.agents.specialized.document import DocumentAgent
from app.agents.specialized.research import ResearchAgent
from app.agents.state import SubTask
from app.research.limits import ResearchLimits
from app.research.plan import STEP_ID
from app.research.runtime import DeepResearchRuntime
from app.search.fetch import PageResult
from app.search.models import SearchHit
from app.search.report import NativeResearch
from app.services import task_submission
from app.services.task_submission import TaskSubmissionService, queued_label
from app.tasks.idempotency import TaskIdempotencyService
from app.tasks.queue import SubtaskJob, SubtaskQueue
from tests.support.settings import make_settings
from tests.unit.test_research_pipeline import FakeFetcher, FakeGateway, FakeSearch
from tests.unit.test_search_service import FakeFetcher as PageFetcherFake
from tests.unit.test_search_service import Provider, service_for


@pytest.fixture(autouse=True)
def _policy() -> Any:
    configure(make_settings())
    yield
    reset()


def old_plan() -> list[SubTask]:
    """What the planner makes for a plain research request: the plan the owner saw."""
    return [
        SubTask(
            id="s1",
            assigned_agent="research",
            description="Perform a web search to find the latest costs for solar battery storage",
            dependencies=[],
            status="pending",
            tool="web_search",
        ),
        SubTask(
            id="s2",
            assigned_agent="document",
            description="Synthesize the gathered research findings",
            dependencies=["s1"],
            status="pending",
        ),
    ]


class Recorder:
    def __init__(self) -> None:
        self.added: list[Any] = []
        self.steps: list[dict[str, Any]] = []


def service_with(
    monkeypatch: pytest.MonkeyPatch, queue: SubtaskQueue | None
) -> tuple[TaskSubmissionService, Recorder]:
    recorder = Recorder()
    session = SimpleNamespace(add=recorder.added.append)

    @asynccontextmanager
    async def fake_session(*_: Any, **__: Any) -> AsyncIterator[Any]:
        yield session

    async def get_or_create(**kwargs: Any) -> tuple[Any, bool]:
        task = SimpleNamespace(
            id=uuid4(),
            title=kwargs.get("title"),
            created_at=None,
            active_plan=None,
            status=None,
            plan_status=None,
            execution_trace=None,
        )
        return task, True

    async def passthrough(nodes: list[SubTask], *_: Any, **__: Any) -> list[SubTask]:
        return nodes

    async def no_op(*_: Any, **__: Any) -> None:
        return None

    async def record_step(_session: Any, **kwargs: Any) -> None:
        recorder.steps.append(kwargs)

    monkeypatch.setattr(task_submission, "tenant_session", fake_session)
    monkeypatch.setattr(TaskIdempotencyService, "get_or_create_task", staticmethod(get_or_create))
    monkeypatch.setattr(task_submission, "_attach_file_context", passthrough)
    monkeypatch.setattr(task_submission, "write_outbox_event", no_op)
    monkeypatch.setattr(task_submission, "emit_task_step", record_step)
    service = TaskSubmissionService(engine=None, gateway=None, queue=queue)  # type: ignore[arg-type]

    async def plan(*_: Any, **__: Any) -> list[SubTask]:
        return old_plan()

    async def no_standing(*_: Any, **__: Any) -> str:
        return ""

    monkeypatch.setattr(service, "_plan", plan)
    monkeypatch.setattr(service, "_standing_block", no_standing)
    monkeypatch.setattr(service, "_audit", no_op)
    return service, recorder


async def submit(
    service: TaskSubmissionService, instruction: str, deep_research: dict[str, Any] | None
) -> Any:
    return await service._submit(
        workspace_id=uuid4(),
        user_id=uuid4(),
        idempotency_key=str(uuid4()),
        instruction=instruction,
        title="Deep research: x",
        session_id=None,
        budget_usd=None,
        source="chat",
        deep_research=deep_research,
    )


# ------------------------------------------------------------------ 2: deep research task plan


@pytest.mark.parametrize("spec", [{}, {"sources": 30}])
async def test_a_deep_research_task_has_exactly_one_deep_research_step(
    monkeypatch: pytest.MonkeyPatch, spec: dict[str, Any]
) -> None:
    service, recorder = service_with(monkeypatch, None)

    result = await submit(service, "Research solar battery storage costs in India", spec)

    assert result.step_count == 1
    assert len(recorder.added) == 1
    row = recorder.added[0]
    assert "deep_research" in row.input_data
    assert "Synthesize" not in row.description and "web search" not in row.description.lower()
    assert str(row.assigned_agent.value) == "research"


async def test_without_the_deep_research_spec_the_planned_steps_are_kept(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    service, recorder = service_with(monkeypatch, None)

    result = await submit(service, "Research solar battery storage costs in India", None)

    assert result.step_count == 2
    assert all("deep_research" not in row.input_data for row in recorder.added)


def research_runtime(sites: int = 36) -> DeepResearchRuntime:
    chain = SimpleNamespace(active_names=lambda without=None: ["ddgs"])
    search = FakeSearch(sites=sites)

    async def run(request: Any, **_: Any) -> Any:
        hits, limited = await search(request.query)
        return SimpleNamespace(hits=hits, limited=limited)

    chain.run = run
    return DeepResearchRuntime(
        engine=None,
        gateway=FakeGateway(),
        web_search=SimpleNamespace(chain=chain),
        limits=ResearchLimits(),
        fetcher=FakeFetcher(),  # type: ignore[arg-type]
    )


def job_for(role: str = "research") -> SubtaskJob:
    return SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent=role,
    )


async def test_the_deep_research_step_runs_the_new_engine_and_reads_20_or_more_domains() -> None:
    agent = ResearchAgent(deep_research=research_runtime())

    result = await agent.execute_subtask(
        job_for(), {"deep_research": {}, "description": "Research solar batteries"}
    )

    assert result.status is AgentExecutionStatus.SUCCESS
    assert result.verification_proof["search_engine"] == "deep_research"
    assert result.verification_proof["domains"] >= 20
    assert len(result.citations) >= 20


async def test_when_the_engine_cannot_run_the_step_says_so_and_searches_nothing() -> None:
    gateway = FakeGateway()
    agent = ResearchAgent(model_gateway=gateway)  # no deep-research runtime wired

    result = await agent.execute_subtask(
        job_for(), {"deep_research": {}, "description": "Research solar batteries"}
    )

    assert result.status is AgentExecutionStatus.FAILED
    assert "not available" in result.summary and gateway.calls == []
    assert result.verification_proof["search_engine"] == "deep_research"


# ------------------------------------------------------------------ 3: two tasks at once


class SlowGateway(FakeGateway):
    """Deterministic interleaving: every model call yields to the other task first."""

    async def generate_text(self, **kwargs: Any) -> Any:
        await asyncio.sleep(0)
        text = "Findings about " + str(kwargs.get("contents", [""])[0])[:30]
        self.calls.append("text")
        return SimpleNamespace(text=text + " [1]", citations=[])


def native_agent(topic: str, gateway: Any) -> ResearchAgent:
    url = f"https://{topic}.example/page"
    service = service_for(
        Provider("ddgs", [[SearchHit(f"{topic} report", url, "snippet")]] * 2),
        PageFetcherFake({url: PageResult(url, True, text=f"{topic} body text")}),
    )
    return ResearchAgent(model_gateway=gateway, native_search=NativeResearch(service, gateway))


async def test_two_research_tasks_at_once_each_keep_their_own_sources() -> None:
    gateway = SlowGateway()
    solar = native_agent("solar", gateway)
    java = native_agent("java", gateway)

    first, second = await asyncio.gather(
        solar.execute_subtask(job_for(), {"query": "solar batteries"}),
        java.execute_subtask(job_for(), {"query": "java features"}),
    )

    assert first.status is second.status is AgentExecutionStatus.SUCCESS
    assert [c["url"] for c in first.citations] == ["https://solar.example/page"]
    assert [c["url"] for c in second.citations] == ["https://java.example/page"]
    assert "java" not in first.summary.split("## Sources")[1]
    assert "solar" not in second.summary.split("## Sources")[1]


async def test_a_native_research_task_and_a_deep_research_task_do_not_disturb_each_other() -> None:
    gateway = SlowGateway()
    native = native_agent("solar", gateway)
    deep = ResearchAgent(deep_research=research_runtime())

    quick, wide = await asyncio.gather(
        native.execute_subtask(job_for(), {"query": "solar batteries"}),
        deep.execute_subtask(job_for(), {"deep_research": {}, "description": "Java features"}),
    )

    assert quick.verification_proof["search_engine"] == "native_search:ddgs"
    assert [c["url"] for c in quick.citations] == ["https://solar.example/page"]
    assert wide.verification_proof["search_engine"] == "deep_research"
    assert len(wide.citations) >= 20
    assert all("solar.example" not in c["url"] for c in wide.citations)


async def test_a_research_task_and_a_document_task_at_once(tmp_path: Path) -> None:
    gateway = SlowGateway()
    research = native_agent("solar", gateway)
    document = DocumentAgent(artifacts_base_dir=tmp_path)

    found, made = await asyncio.gather(
        research.execute_subtask(job_for(), {"query": "solar batteries"}),
        document.execute_subtask(
            job_for("document"),
            {"filename": "notes.md", "content": "# Notes\nSolar storage costs fell."},
        ),
    )

    assert found.agent_role is SpecialistAgentRole.RESEARCH
    assert found.status is AgentExecutionStatus.SUCCESS
    assert made.agent_role is SpecialistAgentRole.DOCUMENT
    assert made.status is AgentExecutionStatus.SUCCESS and len(made.artifact_paths) == 1


async def test_two_research_tasks_of_one_workspace_use_separate_search_rate_buckets(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    """Both steps share a workspace; the per-minute search guard must be per task, not per
    workspace, or one task's searches would block the other's."""
    gateway = SlowGateway()
    seen: list[str] = []
    url = "https://a.example/p"
    service = service_for(
        Provider("ddgs", [[SearchHit("t", url, "s")]] * 4),
        PageFetcherFake({url: PageResult(url, True, text="body")}),
    )
    original = service.chain.run

    async def spy(request: Any, *, user_key: str, **kwargs: Any) -> Any:
        seen.append(user_key)
        return await original(request, user_key=user_key, **kwargs)

    monkeypatch.setattr(service.chain, "run", spy)
    research = NativeResearch(service, gateway)
    workspace: UUID = uuid4()
    one, two = uuid4(), uuid4()

    await asyncio.gather(
        research.research("a", workspace_id=workspace, task_id=one, ctx={}),
        research.research("b", workspace_id=workspace, task_id=two, ctx={}),
    )

    assert {f"task:{one}", f"task:{two}"} <= set(seen)


# ------------------------------------------------------------------ 3: queueing, never failing


def test_the_queue_label_says_how_many_tasks_are_ahead() -> None:
    assert queued_label(0) == "Queued: next in line"
    assert queued_label(1) == "Queued: 1 task ahead"
    assert queued_label(3) == "Queued: 3 tasks ahead"


async def test_a_second_task_queues_behind_a_busy_pool_and_says_so(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    queue = SubtaskQueue()
    queue.capacity = 1
    running = job_for()
    await queue.enqueue(running)
    assert await queue.dequeue(timeout_seconds=1) is running  # the first task now runs
    service, recorder = service_with(monkeypatch, queue)

    result = await submit(service, "Research solar battery storage costs in India", {})

    queued = [s for s in recorder.steps if str(s["step_id"]).startswith("queued-")]
    assert len(queued) == 1 and queued[0]["label"] == "Queued: next in line"
    assert queued[0]["state"] == "running"
    waiting = await queue.dequeue(timeout_seconds=1)
    assert waiting is not None and waiting.task_id == result.task_id and waiting.queued_step
    assert STEP_ID  # the only step of a chat-only deep research is the deep-research step


async def test_a_free_pool_adds_no_queued_step(monkeypatch: pytest.MonkeyPatch) -> None:
    queue = SubtaskQueue()
    queue.capacity = 3
    service, recorder = service_with(monkeypatch, queue)

    await submit(service, "Research solar battery storage costs in India", {})

    assert not [s for s in recorder.steps if str(s["step_id"]).startswith("queued-")]
    job = await queue.dequeue(timeout_seconds=1)
    assert job is not None and not job.queued_step


async def test_the_queue_serves_the_second_task_once_a_worker_is_free() -> None:
    queue = SubtaskQueue()
    queue.capacity = 1
    first, second = job_for(), job_for()
    await queue.enqueue(first)
    await queue.enqueue(second)
    assert queue.waiting_ahead() == 2

    got_first = await queue.dequeue(timeout_seconds=1)
    assert got_first is first
    assert queue.waiting_ahead() == 1  # the second task is the only one left ahead of a new one
    queue.task_done(first)
    assert await queue.dequeue(timeout_seconds=1) is second
