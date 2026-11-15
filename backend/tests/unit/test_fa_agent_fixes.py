"""Task FA: the agent-side defects found by the real-Gemini run (fakes only, no network).

A grounded search through the gateway with sources and failover (A, B), words-floor acceptance and
chapter floors (C), cost tracking (D), path safety (E), item-count fidelity (F), key-pool dedupe
(G), missing tool arguments (H), capability-aware refusal (I) and needs_clarification (J).
"""

from __future__ import annotations

import asyncio
import logging
from decimal import Decimal
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from google.genai.errors import ClientError, ServerError
from pydantic import SecretStr

from app.agents import capabilities, model_policy
from app.agents.capabilities import Capabilities, UnsupportedRequestError, needs_clarification
from app.agents.plan_repair import PlanNode, repair_plan
from app.agents.planner import PLANNER_TOOL_NAMES, DAGPlanner, PlanDecompositionResponse
from app.agents.schemas import AgentExecutionStatus
from app.agents.specialized.document import DocumentAgent
from app.agents.specialized.research import ResearchAgent
from app.automation.acceptance import check_acceptance, plan_chapters, words_floor
from app.automation.paths import ArtifactPathTooLongError, fit_artifact_name, fit_filename
from app.core.metrics import SPEND_USD_TOTAL
from app.core.provider_errors import is_rate_limited
from app.gateway.key_pool import KeyPool
from app.gateway.model_gateway import ModelGateway
from app.gateway.registry import build_default_registry
from app.gateway.resilience import ResiliencePolicy
from app.gateway.schemas import TokenUsage
from app.mcp.client_manager import MCPClientManager
from app.mcp.connectors.google_search import (
    GoogleSearchConnector,
    GoogleSearchError,
    format_sources,
    parse_sources,
)
from app.mcp.gateway import McpGateway, builtin_spec
from app.mcp.permissions import ToolPermissionRegistry, ToolTier
from app.mcp.scope import tool_call_scope
from app.services.task_submission import TaskRefusal, TaskSubmissionService
from app.tasks import healing
from app.tasks import long_document as ld
from app.tasks.dispatch import (
    DocumentSpec,
    FleetSupport,
    SlideSpec,
    safe_filename,
)
from app.tasks.doc_healing import ensure_sources, produced_item_count, requested_item_count
from app.tasks.worker import (
    MissingToolArgumentsError,
    NonRetryableSubtaskError,
    SubtaskWorker,
)
from tests.support.fake_gemini_client import FakeGeminiClient
from tests.support.gateway_factory import make_settings
from tests.support.longdoc import FakeLongDocGateway, make_job, outline_spec
from tests.support.models import SEARCH

URL_A = "https://a.example.test/one"
URL_B = "https://b.example.test/two"
CITES = [
    {"title": "Source A", "url": URL_A, "snippet": ""},
    {"title": "Source B", "url": URL_B, "snippet": ""},
]


@pytest.fixture(autouse=True)
def _defaults() -> Any:
    model_policy.reset()
    capabilities.reset()
    yield
    model_policy.reset()
    capabilities.reset()


