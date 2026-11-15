"""Fail-fast failover for interactive chat: service-wide 5xx, first-chunk timeout, deadline.

Offline: a fake client and an injected clock/sleep; no network, no real waiting beyond a few ms.
"""

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from google.genai.errors import APIError
from pydantic import SecretStr

from app.gateway.exceptions import GatewayFallbackExhaustedError
from app.gateway.key_pool import KeyPool
from app.gateway.model_gateway import ModelGateway
from app.gateway.registry import ModelRegistry, ModelSpec, build_default_registry
from app.gateway.resilience import ResiliencePolicy
from app.gateway.schemas import GatewayProgressEvent
from tests.gateway.test_gateway_key_failover import _make_key, make_settings_with_keys
from tests.support.fake_gemini_client import FakeGeminiClient
from tests.support.models import DISTINCT_LAST_RESORT_MODEL, LAST_RESORT_OVERRIDES, LITE, ORCH


class _Clock:
    def __init__(self) -> None:
        self.now = 0.0

    def __call__(self) -> float:
        return self.now


def _keys(n: int) -> list[str]:
    return [_make_key(f"ff{i}") for i in range(n)]


def _policy(pool: KeyPool, clock: _Clock | None = None) -> ResiliencePolicy:
    return ResiliencePolicy(
        sleep=AsyncMock(),
        key_pool=pool,
        max_key_attempts=4,
        clock=clock or _Clock(),
    )


def _overloaded() -> APIError:
    return APIError(503, {"error": {"message": "overloaded"}})


async def _run(
    policy: ResiliencePolicy,
    registry: ModelRegistry,
    op: Any,
    **kwargs: Any,
) -> tuple[Any, ModelSpec, bool]:
    return await policy.execute(registry, "orchestrator", op, interactive=True, **kwargs)


@pytest.mark.asyncio
async def test_two_503_on_different_keys_jump_to_the_next_alias() -> None:
    pool = KeyPool(_keys(4))
    seen: list[tuple[str, str]] = []

    async def op(spec: ModelSpec, was_fallback: bool, key: SecretStr) -> str:
        seen.append((spec.alias, key.get_secret_value()))
        if spec.alias == "orchestrator":
            raise _overloaded()
        return "ok"

    events: list[GatewayProgressEvent] = []
    result, spec, was_fallback = await _run(
        _policy(pool), build_default_registry(), op, on_progress=events.append
    )
    assert (result, spec.alias, was_fallback) == ("ok", "lite", True)
    assert [a for a, _ in seen] == ["orchestrator", "orchestrator", "lite"]
    assert len({k for a, k in seen if a == "orchestrator"}) == 2  # different keys
    assert [e.kind for e in events] == ["retry", "fallback"]
    assert events[0].reason == "unavailable"


@pytest.mark.asyncio
async def test_without_interactive_mode_every_key_is_still_tried() -> None:
    pool = KeyPool(_keys(4))
    calls: list[str] = []

    async def op(spec: ModelSpec, was_fallback: bool, key: SecretStr) -> str:
        calls.append(spec.alias)
        if spec.alias == "orchestrator":
            raise _overloaded()
        return "ok"

    await _policy(pool).execute(build_default_registry(), "orchestrator", op)
    # Deliberate change: two 503s on different keys now mean a model-wide overload for every
    # call, not only interactive ones, so the remaining keys are not burned.
    assert calls.count("orchestrator") == 2


@pytest.mark.asyncio
async def test_timeouts_do_not_retry_the_same_alias_more_than_once() -> None:
    pool = KeyPool(_keys(4))
    calls: list[str] = []

    async def op(spec: ModelSpec, was_fallback: bool, key: SecretStr) -> str:
        calls.append(spec.alias)
        if spec.alias == "orchestrator":
            raise TimeoutError
        return "ok"

    _, spec, _ = await _run(_policy(pool), build_default_registry(), op)
    assert calls == ["orchestrator", "orchestrator", "lite"]
    assert spec.alias == "lite"


@pytest.mark.asyncio
async def test_a_hung_attempt_is_cut_at_the_first_chunk_timeout() -> None:
    pool = KeyPool(_keys(2))
    calls = 0

    async def op(spec: ModelSpec, was_fallback: bool, key: SecretStr) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            await asyncio.sleep(30)  # a dead key: would hold the turn for 30 s
        return "ok"

    result, _, _ = await _run(
        _policy(pool),
        build_default_registry(),
        op,
        attempt_timeout_seconds=30.0,
        first_chunk_timeout_seconds=0.05,
    )
    assert result == "ok"
    assert calls == 2


