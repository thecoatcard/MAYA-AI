"""MAYA_RESEARCH_BACKEND: ordinary research steps run on MAYA's native search by default and never
burn Gemini grounding quota; a grounding 429 starts a 30-minute shared cooldown. Offline: fake
gateway, fake providers and pages, a fake clock."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from google.genai import errors as genai_errors

from app.agents.grounding_cooldown import COOLDOWN_SECONDS, GroundingCooldown
from app.agents.model_policy import configure, reset
from app.agents.schemas import AgentExecutionStatus
from app.agents.specialized.research import ResearchAgent
from app.search.fetch import PageResult
from app.search.models import ProviderUnavailableError, SearchHit
from app.search.report import NativeResearch
from app.tasks.queue import SubtaskJob
from tests.support.settings import make_settings
from tests.unit.test_search_service import FakeFetcher, Provider, service_for

URL = "https://a.example/p"


@pytest.fixture(autouse=True)
def _policy() -> Any:
    configure(make_settings())
    yield
    reset()


class Gateway:
    """Grounded calls raise 429 (or answer); plain calls write the native report."""

    def __init__(self, *, grounding_quota: bool = True) -> None:
        self.grounding_quota = grounding_quota
        self.calls: list[dict[str, Any]] = []

    @property
    def grounded(self) -> list[dict[str, Any]]:
        return [c for c in self.calls if c.get("enable_google_search")]

    async def generate_text(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if kwargs.get("enable_google_search"):
            if self.grounding_quota:
                raise genai_errors.APIError(429, {"error": {"status": "RESOURCE_EXHAUSTED"}})
            return SimpleNamespace(
                text="Grounded [1]", citations=[{"title": "G", "url": "https://g.example/1"}]
            )
        return SimpleNamespace(text="Chips grew [1].", citations=[])


def native_up() -> Any:
    hit = SearchHit("Chip report", URL, "snippet")
    return service_for(
        Provider("ddgs", [[hit], [hit]]), FakeFetcher({URL: PageResult(URL, True, text="Body")})
    )


def native_down() -> Any:
    return service_for(Provider("ddgs", [ProviderUnavailableError("network")] * 4))


def agent_for(
    gateway: Gateway, service: Any, backend: str, cooldown: GroundingCooldown | None = None
) -> ResearchAgent:
    return ResearchAgent(
        model_gateway=gateway,
        native_search=NativeResearch(service, gateway),
        research_backend=backend,
        cooldown=cooldown or GroundingCooldown(),
    )


def job() -> SubtaskJob:
    return SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="research",
    )


async def test_the_default_backend_is_native_and_never_calls_grounding() -> None:
    gateway = Gateway()
    result = await agent_for(gateway, native_up(), "native").execute_subtask(
        job(), {"query": "chip market"}
    )

    assert result.status is AgentExecutionStatus.SUCCESS
    assert result.verification_proof["search_engine"] == "native_search:ddgs"
    assert [c["url"] for c in result.citations] == [URL]
    assert gateway.grounded == []


async def test_native_mode_with_no_native_result_degrades_honestly_without_grounding() -> None:
    gateway = Gateway(grounding_quota=False)
    result = await agent_for(gateway, native_down(), "native").execute_subtask(
        job(), {"query": "chip market"}
    )

    assert result.status is AgentExecutionStatus.PARTIAL
    assert result.verification_proof["sources_unavailable"] is True
    assert result.verification_proof["search_engine"] == "native_search"
    assert result.citations == [] and gateway.calls == []  # not even one model call


async def test_auto_uses_grounding_only_when_native_found_nothing() -> None:
    up = Gateway(grounding_quota=False)
    first = await agent_for(up, native_up(), "auto").execute_subtask(job(), {"query": "q"})
    assert first.verification_proof["search_engine"] == "native_search:ddgs"
    assert up.grounded == []

    down = Gateway(grounding_quota=False)
    second = await agent_for(down, native_down(), "auto").execute_subtask(job(), {"query": "q"})
    assert second.verification_proof["search_engine"] == "google_search"
    assert len(down.grounded) == 1


async def test_a_grounding_429_makes_one_call_trips_the_cooldown_and_falls_back_to_native() -> None:
    gateway = Gateway()
    cooldown = GroundingCooldown()
    agent = agent_for(gateway, native_up(), "grounding", cooldown)

    result = await agent.execute_subtask(job(), {"query": "chip market"})

    assert len(gateway.grounded) == 1  # no second alias, no key loop of our own
    assert cooldown.active()
    assert result.verification_proof["search_engine"] == "native_search:ddgs"


async def test_during_the_cooldown_grounding_is_not_called_at_all() -> None:
    gateway = Gateway()
    cooldown = GroundingCooldown()
    cooldown.trip()

    for backend in ("grounding", "auto", "native"):
        agent = agent_for(gateway, native_up(), backend, cooldown)
        result = await agent.execute_subtask(job(), {"query": "chip market"})
        assert result.verification_proof["search_engine"] == "native_search:ddgs", backend

    assert gateway.grounded == []


async def test_the_chat_style_grounded_call_is_also_blocked_during_the_cooldown() -> None:
    gateway = Gateway()
    cooldown = GroundingCooldown()
    agent = ResearchAgent(model_gateway=gateway, cooldown=cooldown)

    first = await agent.gemini_grounded_answer("q", workspace_id=None)
    second = await agent.gemini_grounded_answer("q", workspace_id=None)

    assert first.status is second.status is AgentExecutionStatus.PARTIAL
    assert first.verification_proof["cause"] == "rate_limited"
    assert len(gateway.grounded) == 1  # the second request never reached the gateway


def test_the_cooldown_lasts_30_minutes_and_is_shared_by_default() -> None:
    now = [100.0]
    cooldown = GroundingCooldown(clock=lambda: now[0])
    assert not cooldown.active()
    cooldown.trip()
    now[0] += COOLDOWN_SECONDS - 1
    assert cooldown.active() and COOLDOWN_SECONDS == 1800
    now[0] += 2
    assert not cooldown.active()

    from app.agents import grounding_cooldown

    one = ResearchAgent(model_gateway=Gateway())
    two = ResearchAgent(model_gateway=Gateway())
    assert one.cooldown is two.cooldown is grounding_cooldown.shared


def test_the_setting_defaults_to_native_and_rejects_unknown_values() -> None:
    assert make_settings().research_backend == "native"
    with pytest.raises(ValueError):
        make_settings(research_backend="gemini")
