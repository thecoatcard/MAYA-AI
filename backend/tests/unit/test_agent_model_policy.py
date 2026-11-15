"""One model alias for every agent and task call (default lite; live refused; optional fallback)."""

from __future__ import annotations

from collections.abc import Iterator
from datetime import date
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from google.genai.errors import APIError
from pydantic import SecretStr, ValidationError

from app.agents import model_policy
from app.agents.long_document_plan import build_long_document_plan
from app.agents.planner import DAGPlanner, PlanDecompositionResponse, SubTaskPlanItem
from app.agents.specialized.research import ResearchAgent
from app.automation.acceptance import extract_acceptance
from app.gateway.exceptions import GatewayFallbackExhaustedError
from app.gateway.key_pool import KeyPool
from app.gateway.model_gateway import ModelGateway
from app.gateway.registry import build_default_registry
from app.gateway.resilience import ResiliencePolicy
from app.tasks import long_document as ld
from app.tasks.dispatch import EmailSpec, FleetSupport
from app.tasks.executor import execute_subtask_with_agent
from app.tasks.queue import SubtaskJob
from tests.support.fake_gemini_client import FakeGeminiClient
from tests.support.longdoc import FakeLongDocGateway, make_job
from tests.support.models import DISTINCT_LAST_RESORT_MODEL, HEAVY, LAST_RESORT_OVERRIDES, LITE
from tests.support.settings import make_settings


@pytest.fixture(autouse=True)
def _reset_policy() -> Iterator[None]:
    model_policy.reset()
    yield
    model_policy.reset()


# --------------------------------------------------------------------------- settings
def test_default_agent_alias_is_lite_with_no_fallback() -> None:
    settings = make_settings()
    assert settings.agent_model_alias == "lite" and settings.agent_fallback_alias == ""
    policy = model_policy.configure(settings)
    assert (policy.alias, policy.fallback_alias) == ("lite", None)
    kwargs = model_policy.agent_call_kwargs("planner")
    assert kwargs == {
        "alias": "lite",
        "allow_fallback": False,
        "fallback_alias": None,
        "attempt_timeout_seconds": 150.0,
        "affinity": "planner",
    }


@pytest.mark.parametrize("alias", ["lite", "orchestrator", "heavy"])
def test_any_enabled_rest_text_alias_is_accepted(alias: str) -> None:
    assert make_settings(agent_model_alias=alias).agent_model_alias == alias


@pytest.mark.parametrize("alias", ["live", "live-thinking"])
@pytest.mark.parametrize("field", ["agent_model_alias", "agent_fallback_alias"])
def test_live_is_rejected_with_a_clear_message(alias: str, field: str) -> None:
    with pytest.raises(ValidationError) as exc:
        make_settings(**{field: alias})
    text = str(exc.value)
    assert "live is a stateful audio WebSocket API, use it for voice only" in text


@pytest.mark.parametrize("alias", ["embed", "research", "tts", "nope"])
def test_other_aliases_are_rejected(alias: str) -> None:
    with pytest.raises(ValidationError, match="enabled REST text alias"):
        make_settings(agent_model_alias=alias)


def test_the_alias_can_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("MAYA_AGENT_MODEL_ALIAS", "orchestrator")
    monkeypatch.setenv("MAYA_AGENT_FALLBACK_ALIAS", "heavy")
    policy = model_policy.configure(make_settings())
    assert (policy.alias, policy.fallback_alias) == ("orchestrator", "heavy")
    assert model_policy.agent_call_kwargs()["allow_fallback"] is True


def test_key_attempt_limit_is_bounded_between_one_and_seventeen() -> None:
    assert make_settings().gemini_max_key_attempts == 4
    assert make_settings(gemini_max_key_attempts=17).gemini_max_key_attempts == 17
    for bad in (0, 18):
        with pytest.raises(ValidationError):
            make_settings(gemini_max_key_attempts=bad)


def test_lite_registry_values_match_the_documented_pricing() -> None:
    spec = build_default_registry().get("lite")
    assert spec.model_id == LITE and spec.transport == "rest"
    assert (str(spec.cost_per_1m_input), str(spec.cost_per_1m_output)) == ("0.30", "2.50")
    assert spec.context_input_limit == 1_048_576 and spec.context_output_limit == 65_536


# --------------------------------------------------------------------------- gateway behaviour
def _gateway(keys: list[str], clients: dict[str, FakeGeminiClient]) -> ModelGateway:
    pool = KeyPool(keys)
    return ModelGateway(
        registry=build_default_registry(),
        settings=make_settings(gemini_api_keys=[SecretStr(k) for k in keys]),
        resilience_policy=ResiliencePolicy(sleep=AsyncMock(), key_pool=pool),
        key_pool=pool,
        client_factory=lambda key: clients[key.get_secret_value()],
    )


