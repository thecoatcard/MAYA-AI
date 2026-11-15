"""Instant key failover, bounded key attempts, per-attempt timeout (fake clients only)."""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock

import pytest
from google.genai.errors import APIError
from pydantic import SecretStr

from app.core.config import Settings
from app.gateway.exceptions import (
    GatewayConfigurationError,
    GatewayFallbackExhaustedError,
    StreamInterruptedError,
)
from app.gateway.key_pool import KeyPool
from app.gateway.model_gateway import ModelGateway
from app.gateway.registry import ModelSpec, build_default_registry
from app.gateway.resilience import ResiliencePolicy
from tests.support.fake_gemini_client import FakeGeminiClient
from tests.support.models import LITE, SEARCH


def _keys(n: int) -> list[str]:
    return ["-".join(["failover", "test", "key", str(i)]) for i in range(1, n + 1)]


def _settings(**extra: Any) -> Settings:
    return Settings(
        database_url=SecretStr("postgresql+asyncpg://maya_app:secret@127.0.0.1:15432/maya"),
        redis_url=SecretStr("redis://127.0.0.1:16379/0"),
        jwt_signing_key=SecretStr("1234567890123456789012345678901234567890"),
        otp_hmac_key=SecretStr("abcdefghijklmnopqrstuvwxyz0123456789abcdef"),
        **extra,
    )


def _policy(pool: KeyPool | None, sleep: AsyncMock, **kwargs: Any) -> ResiliencePolicy:
    return ResiliencePolicy(sleep=sleep, random_uniform=lambda _a, b: b, key_pool=pool, **kwargs)


class Script:
    """Operation whose behaviour per key is scripted; records the keys it was called with."""

    def __init__(self, failing: dict[str, Exception] | None = None) -> None:
        self.failing = failing or {}
        self.used: list[str] = []

    async def __call__(self, spec: ModelSpec, was_fallback: bool, key: SecretStr) -> str:
        raw = key.get_secret_value()
        self.used.append(raw)
        if raw in self.failing:
            raise self.failing[raw]
        return f"ok:{spec.alias}"


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "error",
    [
        APIError(429, "RESOURCE_EXHAUSTED"),
        APIError(503, "UNAVAILABLE"),
        TimeoutError(),
        ConnectionResetError(),
    ],
)
async def test_retryable_failure_moves_to_another_key_with_zero_sleep(error: Exception) -> None:
    keys = _keys(3)
    pool = KeyPool(keys)
    sleep = AsyncMock()
    op = Script({keys[2]: error})  # research's affinity key (key#3) fails
    result, _, _ = await _policy(pool, sleep).execute(
        build_default_registry(), "lite", op, affinity="research"
    )
    assert result == "ok:lite"
    assert op.used == [keys[2], keys[0]]  # next attempt goes straight to the next healthy key
    sleep.assert_not_awaited()
    assert pool.cooling_count == 1  # the failed key is cooling, the affinity map is untouched


@pytest.mark.asyncio
async def test_retry_after_is_honoured_and_affinity_returns_after_the_cooldown() -> None:
    keys = _keys(2)
    now = [100.0]
    pool = KeyPool(keys, clock=lambda: now[0])
    err = APIError(429, "RESOURCE_EXHAUSTED")
    err.retry_after = 45.0  # type: ignore[attr-defined]
    op = Script({keys[0]: err})
    policy = _policy(pool, AsyncMock())
    await policy.execute(build_default_registry(), "lite", op, affinity="planner")
    # The cooldown is scoped to the bucket (model, tool) the call used: lite generation.
    lite_generate = (LITE, "generate")
    selected = pool.select("planner", bucket=lite_generate)
    assert selected is not None and selected.label == "key#2"
    # ... and the same key is still healthy for every other bucket.
    other = pool.select("planner", bucket=(SEARCH, "grounding"))
    assert other is not None and other.label == "key#1" and other.healthy
    now[0] += 46.0
    selected = pool.select("planner", bucket=lite_generate)
    assert selected is not None and selected.label == "key#1"


@pytest.mark.asyncio
async def test_a_key_that_failed_is_never_retried_within_the_same_call() -> None:
    keys = _keys(5)
    pool = KeyPool(keys)
    failing: dict[str, Exception] = {k: APIError(429, "x") for k in keys[:3]}
    op = Script(failing)
    await _policy(pool, AsyncMock(), max_key_attempts=4).execute(
        build_default_registry(), "lite", op, affinity="planner"
    )
    assert len(op.used) == len(set(op.used)) == 4
    assert op.used[:3] == keys[:3]


