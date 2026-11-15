"""Unit tests for Model Gateway resilience: retry, backoff, jitter, and fallback."""

from unittest.mock import AsyncMock

import pytest
from google.genai.errors import APIError

from app.gateway.exceptions import (
    GatewayFallbackExhaustedError,
    ToolCallCommitmentError,
)
from app.gateway.registry import ModelSpec, build_default_registry
from app.gateway.resilience import ResiliencePolicy, classify_gateway_error


def test_classify_gateway_error() -> None:
    # 429 and timeouts
    assert classify_gateway_error(APIError(429, "Resource exhausted")) == "retry_with_backoff"
    assert classify_gateway_error(TimeoutError("Timeout")) == "retry_with_backoff"

    # 5xx immediate failover
    assert classify_gateway_error(APIError(500, "Internal Server Error")) == "failover_immediate"
    assert classify_gateway_error(APIError(503, "Service Unavailable")) == "failover_immediate"

    # 4xx client errors
    assert classify_gateway_error(APIError(400, "Bad Request")) == "non_retryable"
    assert classify_gateway_error(APIError(401, "Unauthorized")) == "non_retryable"
    assert classify_gateway_error(APIError(403, "Permission Denied")) == "non_retryable"

    # Safety blocks
    assert (
        classify_gateway_error(ValueError("Prompt blocked due to safety policies"))
        == "non_retryable"
    )


@pytest.mark.asyncio
async def test_429_backoff_and_fallback_to_secondary_model() -> None:
    delays_slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays_slept.append(seconds)

    # Injected uniform returning constant 0.5 * delay for deterministic assertion
    def fake_uniform(a: float, b: float) -> float:
        return (a + b) / 2.0

    policy = ResiliencePolicy(
        sleep=fake_sleep,
        random_uniform=fake_uniform,
        max_retries_per_model=4,
        base_delays=(2.0, 4.0, 8.0, 16.0),
    )

    registry = build_default_registry()
    calls: list[str] = []

    async def mock_operation(spec: ModelSpec, was_fallback: bool) -> str:
        calls.append(spec.alias)
        if spec.alias == "orchestrator":
            # Always fail with 429 on orchestrator
            raise APIError(429, "Rate limited")
        # Succeed on fallback model ('lite')
        return f"success_on_{spec.alias}"

    result, final_spec, was_fallback = await policy.execute(
        registry,
        "orchestrator",
        mock_operation,
        allow_fallback=True,
    )

    assert result == "success_on_lite"
    assert final_spec.alias == "lite"
    assert was_fallback is True

    # 1 initial call + 4 retries on orchestrator = 5 calls to orchestrator, then 1 call to lite
    assert calls.count("orchestrator") == 5
    assert calls.count("lite") == 1

    # Delays slept during orchestrator retries: 2/2, 4/2, 8/2, 16/2 -> [1.0, 2.0, 4.0, 8.0]
    assert delays_slept == [1.0, 2.0, 4.0, 8.0]


@pytest.mark.asyncio
async def test_5xx_immediate_fallback_without_retries() -> None:
    delays_slept: list[float] = []

    async def fake_sleep(seconds: float) -> None:
        delays_slept.append(seconds)

    policy = ResiliencePolicy(sleep=fake_sleep)
    registry = build_default_registry()
    calls: list[str] = []

    async def mock_operation(spec: ModelSpec, was_fallback: bool) -> str:
        calls.append(spec.alias)
        if spec.alias == "orchestrator":
            raise APIError(500, "Server crashed")
        return "recovered"

    result, final_spec, was_fallback = await policy.execute(
        registry,
        "orchestrator",
        mock_operation,
        allow_fallback=True,
    )

    assert result == "recovered"
    assert final_spec.alias == "lite"
    assert was_fallback is True
    # 5xx should immediately fall over without retrying on orchestrator
    assert calls == ["orchestrator", "lite"]
    assert len(delays_slept) == 0


@pytest.mark.asyncio
async def test_400_never_falls_back_or_retries() -> None:
    policy = ResiliencePolicy()
    registry = build_default_registry()
    calls: list[str] = []

    async def mock_operation(spec: ModelSpec, was_fallback: bool) -> str:
        calls.append(spec.alias)
        raise APIError(400, "Invalid parameter")

    with pytest.raises(APIError) as exc_info:
        await policy.execute(registry, "orchestrator", mock_operation, allow_fallback=True)

    assert exc_info.value.code == 400
    assert calls == ["orchestrator"]


@pytest.mark.asyncio
async def test_fallback_chain_exhaustion_raises_gateway_fallback_exhausted_error() -> None:
    policy = ResiliencePolicy(sleep=AsyncMock())
    registry = build_default_registry()
    calls: list[str] = []

    async def mock_operation(spec: ModelSpec, was_fallback: bool) -> str:
        calls.append(spec.alias)
        raise APIError(503, "All upstream instances down")

    with pytest.raises(
        GatewayFallbackExhaustedError, match="All candidate models in fallback chain exhausted"
    ):
        await policy.execute(registry, "orchestrator", mock_operation, allow_fallback=True)

    # Chain for orchestrator is ['orchestrator', 'lite']
    assert calls == ["orchestrator", "lite"]


@pytest.mark.asyncio
async def test_tool_call_commitment_prevents_retry_or_fallback() -> None:
    policy = ResiliencePolicy()
    registry = build_default_registry()

    async def mock_operation(spec: ModelSpec, was_fallback: bool) -> str:
        raise APIError(500, "Transient error")

    with pytest.raises(
        ToolCallCommitmentError, match="Cannot retry or fall back after tool call commitment"
    ):
        await policy.execute(
            registry,
            "orchestrator",
            mock_operation,
            allow_fallback=True,
            is_tool_call_committed=True,
        )