def _keys() -> list[str]:
    return ["-".join(["policy", "test", "key", str(i)]) for i in (1, 2)]


@pytest.mark.asyncio
async def test_without_a_fallback_alias_agent_calls_never_fall_back() -> None:
    keys = _keys()
    clients = {k: FakeGeminiClient() for k in keys}
    for c in clients.values():
        c.aio.models.enqueue_errors(LITE, [APIError(429, "x")])
    gateway = _gateway(keys, clients)
    model_policy.configure(make_settings())
    with pytest.raises(GatewayFallbackExhaustedError):
        await gateway.generate_text(contents=["hi"], **model_policy.agent_call_kwargs("planner"))
    models = [c["model"] for cl in clients.values() for c in cl.aio.models.calls]
    assert models == [LITE] * 2  # no orchestrator attempt at all


@pytest.mark.asyncio
async def test_with_a_fallback_alias_agent_calls_fall_back_to_exactly_that_alias() -> None:
    keys = _keys()
    clients = {k: FakeGeminiClient() for k in keys}
    for c in clients.values():
        c.aio.models.enqueue_errors(LITE, [APIError(429, "x")])
        c.aio.models.default_text = "from the fallback"
    gateway = _gateway(keys, clients)
    model_policy.configure(make_settings(agent_fallback_alias="heavy"))
    response = await gateway.generate_text(
        contents=["hi"], **model_policy.agent_call_kwargs("planner")
    )
    assert response.alias_used == "heavy" and response.was_fallback is True
    models = {c["model"] for cl in clients.values() for c in cl.aio.models.calls}
    assert models == {LITE, HEAVY}  # never the chain's orchestrator


# --------------------------------------------------------------------------- call sites
class Recorder:
    """A gateway that records every call's keyword arguments and answers minimally."""

    client = None

    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []

    async def generate_structured(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        schema = kwargs["response_schema"]
        if schema is PlanDecompositionResponse:
            return PlanDecompositionResponse(
                intent="x",
                subtasks=[SubTaskPlanItem(id="a", assigned_agent="research", description="A")],
            )
        return SimpleNamespace()

    async def generate_text(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if kwargs.get("enable_google_search"):
            return SimpleNamespace(
                text="finding",
                citations=[{"title": "T", "url": "https://example.test/a", "snippet": ""}],
            )
        return SimpleNamespace(text="Report body [1].", citations=[])


def _job(agent: str) -> SubtaskJob:
    return SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent=agent,
    )


def _assert_policy(calls: list[dict[str, Any]], alias: str) -> None:
    assert calls, "the call site made no model call"
    for call in calls:
        assert call["alias"] == alias
        assert call["allow_fallback"] is False and call["fallback_alias"] is None
        assert call.get("affinity")


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["lite", "orchestrator"])
async def test_planner_research_email_document_and_executor_use_the_policy_alias(
    alias: str, tmp_path: Path
) -> None:
    model_policy.configure(make_settings(agent_model_alias=alias))
    rec = Recorder()

    await DAGPlanner(gateway=rec).plan("Research the topic")  # type: ignore[arg-type]
    _assert_policy(rec.calls, alias)
    assert {c["affinity"] for c in rec.calls} == {"planner"}

    rec = Recorder()
    agent = ResearchAgent(model_gateway=rec, today=lambda: date(2026, 11, 9))
    await agent.execute_subtask(_job("research"), {"query": "state of EVs"})
    # Web search is ONE grounded call on the dedicated search alias; the agent alias (3.5 flash
    # lite, no grounding quota) is NOT an implicit fallback.
    assert len(rec.calls) == 1 and rec.calls[0]["enable_google_search"] is True
    assert rec.calls[0]["alias"] == "search" and rec.calls[0]["fallback_alias"] is None
    assert rec.calls[0]["allow_fallback"] is False
    assert rec.calls[0]["affinity"] == "research"

    rec = Recorder()
    support = FleetSupport(gateway=rec, artifacts_dir=tmp_path)  # type: ignore[arg-type]
    await support._structured(_job("email"), "p", EmailSpec, "s", affinity="email")
    _assert_policy(rec.calls, alias)
    assert rec.calls[0]["affinity"] == "email"
    rec.calls.clear()
    await support._structured(_job("document"), "p", EmailSpec, "s")  # healing / repair default
    _assert_policy(rec.calls, alias)
    assert rec.calls[0]["affinity"] == "document"

    rec = Recorder()
    out = await execute_subtask_with_agent(
        _job("executor"),
        {"description": "Summarise", "executor": "text"},
        gateway=rec,  # type: ignore[arg-type]
    )
    assert out
    _assert_policy(rec.calls, alias)
    assert rec.calls[0]["affinity"] == "executor"