@pytest.mark.asyncio
async def test_single_key_still_backs_off_between_retries() -> None:
    keys = _keys(1)
    pool = KeyPool(keys)
    sleep = AsyncMock()
    op = Script({keys[0]: APIError(429, "x")})
    with pytest.raises(GatewayFallbackExhaustedError):
        await _policy(pool, sleep).execute(
            build_default_registry(), "lite", op, allow_fallback=False
        )
    assert sleep.await_count == 4  # one sleep between each of the 5 attempts


@pytest.mark.asyncio
async def test_all_keys_failing_fails_honestly_after_max_key_attempts() -> None:
    keys = _keys(17)
    pool = KeyPool(keys)
    sleep = AsyncMock()
    op = Script({k: APIError(429, "x") for k in keys})
    with pytest.raises(GatewayFallbackExhaustedError, match="Last error: APIError\\(429\\)"):
        # Deliberate: 429 may now use up to max_quota_key_attempts keys (default 8); this test
        # pins the cap to 4 to keep checking the "fail honestly after the cap" behaviour.
        await _policy(pool, sleep, max_key_attempts=4, max_quota_key_attempts=4).execute(
            build_default_registry(), "lite", op, allow_fallback=False
        )
    assert len(op.used) == 4 and len(set(op.used)) == 4
    sleep.assert_not_awaited()  # healthy keys remained, so nothing waited


@pytest.mark.asyncio
async def test_two_keys_both_failing_stop_after_two_attempts() -> None:
    keys = _keys(2)
    op = Script({k: APIError(503, "x") for k in keys})
    with pytest.raises(GatewayFallbackExhaustedError):
        await _policy(KeyPool(keys), AsyncMock()).execute(
            build_default_registry(), "lite", op, allow_fallback=False
        )
    assert len(op.used) == 2


@pytest.mark.asyncio
async def test_backoff_sleep_applies_only_when_every_eligible_key_is_cooling() -> None:
    keys = _keys(3)
    pool = KeyPool(keys)
    pool.report_failure(keys[1], "429", retry_after=60.0)
    pool.report_failure(keys[2], "429", retry_after=60.0)
    sleep = AsyncMock()
    op = Script({keys[0]: APIError(429, "x")})
    # allow_fallback=False: with a fallback chain, all-keys-cooling now moves to the next model
    # instead of sleeping (see test_model_failover_policy); this test keeps the sleep rule.
    result, _, _ = await _policy(pool, sleep).execute(
        build_default_registry(), "lite", op, affinity="planner", allow_fallback=False
    )
    assert result == "ok:lite"  # the cooling key recovers soonest and is used after one wait
    assert sleep.await_count == 1


@pytest.mark.asyncio
async def test_invalid_key_fails_over_at_once_and_is_marked_bad() -> None:
    keys = _keys(3)
    pool = KeyPool(keys)
    sleep = AsyncMock()
    op = Script({keys[0]: APIError(403, "PERMISSION_DENIED")})
    result, _, _ = await _policy(pool, sleep).execute(
        build_default_registry(), "lite", op, affinity="planner"
    )
    assert result == "ok:lite" and op.used == [keys[0], keys[1]]
    assert pool.bad_count == 1
    sleep.assert_not_awaited()
    # the dead key is never selected again
    op2 = Script()
    await _policy(pool, sleep).execute(build_default_registry(), "lite", op2, affinity="planner")
    assert op2.used == [keys[1]]


@pytest.mark.asyncio
async def test_every_key_invalid_fails_then_raises_a_clear_configuration_error() -> None:
    keys = _keys(2)
    pool = KeyPool(keys)
    op = Script({k: APIError(401, "bad") for k in keys})
    policy = _policy(pool, AsyncMock(), max_key_attempts=4)
    with pytest.raises(GatewayFallbackExhaustedError):
        await policy.execute(build_default_registry(), "lite", op, allow_fallback=False)
    assert pool.bad_count == 2
    with pytest.raises(GatewayConfigurationError, match="every key in the pool is invalid"):
        await policy.execute(build_default_registry(), "lite", op, allow_fallback=False)