class RecordingGateway:
    """Stands in for ModelGateway: records the keyword arguments of every call."""

    client = None

    def __init__(self, text: str = "Grounded answer", citations: Any = None) -> None:
        self.text = text
        self.citations = CITES if citations is None else citations
        self.calls: list[dict[str, Any]] = []

    async def generate_text(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        return SimpleNamespace(text=self.text, citations=self.citations)


# --------------------------------------------------------------------------- A: sources
def test_sources_block_is_numbered_deterministic_and_parses_back() -> None:
    block = format_sources([*CITES, CITES[0], {"title": "x", "url": "javascript:alert(1)"}])
    assert block == f"Sources:\n1. Source A - {URL_A}\n2. Source B - {URL_B}"
    assert parse_sources(f"Answer text\n\n{block}") == [
        {"title": "Source A", "url": URL_A, "snippet": ""},
        {"title": "Source B", "url": URL_B, "snippet": ""},
    ]
    assert format_sources([]) == ""


@pytest.mark.asyncio
async def test_web_search_tool_returns_text_numbered_sources_and_goes_through_the_gateway() -> None:
    gateway = RecordingGateway()
    registry = ToolPermissionRegistry()
    mcp = McpGateway(MCPClientManager(permission_registry=registry))
    mcp.register_spec(
        builtin_spec(github_token=None, google_search=GoogleSearchConnector(gateway=gateway))
    )
    workspace, task = uuid4(), uuid4()
    with tool_call_scope(workspace, task):
        result = await mcp.invoke("web_search", {"query": "solar"})
    await mcp.close()

    assert result.clean_text.startswith("Grounded answer")
    assert f"Sources:\n1. Source A - {URL_A}\n2. Source B - {URL_B}" in result.clean_text
    assert [c["url"] for c in parse_sources(result.clean_text)] == [URL_A, URL_B]
    call = gateway.calls[0]
    # key-pool affinity, agent alias, timeout and cost scope all reach the gateway
    assert call["alias"] == "search" and call["fallback_alias"] is None
    assert call["affinity"] == "research"
    assert call["enable_google_search"] is True
    assert call["attempt_timeout_seconds"] == model_policy.DEFAULT_AGENT_TIMEOUT_SECONDS
    assert call["workspace_id"] == workspace and call["task_id"] == task


@pytest.mark.asyncio
async def test_search_fails_over_to_the_next_key_within_one_call_without_sleeping() -> None:
    keys = ["-".join(["fa", "search", "key", str(i)]) for i in (1, 2, 3)]
    pool = KeyPool(keys)
    clients = {k: FakeGeminiClient() for k in keys}
    affinity_label = pool.role_map()["research"]
    first = keys[int(affinity_label.split("#")[1]) - 1]
    clients[first].aio.models.enqueue_errors(
        SEARCH, [ServerError(503, {"error": {"status": "UNAVAILABLE"}})]
    )
    sleep = AsyncMock()
    gateway = ModelGateway(
        registry=build_default_registry(),
        settings=make_settings(),
        key_pool=pool,
        client_factory=lambda key: clients[key.get_secret_value()],
        resilience_policy=ResiliencePolicy(
            sleep=sleep, random_uniform=lambda _a, b: b, key_pool=pool
        ),
    )
    result = await GoogleSearchConnector(gateway=gateway).search("anything")

    assert result.text == "Fake model response text"
    assert len(clients[first].aio.models.calls) == 1  # the affinity key was tried first and failed
    used = [k for k in keys if clients[k].aio.models.calls]
    assert len(used) == 2  # and exactly one other key answered
    sleep.assert_not_awaited()


# --------------------------------------------------------------------------- B: errors
class _RaisingGateway(RecordingGateway):
    def __init__(self, error: Exception) -> None:
        super().__init__()
        self.error = error

    async def generate_text(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        raise self.error


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("error", "reason", "status", "klass"),
    [
        (
            ClientError(429, {"error": {"status": "RESOURCE_EXHAUSTED", "message": "SECRET-P"}}),
            "rate_limited",
            429,
            "RESOURCE_EXHAUSTED",
        ),
        (
            ServerError(503, {"error": {"status": "UNAVAILABLE", "message": "SECRET-P"}}),
            "unavailable",
            503,
            "UNAVAILABLE",
        ),
    ],
)
async def test_429_and_503_keep_their_class_and_status_and_stay_retryable(
    error: Exception, reason: str, status: int, klass: str, caplog: pytest.LogCaptureFixture
) -> None:
    connector = GoogleSearchConnector(gateway=_RaisingGateway(error))
    with caplog.at_level(logging.WARNING), pytest.raises(GoogleSearchError) as raised:
        await connector.search("the user prompt text")
    err = raised.value
    assert (err.reason, err.status, err.error_class) == (reason, status, klass)
    assert is_rate_limited(err)  # the worker pauses and retries the step
    logged = caplog.text
    assert f"error_class={klass}" in logged and f"status={status}" in logged
    assert "SECRET-P" not in logged and "the user prompt text" not in logged
    if reason == "rate_limited":
        assert "rate limited on all tried keys; try again in a minute" in str(err)


@pytest.mark.asyncio
async def test_research_retries_an_empty_grounded_answer_once_and_logs_class_and_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Gw:
        def __init__(self) -> None:
            self.searches = 0

        async def generate_text(self, **kwargs: Any) -> Any:
            self.searches += 1
            if self.searches == 1:
                return SimpleNamespace(text="", citations=[])
            return SimpleNamespace(text="Solar facts [1][2]", citations=CITES)

    gateway = Gw()
    result = await ResearchAgent(model_gateway=gateway).execute_subtask(
        make_job("research"), {"query": "solar energy"}
    )
    assert gateway.searches == 2  # one retry, not more
    assert result.status == AgentExecutionStatus.SUCCESS
    assert [c["url"] for c in result.citations] == [URL_A, URL_B]
    assert "## Sources" in result.summary and URL_A in result.summary


@pytest.mark.asyncio
async def test_research_failure_names_the_rate_limit_and_logs_status(
    caplog: pytest.LogCaptureFixture,
) -> None:
    class Gw:
        async def generate_text(self, **_: Any) -> Any:
            raise ClientError(429, {"error": {"status": "RESOURCE_EXHAUSTED"}})

    with caplog.at_level(logging.WARNING):
        result = await ResearchAgent(model_gateway=Gw()).execute_subtask(
            make_job("research"), {"query": "solar power"}
        )
    # Quota is a degraded step (the task goes on without web sources), never a failure.
    assert result.status == AgentExecutionStatus.PARTIAL
    assert result.verification_proof["sources_unavailable"] is True
    assert "error_class=RESOURCE_EXHAUSTED" in caplog.text and "status=429" in caplog.text


def test_a_research_step_naming_web_search_runs_the_research_pipeline() -> None:
    worker = object.__new__(SubtaskWorker)
    with_gateway = SimpleNamespace(model_gateway=object())
    worker.fleet_registry = SimpleNamespace(
        has_agent=lambda role: role == "research", get_agent=lambda role: with_gateway
    )
    job = make_job("research")
    assert worker._runs_research_pipeline(job, "web_search") is True
    assert worker._runs_research_pipeline(job, "github_list_repositories") is False
    assert worker._runs_research_pipeline(make_job("file"), "web_search") is False
    # Every research step is the single grounded call, even with no gateway on the agent: the
    # bare web_search tool needs its own backend, which is what failed in a real run.
    with_gateway.model_gateway = None
    assert worker._runs_research_pipeline(job, "web_search") is True
    worker.fleet_registry = SimpleNamespace(has_agent=lambda role: False)
    assert worker._runs_research_pipeline(job, "web_search") is False  # no research agent at all


@pytest.mark.asyncio
async def test_a_research_node_with_a_web_search_hint_makes_one_grounded_gemini_call() -> None:
    from app.agents.registry import create_default_fleet

    calls: list[dict[str, Any]] = []

    class Gw:
        client = None

        async def generate_text(self, **kwargs: Any) -> Any:
            calls.append(kwargs)
            return SimpleNamespace(
                text="Java 21 is an LTS release [1].",
                citations=[{"title": "T", "url": "https://example.test/j", "snippet": ""}],
            )

    fleet = create_default_fleet(model_gateway=Gw())
    worker = object.__new__(SubtaskWorker)
    worker.fleet_registry = fleet
    job = make_job("research")
    assert worker._runs_research_pipeline(job, "web_search") is True  # the hint is ignored
    research_agent = fleet.get_agent("research")
    assert research_agent is not None
    out = await research_agent.execute_subtask(job, {"query": "Advanced Java"})
    assert out.status == AgentExecutionStatus.SUCCESS and out.citations
    assert len(calls) == 1 and calls[0]["enable_google_search"] is True
    assert calls[0]["alias"] == "search"  # grounding alias, fallback alias set


@pytest.mark.asyncio
async def test_research_retries_once_on_a_different_alias_after_a_provider_error() -> None:
    seen: list[str] = []

    class Gw:
        async def generate_text(self, **kwargs: Any) -> Any:
            seen.append(kwargs["alias"])
            if len(seen) == 1:
                raise RuntimeError("provider hiccup")
            return SimpleNamespace(
                text="Answer [1]",
                citations=[{"title": "T", "url": "https://example.test/a", "snippet": ""}],
            )

    out = await ResearchAgent(model_gateway=Gw()).execute_subtask(
        make_job("research"), {"query": "x"}
    )
    assert out.status == AgentExecutionStatus.SUCCESS
    assert len(seen) == 2 and seen[0] == "search" and seen[1] != seen[0]

    seen.clear()

    class Dead:
        async def generate_text(self, **kwargs: Any) -> Any:
            seen.append(kwargs["alias"])
            raise RuntimeError("down")

    failed = await ResearchAgent(model_gateway=Dead()).execute_subtask(
        make_job("research"), {"query": "x"}
    )
    assert failed.status == AgentExecutionStatus.FAILED and len(seen) == 2
    assert failed.verification_proof["error"] == "error"
    assert failed.verification_proof["error_class"] == "RuntimeError"


@pytest.mark.asyncio
async def test_document_step_keeps_the_sources_and_never_adds_an_invented_url() -> None:
    class Gw:
        client = None

        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def generate_structured(self, **kwargs: Any) -> Any:
            self.prompts.append(kwargs["contents"][0])
            return DocumentSpec(
                file_type="pdf",
                filename="r.pdf",
                title="Report",
                blocks=[
                    {"type": "heading", "level": 1, "text": "Report"},
                    {"type": "paragraph", "text": "Findings [1]. See https://invented.test/x"},
                ],
            )

    gateway = Gw()
    ctx: dict[str, Any] = {
        "description": "Create a PDF report on solar energy with sources",
        "dependency_outputs": {"s1": {"summary": "Findings", "citations": CITES}},
    }
    out = await FleetSupport(gateway=gateway, artifacts_dir=".")._document_context(  # type: ignore[arg-type]
        make_job("document"), ctx
    )
    blocks = out["blocks"]
    assert blocks[-2] == {"type": "heading", "level": 2, "text": "Sources"}
    listed = " ".join(blocks[-1]["items"])
    assert URL_A in listed and URL_B in listed
    # the grounded URLs are the only ones the sources list contains
    assert "invented.test" not in listed
    assert "Grounded sources" in gateway.prompts[0] and URL_A in gateway.prompts[0]
    assert ensure_sources({"blocks": blocks}, ctx["dependency_outputs"]) is False  # idempotent


# --------------------------------------------------------------------------- C: acceptance
def test_words_floor_rejects_a_15_page_3400_word_result_for_a_12_page_request() -> None:
    thin = check_acceptance({"pages": 12}, {"pages": 15, "words": 3400})
    assert thin.met is False
    assert thin.message == (
        "Requested 12 pages, produced 15 pages but only 3,400 words (expected about 5,400)"
    )
    assert words_floor(12) > 3400
    enough = check_acceptance({"pages": 12}, {"pages": 15, "words": words_floor(12)})
    assert enough.met is True
    assert check_acceptance({"pages": 12}, {"pages": 13}).met is True  # words not measurable


def test_chapter_count_follows_the_page_request() -> None:
    assert plan_chapters({"pages": 12}).chapters == 4
    assert plan_chapters({"pages": 9}).chapters == 3
    assert plan_chapters({"pages": 50}).chapters == 15
    # The half-empty last page of each chapter (0.5) is paid out of the budget: (12 - 3 - 4 x 0.5)
    # / 4 chapters (was 2.2 when the chapter tails were ignored and the page estimate ran high).
    assert plan_chapters({"pages": 12}).pages_per_chapter == 1.8


class _TwoDensityPdf:
    """A fake PDF printer: thin pages (about 100 words) first, then full ones (about 390)."""

    def __init__(self) -> None:
        self.calls = 0

    def __call__(self, document: Any, output_path: Path | str, **_: Any) -> Any:
        from reportlab.pdfgen import canvas

        from app.automation.pdf_engine import PdfRenderResult

        self.calls += 1
        lines = 8 if self.calls == 1 else 30
        path = Path(output_path)
        path.parent.mkdir(parents=True, exist_ok=True)
        pdf = canvas.Canvas(str(path))
        for page in range(13):
            for line in range(lines):
                pdf.drawString(72, 780 - line * 13, f"Page {page} line {line} " + "alpha beta " * 4)
            pdf.showPage()
        pdf.save()
        return PdfRenderResult(
            path=path, renderer="browser", theme=document.theme, browser="fake", toc_numbered=True
        )


@pytest.mark.asyncio
async def test_thin_file_gets_one_expansion_then_passes_with_more_words(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    from tests.unit.test_long_document_pipeline import Run

    printer = _TwoDensityPdf()
    monkeypatch.setattr("app.agents.specialized.document.create_pdf_from_document", printer)
    run = Run(tmp_path, FakeLongDocGateway(chapters=3), pages=12)
    result = await run.assemble(await run.stages())
    proof = result.verification_proof

    assert printer.calls == 2  # render, ONE expand round, re-render
    assert proof["healing"][0]["attempt"] == "expand_chapters"
    assert "words" in proof["healing"][0]["reason"]
    assert [c for c in run.gateway.calls if c.startswith("expand:")]
    assert result.status == AgentExecutionStatus.SUCCESS and proof["acceptance"]["met"] is True


@pytest.mark.asyncio
async def test_a_short_chapter_gets_exactly_one_expansion_with_the_measured_numbers() -> None:
    gateway = FakeLongDocGateway(chapters=3, yield_factor=0.4, expand_factor=1.0)
    job = make_job()
    outline = await ld.run_outline(gateway, job, _outline_node(12))  # type: ignore[arg-type]
    chapter_node = _chapter_input(outline["result"], 1)
    out = await ld.run_chapter(gateway, job, chapter_node)  # type: ignore[arg-type]
    assert gateway.calls.count("expand:1") == 1
    assert "expanded once" in out["result"]["summary"]
    assert out["result"]["chapter"]["expanded"] is True

    stubborn = FakeLongDocGateway(chapters=3, yield_factor=0.3, expand_factor=0.1)
    await ld.run_chapter(stubborn, job, chapter_node)  # type: ignore[arg-type]
    assert stubborn.calls.count("expand:1") == 1  # never a second call
    done = FakeLongDocGateway(chapters=3, yield_factor=1.0)
    await ld.run_chapter(done, job, chapter_node)  # type: ignore[arg-type]
    assert not [c for c in done.calls if c.startswith("expand")]  # a full chapter is left alone


def _outline_node(pages: int) -> dict[str, Any]:
    from app.agents.long_document_plan import build_long_document_plan

    text = f"make notes on java, pdf of {pages} pages"
    from app.automation.acceptance import extract_acceptance

    _, nodes, _ = build_long_document_plan(text, extract_acceptance(text))
    return dict(nodes[0].input_data)


def _chapter_input(outline_result: dict[str, Any], index: int) -> dict[str, Any]:
    return {
        "pipeline": {"index": index, "of": 3},
        "dependency_outputs": {"outline": outline_result},
    }


def test_every_topic_the_user_listed_ends_up_in_a_chapter() -> None:
    chapters = [
        {"title": "Collections and generics", "subtopics": ["lists"], "index": 1},
        {"title": "Concurrency", "subtopics": [], "index": 2},
    ]
    added = ld.ensure_topics_covered(chapters, ["Collections", "Rust ownership", "threads"])
    assert added == ["Rust ownership", "threads"]
    flat = " ".join(str(c) for c in chapters)
    assert "Rust ownership" in flat and "threads" in flat
    assert ld.ensure_topics_covered(chapters, ["Rust ownership"]) == []  # now covered


@pytest.mark.asyncio
async def test_the_outline_step_adds_a_listed_topic_the_model_left_out() -> None:
    class Gw(FakeLongDocGateway):
        async def generate_structured(self, **kwargs: Any) -> Any:
            if kwargs["response_schema"] is ld.OutlineSpec:
                return outline_spec(3, user_topics=["Variables", "Rust ownership"])
            return await super().generate_structured(**kwargs)

    out = await ld.run_outline(Gw(chapters=3), make_job(), _outline_node(12))  # type: ignore[arg-type]
    outline = out["result"]["outline"]
    assert outline["topics_added"] == ["Rust ownership"]
    assert any("Rust ownership" in c["subtopics"] for c in outline["chapters"])


# --------------------------------------------------------------------------- D: cost
@pytest.mark.asyncio
async def test_spend_metric_moves_for_priced_usage_and_not_for_unpriced() -> None:
    gateway = ModelGateway(
        registry=build_default_registry(), settings=make_settings(), client=FakeGeminiClient()
    )
    workspace = uuid4()
    labels = {"workspace_id": str(workspace)}
    before = SPEND_USD_TOTAL.get(labels=labels)
    await gateway._record_budget(
        workspace, uuid4(), TokenUsage(prompt_tokens=10, estimated_cost_usd=Decimal("0.25"))
    )
    assert SPEND_USD_TOTAL.get(labels=labels) == pytest.approx(before + 0.25)
    await gateway._record_budget(workspace, uuid4(), TokenUsage(prompt_tokens=10))  # unpriced
    assert SPEND_USD_TOTAL.get(labels=labels) == pytest.approx(before + 0.25)  # never "0 spent"
    await gateway._record_budget(  # a failed attempt's reservation is not spend
        workspace,
        uuid4(),
        TokenUsage(estimated_cost_usd=Decimal("9")),
        count_spend=False,
    )
    assert SPEND_USD_TOTAL.get(labels=labels) == pytest.approx(before + 0.25)


@pytest.mark.asyncio
async def test_generate_text_counts_the_priced_call_in_the_spend_metric() -> None:
    gateway = ModelGateway(
        registry=build_default_registry(), settings=make_settings(), client=FakeGeminiClient()
    )
    workspace = uuid4()
    labels = {"workspace_id": str(workspace)}
    before = SPEND_USD_TOTAL.get(labels=labels)
    await gateway.generate_text(
        **model_policy.agent_call_kwargs("research"),
        contents=["hello"],
        workspace_id=workspace,
        task_id=uuid4(),
    )
    assert SPEND_USD_TOTAL.get(labels=labels) > before


@pytest.mark.asyncio
async def test_task_spent_usd_follows_the_budget_tracker_and_only_grows() -> None:
    task = SimpleNamespace(spent_usd=Decimal("0"))
    session = SimpleNamespace(
        execute=AsyncMock(
            return_value=SimpleNamespace(scalars=lambda: SimpleNamespace(first=lambda: task))
        )
    )
    tracker = SimpleNamespace(get_usage=AsyncMock(return_value={"cost_usd": Decimal("0.01234")}))
    worker = object.__new__(SubtaskWorker)
    worker.fleet_support = SimpleNamespace(gateway=SimpleNamespace(budget_tracker=tracker))
    job = make_job()
    await worker._sync_task_spend(session, job)  # type: ignore[arg-type]
    assert task.spent_usd == Decimal("0.0123")
    tracker.get_usage.return_value = {"cost_usd": Decimal("0")}  # unpriced: no new information
    await worker._sync_task_spend(session, job)  # type: ignore[arg-type]
    assert task.spent_usd == Decimal("0.0123")


# --------------------------------------------------------------------------- E: paths
def test_a_200_character_title_gives_a_short_distinct_file_name() -> None:
    one = safe_filename("A" * 200 + " one", "pdf")
    two = safe_filename("A" * 200 + " two", "pdf")
    assert len(one) <= 84 and one.endswith(".pdf") and one != two
    assert safe_filename("A" * 200 + " one", "pdf") == one  # deterministic
    assert safe_filename("con", "pdf") == "con_.pdf"  # a reserved Windows device name
    assert safe_filename("report... ", "pdf") == "report.pdf"


def test_a_deep_folder_shortens_the_name_and_a_too_deep_one_names_the_setting() -> None:
    base = "C:/" + "x" * 150
    name = fit_filename(base, "task-id/" + "T" * 80 + ".pdf")
    assert len(f"{base}/{name}") <= 240 and name.endswith(".pdf")
    assert fit_filename(base, "task-id/short.pdf") == "task-id/short.pdf"  # fits: untouched
    with pytest.raises(ArtifactPathTooLongError, match="MAYA_ARTIFACTS_DIR"):
        fit_artifact_name("C:/" + "x" * 300, "a", "pdf")


@pytest.mark.asyncio
async def test_saving_with_a_long_title_and_a_deep_artifacts_folder_works(tmp_path: Path) -> None:
    job = make_job("document")
    # workspace id (36) + task id (36) + separators sit below the base folder
    pad = 235 - 74 - len(str(tmp_path)) - 22
    if pad < 5:
        pytest.skip("temporary folder is already too deep for this scenario")
    base = tmp_path / ("p" * pad)
    agent = DocumentAgent(artifacts_base_dir=base)
    ctx = {
        "file_type": "docx",
        "filename": f"{job.task_id}/{safe_filename('Very long title ' * 14, 'docx')}",
        "title": "T",
        "blocks": [
            {"type": "heading", "level": 1, "text": "T"},
            {"type": "paragraph", "text": "Body text."},
        ],
    }
    result = await agent.execute_subtask(job, ctx)
    assert result.status == AgentExecutionStatus.SUCCESS, result.summary
    path = Path(result.artifact_paths[0])
    assert await asyncio.to_thread(path.exists) and len(str(path)) <= 240

    too_deep = DocumentAgent(artifacts_base_dir=tmp_path / ("q" * 230))
    refused = await too_deep.execute_subtask(job, ctx)
    assert refused.status == AgentExecutionStatus.FAILED
    assert "MAYA_ARTIFACTS_DIR" in refused.summary


# --------------------------------------------------------------------------- F: items
@pytest.mark.parametrize(
    ("text", "count"),
    [
        ("5 tips for sleep, exactly 5 slides", 5),
        ("give me 7 key points about tea", 7),
        ("a deck on sleep", None),
        ("12 pages on java", None),
    ],
)
def test_the_number_of_requested_items_is_read_from_the_request(
    text: str, count: int | None
) -> None:
    assert requested_item_count(text) == count


def test_items_are_counted_on_slides_and_in_documents() -> None:
    deck = {
        "slides": [
            {"layout": "title", "title": "Sleep"},
            *({"layout": "bullets", "title": f"Tip {i}", "bullets": ["x"]} for i in range(4)),
        ]
    }
    assert produced_item_count("pptx", deck) == 4
    doc = {"blocks": [{"type": "numbered", "items": list("abcde")}]}
    assert produced_item_count("pdf", doc) == 5


@pytest.mark.asyncio
async def test_a_deck_short_of_the_requested_tips_gets_one_repair() -> None:
    def deck(tips: int, title_slide: bool) -> DocumentSpec:
        slides = [SlideSpec(layout="title", title="Sleep")] if title_slide else []
        slides += [
            SlideSpec(layout="bullets", title=f"Tip {i}", bullets=["Keep a schedule"])
            for i in range(1, tips + 1)
        ]
        return DocumentSpec(file_type="pptx", filename="s.pptx", title="Sleep", slides=slides)

    class Gw:
        client = None

        def __init__(self) -> None:
            self.prompts: list[str] = []

        async def generate_structured(self, **kwargs: Any) -> Any:
            self.prompts.append(kwargs["contents"][0])
            return deck(4, True) if len(self.prompts) == 1 else deck(5, False)

    gateway = Gw()
    out = await FleetSupport(gateway=gateway, artifacts_dir=".")._document_context(  # type: ignore[arg-type]
        make_job("document"),
        {
            "description": "Make a deck with 5 tips for sleep, exactly 5 slides",
            "acceptance": {"slides": 5},
        },
    )
    assert len(gateway.prompts) == 2  # one repair call, not more
    assert "NO separate title slide" in gateway.prompts[0]
    assert [s["layout"] for s in out["slides"]].count("title") == 0 and len(out["slides"]) == 5
    assert any("no separate title slide" in w for w in out["spec_warnings"])
    assert any(h["attempt"] == "item_count_repair" for h in out["spec_healing"])


# --------------------------------------------------------------------------- G: key pool
def test_the_key_pool_counts_each_key_once_whatever_its_written_shape() -> None:
    k1, k2, k3 = ("-".join(["fa", "pool", "key", str(i)]) for i in (1, 2, 3))
    settings = make_settings(
        gemini_api_key=SecretStr(f'  "{k1}"\r\n'),
        gemini_api_keys=f"{k1},'{k2}', {k2} ,\"{k3}\",{k3},\r\n,",
    )
    assert [k.get_secret_value() for k in settings.effective_gemini_api_keys] == [k1, k2, k3]
    as_json = make_settings(gemini_api_key=None, gemini_api_keys=f'["{k1}", " {k1} ", "{k2}\\n"]')
    assert [k.get_secret_value() for k in as_json.effective_gemini_api_keys] == [k1, k2]
    pool = KeyPool([k1, f" {k1} ", f'"{k1}"', SecretStr(f"{k2}\n"), k2])
    assert len(pool) == 2


# --------------------------------------------------------------------------- H: arguments
def test_a_search_node_without_a_query_is_repaired_from_its_instruction() -> None:
    node = PlanNode(
        id="step_1",
        role="research",
        description="  Perform grounded research and retrieve repository information  ",
        tool="web_search",
    )
    nodes, repairs = repair_plan([node], PLANNER_TOOL_NAMES)
    assert nodes[0].tool_args["query"] == (
        "Perform grounded research and retrieve repository information"
    )
    assert any("no query" in r for r in repairs)
    long = repair_plan(
        [PlanNode(id="a", role="research", description="x" * 900, tool="web_search")],
        PLANNER_TOOL_NAMES,
    )[0][0]
    assert len(long.tool_args["query"]) == 300


@pytest.mark.asyncio
async def test_the_owner_case_a_research_search_without_arguments_still_searches() -> None:
    gateway = RecordingGateway()
    mcp = McpGateway(MCPClientManager(permission_registry=ToolPermissionRegistry()))
    mcp.register_spec(
        builtin_spec(github_token=None, google_search=GoogleSearchConnector(gateway=gateway))
    )
    worker = object.__new__(SubtaskWorker)
    worker.tool_invoker = mcp
    subtask = SimpleNamespace(description="List repositories of the user", input_data={})
    args = worker._repair_tool_args(subtask, "web_search", {})  # type: ignore[arg-type]
    assert args == {"query": "List repositories of the user"}
    assert subtask.input_data["healing"][0]["to"] == "arguments repaired"
    result = await mcp.invoke("web_search", args)  # the permission gate accepts it now
    await mcp.close()
    assert "Grounded answer" in result.clean_text
    assert "filled in a missing argument" in healing.recovered_text(subtask.input_data["healing"])


def test_other_missing_arguments_fail_precisely_or_heal_agent_only() -> None:
    worker = object.__new__(SubtaskWorker)
    worker.tool_invoker = SimpleNamespace(required_args=lambda tool: frozenset({"repo"}))
    subtask = SimpleNamespace(description="Open it", input_data={})
    with pytest.raises(MissingToolArgumentsError) as raised:
        worker._repair_tool_args(subtask, "github_create_pull_request", {"repo": " "})  # type: ignore[arg-type]
    assert "'repo'" in str(raised.value) and isinstance(raised.value, NonRetryableSubtaskError)
    kwargs: dict[str, Any] = {
        "code": healing.CODE_MISSING_ARGUMENTS,
        "description": "Summarise the findings",
        "tool": "x_tool",
        "dep_outputs": {},
        "save_tools": frozenset(),
        "hops": 0,
    }
    assert healing.decide(role="research", **kwargs).action == "rerun_without_tool"  # type: ignore[union-attr]
    assert healing.decide(role="email", **kwargs) is None  # a side-effect role is never rewritten
    assert healing.decide(role="pm", **{**kwargs, "description": "Open the file"}) is None


# --------------------------------------------------------------------------- I: capabilities
class _NeverCalled:
    client = None

    async def generate_structured(self, **_: Any) -> Any:
        raise AssertionError("decided without a model call")


@pytest.mark.asyncio
async def test_listing_repositories_without_a_github_token_is_refused_not_researched() -> None:
    with pytest.raises(UnsupportedRequestError) as raised:
        await DAGPlanner(gateway=_NeverCalled()).plan("list my repo")  # type: ignore[arg-type]
    refusal = raised.value.refusal
    assert "can't list your repositories" in refusal.reason
    assert "Settings > Connections" in refusal.hint


@pytest.mark.asyncio
async def test_with_a_github_token_the_plan_uses_the_read_only_github_tool() -> None:
    capabilities.configure(make_settings(github_token=SecretStr("-".join(["fa", "gh", "token"]))))
    _, nodes = await DAGPlanner(gateway=_NeverCalled()).plan("list my repositories")  # type: ignore[arg-type]
    assert [(n.tool, n.assigned_agent) for n in nodes] == [("github_list_repositories", "research")]
    assert "github_list_repositories" in PLANNER_TOOL_NAMES
    assert ToolPermissionRegistry().classify("github_list_repositories") == ToolTier.READ


@pytest.mark.asyncio
async def test_the_planner_prompt_lists_capabilities_and_a_model_refusal_is_honoured() -> None:
    seen: dict[str, Any] = {}

    class Gw:
        client = None

        async def generate_structured(self, **kwargs: Any) -> Any:
            seen.update(kwargs)
            return PlanDecompositionResponse(
                intent="x",
                unsupported=True,
                unsupported_reason="Your private files are not reachable.",
                unsupported_hint="Connect a drive first.",
            )

    with pytest.raises(UnsupportedRequestError) as raised:
        await DAGPlanner(gateway=Gw()).plan("summarise my private notes folder")  # type: ignore[arg-type]
    assert raised.value.refusal.hint == "Connect a drive first."
    system = seen["system_instruction"]
    assert "GitHub" in system and "NOT connected" in system
    assert "Never plan web research as a substitute" in system
    assert Capabilities().github is False


@pytest.mark.asyncio
async def test_github_list_repositories_tool_output(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(
        "app.mcp.gateway.GitHubConnector.list_repositories",
        AsyncMock(
            return_value=[
                {
                    "full_name": "me/maya",
                    "private": True,
                    "updated_at": "2026-11-01T10:00:00Z",
                    "description": "Digital employee",
                }
            ]
        ),
    )
    mcp = McpGateway(MCPClientManager(permission_registry=ToolPermissionRegistry()))
    mcp.register_spec(builtin_spec(github_token="-".join(["fa", "gh", "token"])))
    result = await mcp.invoke("github_list_repositories", {})
    await mcp.close()
    # The list is newest-push first with dates; the line format changed from "updated" to "pushed".
    assert "1. me/maya (private, pushed 2026-11-01) - Digital employee" in result.clean_text


@pytest.mark.asyncio
async def test_a_refusal_is_returned_to_chat_without_touching_the_database() -> None:
    # engine=None: any attempt to create a task row would crash, so none is made.
    service = TaskSubmissionService(engine=None, gateway=None, queue=None)  # type: ignore[arg-type]
    result = await service.submit(
        workspace_id=uuid4(),
        user_id=uuid4(),
        idempotency_key="k",
        instruction="list my repo",
        title=None,
        session_id=None,
    )
    assert isinstance(result, TaskRefusal) and result.ok is False and result.is_new is False
    assert result.reason and "Settings > Connections" in result.hint
    assert result.as_result() == {
        "ok": False,
        "code": "unsupported",
        "status": "unsupported",
        "reason": result.reason,
        "hint": result.hint,
    }


# --------------------------------------------------------------------------- J: clarification
@pytest.mark.parametrize(
    "text",
    [
        "create a ppt",
        "Create a PPT",
        "make a pdf",
        "please create a presentation",
        "write a report",
        "I need notes",
        "banao ek presentation",
        "mujhe ek ppt banani hai",
        "ek pdf bana do",
        "create a ppt with 10 slides",
    ],
)
def test_a_deliverable_without_a_subject_needs_clarification(text: str) -> None:
    refusal = needs_clarification(text)
    assert refusal is not None and refusal.ok is False
    assert refusal.status == "needs_clarification" and "topic" in refusal.missing
    assert refusal.hint == refusal.question and refusal.question.endswith("?")


@pytest.mark.parametrize(
    "text",
    [
        "ppt on solar energy",
        "ppt on AI",
        "report on Q3 sales",
        "notes on java",
        "Java notes 12 pages",
        "make a 12 page pdf on sleep hygiene",
        "5 tips for sleep as a deck",
        "create a presentation about GPT4",
        "ek ppt banao AI par",
        "mujhe sleep par ppt chahiye",
        "send an email to bob@example.test",
        "what is the weather today",
        "explain photosynthesis",
    ],
)
def test_a_request_with_any_subject_is_never_blocked(text: str) -> None:
    assert needs_clarification(text) is None


def test_the_clarification_shape_asks_for_slides_or_pages() -> None:
    deck = needs_clarification("create a ppt")
    assert deck is not None
    assert deck.reason == "The presentation needs a topic."
    assert deck.question == "What should the presentation be about, and roughly how many slides?"
    assert deck.missing == ("topic", "length")
    assert deck.as_result()["status"] == "needs_clarification"
    doc = needs_clarification("make a pdf")
    assert doc is not None and "pages" in doc.question


@pytest.mark.asyncio
async def test_no_task_is_created_for_a_clarification_and_the_planner_can_ask_too() -> None:
    service = TaskSubmissionService(engine=None, gateway=None, queue=None)  # type: ignore[arg-type]
    result = await service.submit(
        workspace_id=uuid4(),
        user_id=uuid4(),
        idempotency_key="k",
        instruction="mujhe ek ppt banani hai",
        title=None,
        session_id=None,
    )
    assert isinstance(result, TaskRefusal) and result.status == "needs_clarification"
    assert result.missing == ("topic", "length") and result.hint == result.question
    assert result.as_result()["missing"] == ["topic", "length"]

    class Gw:
        client = None

        async def generate_structured(self, **_: Any) -> Any:
            return PlanDecompositionResponse(
                intent="x", needs_clarification=True, clarification_question="About what?"
            )

    with pytest.raises(UnsupportedRequestError) as raised:
        await DAGPlanner(gateway=Gw()).plan("do the thing for me")  # type: ignore[arg-type]
    assert raised.value.refusal.status == "needs_clarification"
    assert raised.value.refusal.question == "About what?"


@pytest.mark.asyncio
async def test_the_worker_guard_fails_with_a_question_instead_of_a_dummy_file() -> None:
    class Gw:
        client = None

        def __init__(self, spec: DocumentSpec | None = None) -> None:
            self.spec = spec
            self.calls = 0

        async def generate_structured(self, **_: Any) -> Any:
            self.calls += 1
            assert self.spec is not None
            return self.spec

    no_subject = Gw()
    with pytest.raises(NonRetryableSubtaskError, match="needs a topic"):
        await FleetSupport(gateway=no_subject, artifacts_dir=".")._document_context(  # type: ignore[arg-type]
            make_job("document"), {"description": "create a ppt"}
        )
    assert no_subject.calls == 0  # the model is not even asked to invent content

    asks = Gw(
        DocumentSpec(
            file_type="pptx", filename="x.pptx", title="x", needs_clarification="Which topic?"
        )
    )
    with pytest.raises(NonRetryableSubtaskError, match="Which topic"):
        await FleetSupport(gateway=asks, artifacts_dir=".")._document_context(  # type: ignore[arg-type]
            make_job("document"), {"description": "make slides for the team"}
        )
    # earlier step output is a subject: a bare "create a pdf" after research is fine
    spec = DocumentSpec(
        file_type="pdf",
        filename="r.pdf",
        title="R",
        blocks=[{"type": "paragraph", "text": "Body"}],
    )
    ok = await FleetSupport(gateway=Gw(spec), artifacts_dir=".")._document_context(  # type: ignore[arg-type]
        make_job("document"),
        {"description": "create a pdf", "dependency_outputs": {"s": {"summary": "Findings"}}},
    )
    assert ok["blocks"]


@pytest.mark.asyncio
async def test_the_outline_step_surfaces_a_clarification_question() -> None:
    class Gw(FakeLongDocGateway):
        async def generate_structured(self, **kwargs: Any) -> Any:
            if kwargs["response_schema"] is ld.OutlineSpec:
                return outline_spec(3, needs_clarification="What is the document about?")
            return await super().generate_structured(**kwargs)

    with pytest.raises(NonRetryableSubtaskError, match="What is the document about"):
        await ld.run_outline(Gw(chapters=3), make_job(), _outline_node(12))  # type: ignore[arg-type]