@pytest.mark.asyncio
async def test_total_deadline_ends_the_chain_with_an_honest_error() -> None:
    clock = _Clock()
    pool = KeyPool(_keys(4))

    async def op(spec: ModelSpec, was_fallback: bool, key: SecretStr) -> str:
        clock.now += 40.0  # each failed attempt "takes" 40 s on the fake clock
        raise TimeoutError

    with pytest.raises(GatewayFallbackExhaustedError) as info:
        await _run(
            _policy(pool, clock),
            build_default_registry(),
            op,
            total_deadline_seconds=75.0,
            first_chunk_timeout_seconds=60.0,
        )
    error = info.value
    assert error.deadline_exceeded is True
    assert error.overloaded is True
    assert error.attempts == 2
    assert error.reason == "timeout"


@pytest.mark.asyncio
async def test_a_failing_progress_callback_never_changes_the_outcome() -> None:
    pool = KeyPool(_keys(3))
    calls = 0

    async def op(spec: ModelSpec, was_fallback: bool, key: SecretStr) -> str:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise _overloaded()
        return "ok"

    def boom(_event: GatewayProgressEvent) -> None:
        raise RuntimeError("listener bug")

    result, _, _ = await _run(_policy(pool), build_default_registry(), op, on_progress=boom)
    assert result == "ok"


def _gateway(
    client: FakeGeminiClient, keys: list[str], registry: ModelRegistry | None = None
) -> ModelGateway:
    pool = KeyPool(keys)
    return ModelGateway(
        registry=registry or build_default_registry(),
        settings=make_settings_with_keys(keys=keys),
        client=client,
        resilience_policy=_policy(pool),
        key_pool=pool,
    )


@pytest.mark.asyncio
async def test_stream_yields_retry_notices_then_the_answer() -> None:
    client = FakeGeminiClient()
    client.aio.models.enqueue_errors(ORCH, [_overloaded()])
    client.aio.models.default_text = "Hello"
    gateway = _gateway(client, _keys(3))

    chunks = [
        c
        async for c in gateway.generate_stream(
            "orchestrator", ["Hi"], interactive=True, emit_progress=True
        )
    ]
    notices = [c.progress for c in chunks if c.progress is not None]
    assert [n.kind for n in notices] == ["retry"]
    assert "Hello" in "".join(c.text for c in chunks)
    assert all(c.text == "" for c in chunks if c.progress is not None)


@pytest.mark.asyncio
async def test_stream_without_emit_progress_yields_no_notice_chunks() -> None:
    client = FakeGeminiClient()
    client.aio.models.enqueue_errors(ORCH, [_overloaded()])
    gateway = _gateway(client, _keys(3))
    chunks = [c async for c in gateway.generate_stream("orchestrator", ["Hi"], interactive=True)]
    assert all(c.progress is None for c in chunks)


@pytest.mark.asyncio
async def test_stream_says_still_working_while_the_model_is_silent() -> None:
    client = FakeGeminiClient()
    gateway = _gateway(client, _keys(2))
    original = client.aio.models.generate_content_stream

    async def slow(*args: Any, **kwargs: Any) -> Any:
        await asyncio.sleep(0.2)
        return await original(*args, **kwargs)

    client.aio.models.generate_content_stream = slow  # type: ignore[method-assign]
    chunks = [
        c
        async for c in gateway.generate_stream(
            "orchestrator",
            ["Hi"],
            interactive=True,
            emit_progress=True,
            still_working_after_seconds=0.05,
        )
    ]
    kinds = [c.progress.kind for c in chunks if c.progress is not None]
    assert kinds == ["waiting"]


@pytest.mark.asyncio
async def test_stream_raises_exhausted_with_attempt_count_when_everything_is_503() -> None:
    client = FakeGeminiClient()
    client.aio.models.enqueue_errors(ORCH, [_overloaded(), _overloaded()])
    client.aio.models.enqueue_errors(LITE, [_overloaded(), _overloaded()])
    # The chain ends at the last-resort model, which also fails here. It only exists when its
    # model id differs from the chain, so this scenario uses a registry where it does.
    client.aio.models.enqueue_errors(DISTINCT_LAST_RESORT_MODEL, [_overloaded(), _overloaded()])
    gateway = _gateway(client, _keys(4), build_default_registry(LAST_RESORT_OVERRIDES))
    with pytest.raises(GatewayFallbackExhaustedError) as info:
        async for _ in gateway.generate_stream(
            "orchestrator", ["Hi"], interactive=True, emit_progress=True
        ):
            pass
    assert info.value.attempts == 6
    assert info.value.models_tried == 3
    assert info.value.overloaded is True
