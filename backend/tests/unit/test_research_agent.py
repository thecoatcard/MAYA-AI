"""Unit tests for the research agent: web search is ONE Gemini grounded call.

A fake gateway stands in for Gemini: no network call, no real model. The removed multi-step
pipeline (plan sub-questions, fan-out search, synthesis) is no longer tested because it no longer
exists.
"""

from __future__ import annotations

import importlib
from datetime import date
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest
from google.genai import errors as genai_errors

from app.agents.model_policy import configure, reset
from app.agents.schemas import AgentExecutionStatus
from app.agents.specialized.research import ResearchAgent, render_citations
from app.agents.unsourced import NO_SOURCES_TEXT
from app.core.config import Settings
from app.gateway.exceptions import GatewayFallbackExhaustedError
from app.mcp.gateway import BUILTIN_MANIFEST
from app.tasks.queue import SubtaskJob
from tests.support.settings import make_settings

TODAY = date(2026, 11, 9)


def _job() -> SubtaskJob:
    return SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="research",
    )


def _src(i: int) -> dict[str, str]:
    return {"title": f"Source {i}", "url": f"https://example.test/{i}", "snippet": ""}


class FakeGateway:
    """Records every call; the answer is injected."""

    def __init__(self, outcome: Any = None) -> None:
        self.outcome = outcome or ("Claim one [1]. Claim two [2].", [_src(1), _src(2)])
        self.calls: list[dict[str, Any]] = []

    async def generate_text(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if isinstance(self.outcome, Exception):
            raise self.outcome
        text, sources = self.outcome
        return SimpleNamespace(text=text, citations=sources)


def _agent(gateway: FakeGateway) -> ResearchAgent:
    return ResearchAgent(model_gateway=gateway, today=lambda: TODAY)


@pytest.fixture(autouse=True)
def _policy() -> Any:
    configure(make_settings())
    yield
    reset()


@pytest.mark.asyncio
async def test_research_is_exactly_one_grounded_call_on_the_search_alias() -> None:
    gateway = FakeGateway()
    result = await _agent(gateway).execute_subtask(_job(), {"query": "state of EVs"})
    assert result.status == AgentExecutionStatus.SUCCESS
    assert len(gateway.calls) == 1  # never a plan, a fan-out or a synthesis call
    call = gateway.calls[0]
    assert call["enable_google_search"] is True
    assert call["alias"] == "search"
    assert call["fallback_alias"] is None  # no implicit fallback on lite grounding (429 quota)
    assert "2026-11-09" in call["system_instruction"]


@pytest.mark.asyncio
async def test_prompt_carries_the_user_requirements_and_language() -> None:
    gateway = FakeGateway()
    ctx = {
        "query": "solar prices",
        "requirements": {
            "user_request": "Research solar prices in simple Hinglish",
            "style": {"language": "Hinglish", "tone": "simple words"},
        },
    }
    await _agent(gateway).execute_subtask(_job(), ctx)
    call = gateway.calls[0]
    assert "USER REQUIREMENTS" in call["contents"][0]
    assert "simple Hinglish" in call["contents"][0]
    assert "Hinglish" in call["system_instruction"]


@pytest.mark.asyncio
async def test_fabricated_urls_and_citation_numbers_are_stripped() -> None:
    text = "Fact [1] and [7]. See https://invented.test/x and [a link](https://invented.test/y)."
    result = await _agent(FakeGateway((text, [_src(1)]))).execute_subtask(_job(), {"query": "q"})
    assert "invented.test" not in result.summary
    assert "[7]" not in result.summary
    assert "a link" in result.summary  # the markdown label survives, the link does not
    assert result.verification_proof["stripped_unverified_citations"] >= 3
    assert result.verification_proof["sources"] == ["https://example.test/1"]


def test_render_citations_renumbers_in_first_use_order_and_drops_unknown() -> None:
    sources = [_src(1), _src(2), _src(3)]
    text, cited, removed = render_citations("B [3][1], then [2, 9].", sources)
    assert text == "B [1][2], then [3]."
    assert [c["url"] for c in cited] == [
        "https://example.test/3",
        "https://example.test/1",
        "https://example.test/2",
    ]
    assert removed == 1


@pytest.mark.asyncio
async def test_sources_section_is_written_by_code_from_grounding_urls() -> None:
    result = await _agent(FakeGateway(("Fact [1].", [_src(2)]))).execute_subtask(
        _job(), {"query": "goal text"}
    )
    assert "## Sources\n1. Source 2 - https://example.test/2" in result.summary
    assert result.citations == [_src(2)]


@pytest.mark.asyncio
async def test_no_grounding_at_all_fails_honestly() -> None:
    gateway = FakeGateway(("confident but ungrounded text", []))
    result = await _agent(gateway).execute_subtask(_job(), {"query": "goal text"})
    assert result.status == AgentExecutionStatus.FAILED
    assert result.citations == []
    assert result.verification_proof["error"] == "no_grounding"


@pytest.mark.asyncio
async def test_no_backend_configured_fails_honestly() -> None:
    result = await ResearchAgent().execute_subtask(_job(), {"query": "goal text"})
    assert result.status == AgentExecutionStatus.FAILED
    assert result.citations == []


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        GatewayFallbackExhaustedError("all search keys exhausted"),
        genai_errors.APIError(429, {"error": {"status": "RESOURCE_EXHAUSTED"}}),
    ],
)
async def test_search_unavailable_degrades_instead_of_failing(error: Exception) -> None:
    result = await _agent(FakeGateway(error)).execute_subtask(_job(), {"query": "goal text"})
    assert result.status == AgentExecutionStatus.PARTIAL  # stored as a completed step
    assert result.summary == NO_SOURCES_TEXT
    assert result.citations == []
    proof = result.verification_proof
    assert proof["degraded"] is True and proof["sources_unavailable"] is True