class _SpyLongDoc(FakeLongDocGateway):
    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self.kwargs_seen: list[dict[str, Any]] = []

    async def generate_structured(self, **kwargs: Any) -> Any:
        self.kwargs_seen.append({k: v for k, v in kwargs.items() if k != "contents"})
        return await super().generate_structured(**kwargs)

    async def generate_text(self, **kwargs: Any) -> Any:
        # A chapter is a text (Markdown) call now: the same policy kwargs must reach it.
        self.kwargs_seen.append({k: v for k, v in kwargs.items() if k != "contents"})
        return await super().generate_text(**kwargs)


@pytest.mark.asyncio
@pytest.mark.usefixtures("nine_chapters")
async def test_long_document_outline_and_chapters_use_the_policy_alias_and_spread_over_keys() -> (
    None
):
    model_policy.configure(make_settings())
    gateway = _SpyLongDoc(chapters=9)
    text = "make a notes on java, pdf of 50 pages"
    _, nodes, _ = build_long_document_plan(text, extract_acceptance(text))
    job = make_job()
    outline = await ld.run_outline(gateway, job, nodes[0].input_data)  # type: ignore[arg-type]
    for node in nodes[1:4]:
        data = {**node.input_data, "dependency_outputs": {"outline": outline["result"]}}
        await ld.run_chapter(gateway, job, data)  # type: ignore[arg-type]
    seen = gateway.kwargs_seen
    # outline + 3 chapters, plus at most one floor-expansion call per chapter
    assert 4 <= len(seen) <= 7
    _assert_policy(seen, "lite")
    assert seen[0]["affinity"] == "longdoc"
    assert {c["affinity"] for c in seen[1:]} <= {"chapter:1", "chapter:2", "chapter:3"}
    assert {c["affinity"] for c in seen[1:]} >= {"chapter:1"}
    # an explicit output ceiling so a long chapter is never cut short by a smaller default
    assert {c["max_output_tokens"] for c in seen} == {model_policy.LONG_OUTPUT_TOKENS}


def test_chat_keeps_its_own_alias_and_does_not_use_the_agent_policy() -> None:
    root = Path(__file__).resolve().parents[2] / "app"
    chat = (root / "services" / "chat_service.py").read_text(encoding="utf-8")
    assert "model_policy" not in chat and "agent_call_kwargs" not in chat
    assert "orchestrator" in chat
    for path in (root / "agents").rglob("*.py"):
        if path.name == "registry.py":
            continue
        source = path.read_text(encoding="utf-8")
        assert 'alias="orchestrator"' not in source and 'alias="heavy"' not in source, path
    for path in (root / "tasks").rglob("*.py"):
        source = path.read_text(encoding="utf-8")
        assert 'alias="orchestrator"' not in source and 'alias="heavy"' not in source, path


@pytest.mark.asyncio
async def test_executor_result_records_model_alias_and_key_label_only() -> None:
    class Labelled(Recorder):
        async def generate_text(self, **kwargs: Any) -> Any:
            return SimpleNamespace(text="hello", citations=[], alias_used="lite", key_label="key#4")

    out = await execute_subtask_with_agent(
        _job("executor"),
        {"description": "Summarise", "executor": "text"},
        gateway=Labelled(),  # type: ignore[arg-type]
    )
    assert out["result"]["model_alias"] == "lite" and out["result"]["key_label"] == "key#4"


def test_grounding_never_defaults_to_the_lite_model() -> None:
    # The default registry maps ``search`` and ``orchestrator`` to the same model id, so the
    # "alternate alias is a different model" intent is exercised on a registry where they differ.
    registry = build_default_registry(LAST_RESORT_OVERRIDES)
    model_policy.configure(make_settings())
    first = model_policy.search_call_kwargs("research")
    second = model_policy.alternate_search_call_kwargs("research")
    assert registry.get(first["alias"]).model_id == DISTINCT_LAST_RESORT_MODEL
    assert first["fallback_alias"] is None and first["allow_fallback"] is False
    lite_id = registry.get("lite").model_id
    assert registry.get(second["alias"]).model_id not in (lite_id, DISTINCT_LAST_RESORT_MODEL)
    assert second["alias"] != first["alias"] and second["allow_fallback"] is False


def test_an_explicit_search_fallback_is_still_honoured() -> None:
    model_policy.configure(make_settings(agent_search_fallback_alias="orchestrator"))
    kwargs = model_policy.search_call_kwargs()
    assert kwargs["fallback_alias"] == "orchestrator" and kwargs["allow_fallback"] is True
    assert model_policy.alternate_search_call_kwargs()["alias"] == "heavy"