@pytest.mark.asyncio
async def test_a_plain_400_still_raises_without_switching_keys() -> None:
    keys = _keys(3)
    op = Script({keys[0]: APIError(400, "bad request body")})
    with pytest.raises(APIError):
        await _policy(KeyPool(keys), AsyncMock()).execute(
            build_default_registry(), "lite", op, affinity="planner"
        )
    assert len(op.used) == 1


@pytest.mark.asyncio
async def test_a_hung_attempt_times_out_at_the_attempt_timeout_and_fails_over() -> None:
    keys = _keys(2)
    pool = KeyPool(keys)
    sleep = AsyncMock()
    used: list[str] = []

    async def op(spec: ModelSpec, was_fallback: bool, key: SecretStr) -> str:
        used.append(key.get_secret_value())
        if len(used) == 1:
            await asyncio.sleep(30)  # a hung connection; the 300 s deadline is far away
        return "ok"

    started = asyncio.get_running_loop().time()
    result, _, _ = await _policy(pool, sleep, attempt_timeout_seconds=0.05).execute(
        build_default_registry(), "lite", op, affinity="planner"
    )
    assert result == "ok" and used == [keys[0], keys[1]]
    assert asyncio.get_running_loop().time() - started < 2.0
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_wires_the_request_timeout_and_key_attempt_limit_from_settings() -> None:
    keys = _keys(6)
    gateway = ModelGateway(
        registry=build_default_registry(),
        settings=_settings(
            gemini_api_keys=[SecretStr(k) for k in keys],
            gemini_request_timeout_seconds=12.5,
            gemini_max_key_attempts=2,
        ),
        client_factory=lambda key: FakeGeminiClient(),
    )
    policy = gateway._resilience
    assert policy.attempt_timeout_seconds == 12.5 and policy.max_key_attempts == 2


@pytest.mark.asyncio
async def test_gateway_text_429_on_the_affinity_key_goes_to_the_next_key_instantly() -> None:
    keys = _keys(3)
    pool = KeyPool(keys)
    clients = {k: FakeGeminiClient() for k in keys}
    clients[keys[1]].aio.models.enqueue_errors(LITE, [APIError(429, "x")])
    sleep = AsyncMock()
    gateway = ModelGateway(
        registry=build_default_registry(),
        settings=_settings(gemini_api_keys=[SecretStr(k) for k in keys]),
        resilience_policy=_policy(pool, sleep),
        key_pool=pool,
        client_factory=lambda key: clients[key.get_secret_value()],
    )
    response = await gateway.generate_text(
        "lite", ["hi"], affinity="executor", allow_fallback=False
    )
    assert response.key_label == "key#3"  # key#2 (executor's key) failed; ring order moved on
    assert len(clients[keys[1]].aio.models.calls) == 1
    assert len(clients[keys[2]].aio.models.calls) == 1
    sleep.assert_not_awaited()


@pytest.mark.asyncio
async def test_gateway_stream_fails_over_only_before_the_first_chunk() -> None:
    keys = _keys(2)
    pool = KeyPool(keys)
    clients = {k: FakeGeminiClient() for k in keys}
    clients[keys[0]].aio.models.enqueue_errors(LITE, [APIError(503, "x")])
    for c in clients.values():
        c.aio.models.default_text = "streamed ok"
    sleep = AsyncMock()
    gateway = ModelGateway(
        registry=build_default_registry(),
        settings=_settings(gemini_api_keys=[SecretStr(k) for k in keys]),
        resilience_policy=_policy(pool, sleep),
        key_pool=pool,
        client_factory=lambda key: clients[key.get_secret_value()],
    )
    text = "".join(
        [c.text async for c in gateway.generate_stream("lite", ["hi"], affinity="planner")]
    )
    assert "streamed ok" in text
    sleep.assert_not_awaited()

    # after the first chunk a failure is NOT retried on another key
    clients[keys[0]].aio.models.stream_error_after_chunk = 1
    clients[keys[1]].aio.models.stream_error_after_chunk = 1
    with pytest.raises(StreamInterruptedError):
        async for _ in gateway.generate_stream("lite", ["hi"], affinity="planner"):
            pass
    streams = sum(
        1 for c in clients.values() for call in c.aio.models.calls if "stream" in call["method"]
    )
    assert streams == 3  # 2 for the first call (503 then ok) + exactly 1 for the interrupted one
