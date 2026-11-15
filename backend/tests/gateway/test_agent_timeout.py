"""Agent calls get their own (longer) per-attempt cap; chat keeps the short one (fakes only)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from google.genai.errors import APIError
from pydantic import SecretStr, ValidationError

from app.agents import model_policy
from app.gateway.exceptions import GatewayFallbackExhaustedError
from app.gateway.key_pool import KeyPool
from app.gateway.model_gateway import ModelGateway
from app.gateway.registry import build_default_registry
from app.gateway.resilience import ResiliencePolicy
from tests.support.fake_gemini_client import FakeGeminiClient
from tests.support.models import LITE
from tests.support.settings import make_settings

CHAT_CAP = 0.05  # stands in for the 30 s chat cap
AGENT_CAP = 1.0  # stands in for the 150 s agent cap
SLOW = 0.2  # stands in for a legitimate 45 s call


@pytest.fixture(autouse=True)
def _reset() -> Any:
    model_policy.reset()
    yield
    model_policy.reset()


def _keys(n: int) -> list[str]:
    return ["-".join(["timeout", "test", "key", str(i)]) for i in range(1, n + 1)]


class SlowClient(FakeGeminiClient):
    """A fake whose first ``slow_calls`` requests take ``SLOW`` seconds."""

    def __init__(self, slow_calls: int = 1000, delay: float = SLOW) -> None:
        super().__init__()
        self.delay = delay
        self.slow_calls = slow_calls
        original = self.aio.models.generate_content

        async def slow(**kwargs: Any) -> Any:
            if len(self.aio.models.calls) < self.slow_calls:
                self.aio.models.calls.append({"method": "slow", "config": kwargs.get("config")})
                await asyncio.sleep(self.delay)
                return await original(**kwargs)
            return await original(**kwargs)

        self.aio.models.generate_content = slow  # type: ignore[method-assign]


def _gateway(clients: dict[str, FakeGeminiClient], sleep: AsyncMock) -> ModelGateway:
    keys = list(clients)
    pool = KeyPool(keys)
    return ModelGateway(
        registry=build_default_registry(),
        settings=make_settings(gemini_api_keys=[SecretStr(k) for k in keys]),
        resilience_policy=ResiliencePolicy(
            sleep=sleep, key_pool=pool, attempt_timeout_seconds=CHAT_CAP
        ),
        key_pool=pool,
        client_factory=lambda key: clients[key.get_secret_value()],
    )


def _agent_kwargs() -> dict[str, Any]:
    kwargs = model_policy.agent_call_kwargs("planner")
    kwargs["attempt_timeout_seconds"] = AGENT_CAP
    return kwargs


@pytest.mark.asyncio
async def test_a_slow_call_completes_for_an_agent_and_times_out_for_chat() -> None:
    key = _keys(1)[0]
    clients: dict[str, FakeGeminiClient] = {key: SlowClient()}
    gateway = _gateway(clients, AsyncMock())
    response = await gateway.generate_text(contents=["hi"], **_agent_kwargs())
    assert response.text  # the agent cap (long) let the slow call finish

    clients2: dict[str, FakeGeminiClient] = {key: SlowClient()}
    chat = _gateway(clients2, AsyncMock())
    with pytest.raises(GatewayFallbackExhaustedError):
        await chat.generate_text("lite", ["hi"], allow_fallback=False)  # no per-call override


@pytest.mark.asyncio
async def test_fast_429_still_switches_instantly_under_the_agent_cap() -> None:
    keys = _keys(3)
    clients: dict[str, FakeGeminiClient] = {k: FakeGeminiClient() for k in keys}
    clients[keys[0]].aio.models.enqueue_errors(LITE, [APIError(429, "x")])
    sleep = AsyncMock()
    gateway = _gateway(clients, sleep)
    response = await gateway.generate_text(contents=["hi"], **_agent_kwargs())
    assert response.key_label == "key#2"
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_a_hung_agent_attempt_fails_over_at_the_agent_cap_not_the_chat_cap() -> None:
    keys = _keys(2)
    clients: dict[str, FakeGeminiClient] = {
        keys[0]: SlowClient(delay=30.0),  # hangs on its first call
        keys[1]: FakeGeminiClient(),
    }
    sleep = AsyncMock()
    gateway = _gateway(clients, sleep)
    started = asyncio.get_running_loop().time()
    kwargs = _agent_kwargs()
    kwargs["attempt_timeout_seconds"] = 0.3
    response = await gateway.generate_text(contents=["hi"], **kwargs)
    elapsed = asyncio.get_running_loop().time() - started
    assert response.key_label == "key#2" and 0.25 <= elapsed < 2.0
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_the_per_call_cap_reaches_the_sdk_request_for_text_and_stream() -> None:
    key = _keys(1)[0]
    client = FakeGeminiClient()
    gateway = _gateway({key: client}, AsyncMock())
    await gateway.generate_text(contents=["a"], **model_policy.agent_call_kwargs("planner"))
    async for _ in gateway.generate_stream(
        contents=["b"], **model_policy.agent_call_kwargs("planner")
    ):
        pass
    await gateway.generate_text("lite", ["c"], allow_fallback=False)  # chat-style: no override
    configs = [c["config"] for c in client.aio.models.calls]
    assert configs[0].http_options.timeout == 150_000  # agent default, in milliseconds
    assert configs[1].http_options.timeout == 150_000
    assert configs[2].http_options is None  # the client's own 30 s default applies


def test_agent_timeout_setting_default_and_bounds() -> None:
    assert make_settings().agent_request_timeout_seconds == 150.0
    assert make_settings(agent_request_timeout_seconds=600).agent_request_timeout_seconds == 600
    for bad in (0, -1, 601):
        with pytest.raises(ValidationError):
            make_settings(agent_request_timeout_seconds=bad)
    policy = model_policy.configure(make_settings(agent_request_timeout_seconds=200))
    assert policy.timeout_seconds == 200.0
    assert model_policy.agent_call_kwargs()["attempt_timeout_seconds"] == 200.0


@pytest.mark.asyncio
async def test_aclose_closes_every_per_key_client() -> None:
    keys = _keys(3)
    gateway = ModelGateway(
        registry=build_default_registry(),
        settings=make_settings(gemini_api_keys=[SecretStr(k) for k in keys]),
    )
    clients = [gateway._get_client_for_key(SecretStr(k)) for k in keys]
    closers = []
    for client in clients:
        closer = AsyncMock()
        client.aio.aclose = closer
        closers.append(closer)
    await gateway.aclose()
    assert all(c.await_count >= 1 for c in closers)
