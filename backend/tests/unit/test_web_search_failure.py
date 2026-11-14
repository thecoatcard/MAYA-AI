"""A failed web search is reported with a plain cause, retried once, and never yields news."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any

import httpx
import pytest
from google.genai import errors as genai_errors

from app.agents.specialized.research import ResearchAgent
from app.core.provider_errors import failure_cause
from app.gateway.exceptions import GatewayFallbackExhaustedError
from app.services.loop_tools import web_search_result


class _Gateway:
    def __init__(self, error: Exception | None = None, *, text: str = "x") -> None:
        self.calls = 0
        self.error = error
        self.text = text

    async def generate_text(self, **_: Any) -> Any:
        self.calls += 1
        if self.error is not None:
            raise self.error
        return SimpleNamespace(text=self.text, citations=[])


def _exhausted_by(cause: Exception) -> GatewayFallbackExhaustedError:
    error = GatewayFallbackExhaustedError("exhausted")
    error.__cause__ = cause
    return error


def test_failure_cause_walks_the_wrapped_chain() -> None:
    assert failure_cause(_exhausted_by(httpx.ConnectError("dns"))) == "network"
    assert failure_cause(_exhausted_by(httpx.ReadTimeout("slow"))) == "timeout"
    assert failure_cause(_exhausted_by(genai_errors.ServerError(503, {}))) == "rate_limited"
    assert failure_cause(_exhausted_by(genai_errors.ServerError(500, {}))) == "server"
    assert failure_cause(GatewayFallbackExhaustedError("x")) == "unknown"


@pytest.mark.asyncio
async def test_a_network_failure_is_retried_once_and_reported_as_network() -> None:
    gateway = _Gateway(_exhausted_by(httpx.ConnectError("dns")))
    result = await ResearchAgent(model_gateway=gateway).grounded_answer("news", workspace_id=None)

    assert gateway.calls == 2  # the first attempt and one retry
    assert result.status.value == "failed"
    assert result.verification_proof["cause"] == "network"
    outcome = web_search_result(result)
    assert outcome["status"] == "unavailable"
    assert outcome["user_reason"] == "The search service could not be reached (network problem)."
    assert "Do NOT give headlines" in outcome["message"]
    assert "Answer from what you know" not in outcome["message"]


@pytest.mark.asyncio
async def test_a_quota_failure_stays_degraded_with_a_busy_reason() -> None:
    gateway = _Gateway(_exhausted_by(genai_errors.ClientError(429, {})))
    result = await ResearchAgent(model_gateway=gateway).grounded_answer("news", workspace_id=None)

    assert result.verification_proof["degraded"] is True
    assert "busy" in web_search_result(result)["user_reason"]


def test_an_answer_without_sources_is_a_failure_with_its_own_reason() -> None:
    outcome = web_search_result(None, error=httpx.ConnectError("dns"))
    assert "could not be reached" in outcome["user_reason"]
    outcome = web_search_result(None, error=RuntimeError("boom"))
    assert outcome["user_reason"] == "The web search failed."