@pytest.mark.asyncio
async def test_a_non_quota_provider_error_is_a_real_failure() -> None:
    error = genai_errors.APIError(400, {"error": {"status": "INVALID_ARGUMENT"}})
    result = await _agent(FakeGateway(error)).execute_subtask(_job(), {"query": "goal text"})
    assert result.status == AgentExecutionStatus.FAILED


# ------------------------------------------------------------------ Brave removal


def test_brave_symbols_are_gone() -> None:
    with pytest.raises(ModuleNotFoundError):
        importlib.import_module("app.mcp.connectors.brave")
    import app.mcp.connectors as connectors

    assert not hasattr(connectors, "BraveSearchConnector")
    assert not hasattr(ResearchAgent(), "brave_connector")
    # MAYA_BRAVE_API_KEY is back on purpose (owner request, native search): it only feeds the
    # optional Brave provider in app/search, never the old connector removed above.
    assert "brave_api_key" in Settings.model_fields


def test_brave_env_var_is_a_secret_key_for_the_search_provider_only(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setenv("MAYA_BRAVE_API_KEY", "brave-key-not-real")
    settings = make_settings()
    assert settings.brave_api_key is not None
    assert "brave-key-not-real" not in repr(settings)  # a SecretStr: never printed
    assert not hasattr(ResearchAgent(), "brave_connector")


def test_mcp_gateway_lists_no_brave_tool() -> None:
    names = {tool["name"] for tool in BUILTIN_MANIFEST}
    assert "web_search" in names
    assert not any("brave" in name.lower() for name in names)
    assert not any("brave" in str(tool).lower() for tool in BUILTIN_MANIFEST)


@pytest.mark.asyncio
async def test_a_deep_research_step_never_falls_back_to_gemini_grounding() -> None:
    # Owner bug: a deep research run finished with "search_engine: google_search / rate_limited".
    # Without the deep-research runtime the step fails closed; the grounded call is never made.
    gateway = FakeGateway()
    agent = ResearchAgent(model_gateway=gateway, today=lambda: TODAY)

    result = await agent.execute_subtask(
        _job(), {"deep_research": {"request": "best book"}, "description": "best book"}
    )

    assert result.status is AgentExecutionStatus.FAILED
    assert gateway.calls == []
    assert result.verification_proof.get("error") == "deep_research_unavailable"
