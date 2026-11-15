"""Tests for TokenUsage schemas, exact Decimal cost tracking, and Redis budget enforcement."""

import asyncio
import logging
import os
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from dotenv import dotenv_values
from pydantic import SecretStr
from redis.asyncio import Redis

from app.core.config import Settings
from app.gateway.cost_tracker import (
    BudgetTracker,
    calculate_cost,
    cost_to_micro_usd,
)
from app.gateway.exceptions import BudgetExceededError
from app.gateway.registry import ModelSpec, build_default_registry
from app.gateway.schemas import (
    EmbeddingResponse,
    GatewayResponse,
    GatewayStreamChunk,
    TokenCountResponse,
    TokenUsage,
    ToolCallRequest,
    ToolDefinition,
    ToolGatewayResponse,
)


def make_settings(**kwargs: object) -> Settings:
    base: dict[str, object] = {
        "database_url": SecretStr("postgresql+asyncpg://maya_app:secret@127.0.0.1:15432/maya"),
        "redis_url": SecretStr("redis://127.0.0.1:16379/0"),
        "jwt_signing_key": SecretStr("1234567890123456789012345678901234567890"),
        "otp_hmac_key": SecretStr("abcdefghijklmnopqrstuvwxyz0123456789abcdef"),
    }
    base.update(kwargs)
    return Settings(**base)  # type: ignore[arg-type]


# -----------------------------------------------------------------------------
# 1. Exact Decimal calculations and rounding UP to micro-USD
# -----------------------------------------------------------------------------


def test_exact_decimal_calculations_orchestrator() -> None:
    registry = build_default_registry()
    orch = registry.get("orchestrator")
    assert orch.cost_per_1m_input == Decimal("0.75")
    assert orch.cost_per_1m_output == Decimal("3.75")
    assert orch.cost_per_1m_cached == Decimal("0.075")

    # 1,000 prompt tokens (200 cached, 800 uncached) + 500 candidates tokens
    usage = TokenUsage(
        prompt_tokens=1_000,
        cached_content_token_count=200,
        candidates_tokens=500,
    )
    cost, complete = calculate_cost(orch, usage)

    # 800 * 0.75 / 1,000,000 = 0.000600
    # 200 * 0.075 / 1,000,000 = 0.000015
    # 500 * 3.75 / 1,000,000 = 0.001875
    # Sum = 0.002490
    expected_cost = Decimal("0.002490")
    assert cost == expected_cost
    assert complete is True

    micro = cost_to_micro_usd(cost)
    assert micro == 2490


def test_exact_decimal_calculations_embed() -> None:
    registry = build_default_registry()
    embed = registry.get("embed")
    assert embed.cost_per_1m_input == Decimal("0.20")
    assert embed.cost_per_1m_output is None
    assert embed.cost_per_1m_cached is None

    usage = TokenUsage(prompt_tokens=10_000, candidates_tokens=0, cached_content_token_count=0)
    cost, complete = calculate_cost(embed, usage)

    # 10,000 * 0.20 / 1,000,000 = 0.002000
    expected_cost = Decimal("0.002000")
    assert cost == expected_cost
    assert complete is True
    assert cost_to_micro_usd(cost) == 2000


def test_cost_to_micro_usd_rounds_up() -> None:
    # Exact micro amounts
    assert cost_to_micro_usd(Decimal("0.000000")) == 0
    assert cost_to_micro_usd(Decimal("0.000001")) == 1
    assert cost_to_micro_usd(Decimal("1.500000")) == 1_500_000

    # Sub-micro amounts must ROUND UP so budget never undercounts
    assert cost_to_micro_usd(Decimal("0.0000001")) == 1
    assert cost_to_micro_usd(Decimal("0.000000001")) == 1
    assert cost_to_micro_usd(Decimal("0.00000100001")) == 2
    assert cost_to_micro_usd(Decimal("1.5000001")) == 1_500_001


# -----------------------------------------------------------------------------
# 2. Unknown price handling
# -----------------------------------------------------------------------------


def test_unknown_price_handling_research_tts_embed_fallback() -> None:
    registry = build_default_registry()

    for alias in ("research", "tts", "embed-fallback"):
        spec = registry.get(alias)
        assert spec.price_unknown is True
        usage = TokenUsage(prompt_tokens=500, candidates_tokens=100)
        cost, complete = calculate_cost(spec, usage)
        # Unknown is None, never 0.00: a zero would read as "free".
        assert cost is None
        assert complete is False


def test_unknown_price_handling_overridden_alias() -> None:
    # Overriding an alias marks price_unknown=True
    registry = build_default_registry({"orchestrator": "gemini-custom-enterprise"})
    spec = registry.get("orchestrator")
    assert spec.price_unknown is True

    usage = TokenUsage(prompt_tokens=1_000, candidates_tokens=500)
    cost, complete = calculate_cost(spec, usage)
    assert cost is None
    assert complete is False


def test_missing_needed_rates_returns_incomplete() -> None:
    # A spec with output rate missing when candidates are generated
    incomplete_spec = ModelSpec(
        alias="test-incomplete",
        model_id="test-incomplete-model",
        fallback_alias=None,
        context_input_limit=1000,
        context_output_limit=1000,
        cost_per_1m_input=Decimal("0.50"),
        cost_per_1m_output=None,
        cost_per_1m_cached=None,
    )
    usage_with_output = TokenUsage(prompt_tokens=100, candidates_tokens=50)
    cost, complete = calculate_cost(incomplete_spec, usage_with_output)
    assert cost is None
    assert complete is False

    # But if no output tokens are produced and no cached tokens, input-only works
    usage_input_only = TokenUsage(prompt_tokens=100, candidates_tokens=0)
    cost, complete = calculate_cost(incomplete_spec, usage_input_only)
    assert complete is True
    assert cost == Decimal("0.000050")


# -----------------------------------------------------------------------------
# 3. BudgetTracker validations & task_id handling
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_tracker_task_id_without_workspace_id_raises_value_error() -> None:
    mock_redis = MagicMock(spec=Redis)
    settings = make_settings()
    tracker = BudgetTracker(mock_redis, settings)
    usage = TokenUsage(prompt_tokens=100, estimated_cost_usd=Decimal("0.01"))

    with pytest.raises(ValueError, match="workspace_id is required whenever task_id is specified"):
        await tracker.record_usage(workspace_id=None, task_id=uuid4(), usage=usage)


@pytest.mark.asyncio
async def test_budget_tracker_task_id_none_does_not_call_redis() -> None:
    mock_redis = MagicMock(spec=Redis)
    settings = make_settings()
    tracker = BudgetTracker(mock_redis, settings)
    usage = TokenUsage(prompt_tokens=100, estimated_cost_usd=Decimal("0.01"))

    # Both workspace_id provided and None when task_id is None
    await tracker.record_usage(workspace_id=uuid4(), task_id=None, usage=usage)
    assert mock_redis.pipeline.call_count == 0

    await tracker.record_usage(workspace_id=None, task_id=None, usage=usage)
    assert mock_redis.pipeline.call_count == 0


# -----------------------------------------------------------------------------
# 4. BudgetTracker log_only and enforce modes
# -----------------------------------------------------------------------------


def _mock_redis_pipeline(return_values: list[Any]) -> MagicMock:
    mock_redis = MagicMock(spec=Redis)
    mock_pipe = MagicMock()
    mock_pipe.hincrby = MagicMock()
    mock_pipe.expire = MagicMock()
    mock_pipe.execute = AsyncMock(return_value=return_values)

    async def _aenter_pipe(*args: Any, **kwargs: Any) -> MagicMock:
        return mock_pipe

    mock_pipe.__aenter__ = _aenter_pipe
    mock_pipe.__aexit__ = AsyncMock(return_value=None)
    mock_redis.pipeline.return_value = mock_pipe
    return mock_redis


@pytest.mark.asyncio
async def test_budget_tracker_log_only_mode(caplog: pytest.LogCaptureFixture) -> None:
    settings = make_settings(
        budget_enforcement_mode="log_only",
        gemini_budget_task_usd_alert=Decimal("1.50"),
        gemini_max_tool_turns_alert=10,
    )
    workspace_id = uuid4()
    task_id = uuid4()
    usage = TokenUsage(prompt_tokens=1000, estimated_cost_usd=Decimal("0.10"))

    # Case A: Below alert thresholds ($0.50, 5 turns) -> no warning
    mock_redis = _mock_redis_pipeline([500_000, 5, 2000, True])
    tracker = BudgetTracker(mock_redis, settings)

    with caplog.at_level(logging.WARNING):
        caplog.clear()
        await tracker.record_usage(workspace_id, task_id, usage, is_tool_turn=True)
        assert "budget_alert_threshold_exceeded" not in caplog.text

    # Case B: Exceeds cost alert ($1.51 > $1.50, 5 turns) -> warning logged, no error raised
    mock_redis = _mock_redis_pipeline([1_510_000, 5, 5000, True])
    tracker = BudgetTracker(mock_redis, settings)

    with caplog.at_level(logging.WARNING):
        caplog.clear()
        await tracker.record_usage(workspace_id, task_id, usage, is_tool_turn=False)
        assert "budget_alert_threshold_exceeded" in caplog.text

    # Case C: Exceeds tool turns alert (11 > 10, $0.50) -> warning logged, no error raised
    mock_redis = _mock_redis_pipeline([500_000, 11, 6000, True])
    tracker = BudgetTracker(mock_redis, settings)

    with caplog.at_level(logging.WARNING):
        caplog.clear()
        await tracker.record_usage(workspace_id, task_id, usage, is_tool_turn=True)
        assert "budget_alert_threshold_exceeded" in caplog.text


@pytest.mark.asyncio
async def test_budget_tracker_enforce_mode_raises_on_cap_exceeded() -> None:
    settings = make_settings(
        budget_enforcement_mode="enforce",
        gemini_budget_task_usd_alert=Decimal("1.00"),
        gemini_budget_task_usd_cap=Decimal("2.00"),
        gemini_max_tool_turns_alert=5,
        gemini_max_tool_turns_cap=10,
    )
    workspace_id = uuid4()
    task_id = uuid4()
    usage = TokenUsage(prompt_tokens=1000, estimated_cost_usd=Decimal("0.50"))

    # Case A: Within caps ($1.80, 8 turns) -> does not raise
    mock_redis = _mock_redis_pipeline([1_800_000, 8, 3000, True])
    tracker = BudgetTracker(mock_redis, settings)
    await tracker.record_usage(workspace_id, task_id, usage, is_tool_turn=True)

    # Case B: Exceeds cost cap ($2.01 > $2.00 cap) -> raises BudgetExceededError
    mock_redis = _mock_redis_pipeline([2_010_000, 8, 4000, True])
    tracker = BudgetTracker(mock_redis, settings)
    with pytest.raises(BudgetExceededError, match="exceeded financial budget cap"):
        await tracker.record_usage(workspace_id, task_id, usage, is_tool_turn=False)

    # Case C: Exceeds tool turns cap (11 > 10 cap) -> raises BudgetExceededError
    mock_redis = _mock_redis_pipeline([1_500_000, 11, 4000, True])
    tracker = BudgetTracker(mock_redis, settings)
    with pytest.raises(BudgetExceededError, match="exceeded tool turns cap"):
        await tracker.record_usage(workspace_id, task_id, usage, is_tool_turn=True)


# -----------------------------------------------------------------------------
# 5. Shielded cancellation verification
# -----------------------------------------------------------------------------


@pytest.mark.asyncio
async def test_budget_tracker_cancellation_shielded() -> None:
    mock_redis = MagicMock(spec=Redis)
    pipeline_started = asyncio.Event()
    pipeline_completed = asyncio.Event()

    async def slow_execute() -> list[Any]:
        pipeline_started.set()
        await asyncio.sleep(0.05)
        pipeline_completed.set()
        return [100_000, 1, 1000, True]

    mock_pipe = MagicMock()
    mock_pipe.hincrby = MagicMock()
    mock_pipe.expire = MagicMock()
    mock_pipe.execute = slow_execute

    async def _aenter(*args: Any, **kwargs: Any) -> MagicMock:
        return mock_pipe

    mock_pipe.__aenter__ = _aenter
    mock_pipe.__aexit__ = AsyncMock(return_value=None)
    mock_redis.pipeline.return_value = mock_pipe

    settings = make_settings()
    tracker = BudgetTracker(mock_redis, settings)
    usage = TokenUsage(prompt_tokens=100, estimated_cost_usd=Decimal("0.10"))

    task = asyncio.create_task(tracker.record_usage(uuid4(), uuid4(), usage, is_tool_turn=True))

    await pipeline_started.wait()
    task.cancel()

    with pytest.raises(asyncio.CancelledError):
        await task

    # Even though calling task was cancelled, the pipeline execution finished
    await asyncio.wait_for(pipeline_completed.wait(), timeout=1.0)
    assert pipeline_completed.is_set()


# -----------------------------------------------------------------------------
# 6. Schemas validation & data contracts
# -----------------------------------------------------------------------------


def test_gateway_schemas_contracts() -> None:
    # TokenUsage auto-computes total_token_count if 0
    usage = TokenUsage(
        prompt_tokens=500,
        candidates_tokens=150,
        thoughts_token_count=50,
        estimated_cost_usd=Decimal("0.001250"),
    )
    assert usage.total_token_count == 700

    # GatewayResponse syncs usage bidirectionally
    resp1 = GatewayResponse(
        text="Hello world",
        model_id="gemini-4-hydrogen",
        alias_used="orchestrator",
        was_fallback=False,
        usage=usage,
    )
    assert resp1.input_tokens == 500
    assert resp1.output_tokens == 150
    assert resp1.estimated_cost_usd == Decimal("0.001250")

    resp2 = GatewayResponse(
        text="From flat tokens",
        model_id="gemini-4-hydrogen",
        alias_used="orchestrator",
        input_tokens=300,
        output_tokens=70,
        cached_tokens=50,
        estimated_cost_usd=Decimal("0.000500"),
    )
    assert resp2.usage.prompt_tokens == 300
    assert resp2.usage.candidates_tokens == 70
    assert resp2.usage.cached_content_token_count == 50
    assert resp2.usage.total_token_count == 370
    assert resp2.usage.estimated_cost_usd == Decimal("0.000500")

    # GatewayStreamChunk
    chunk = GatewayStreamChunk(
        text="chunk-text",
        model_id="gemini-4-hydrogen",
        alias_used="orchestrator",
        is_final=True,
        usage=usage,
    )
    assert chunk.text == "chunk-text"
    assert chunk.is_final is True

    # ToolDefinition & ToolCallRequest
    tool_def = ToolDefinition(
        name="web_search",
        description="Search web",
        parameters={"type": "object", "properties": {"query": {"type": "string"}}},
    )
    assert tool_def.name == "web_search"

    call_req = ToolCallRequest(
        call_id="call-123",
        tool_name="web_search",
        arguments={"query": "Gemini 3.8 pricing"},
    )
    assert call_req.call_id == "call-123"

    # ToolGatewayResponse
    tool_resp = ToolGatewayResponse(
        call_requests=[call_req],
        model_id="gemini-4-hydrogen",
        alias_used="orchestrator",
        usage=usage,
    )
    assert len(tool_resp.tool_calls) == 1
    assert tool_resp.tool_calls[0].tool_name == "web_search"

    # EmbeddingResponse & TokenCountResponse
    embed_resp = EmbeddingResponse(
        vectors=[[0.1, 0.2, 0.3]],
        model_id="gemini-embedding-2",
        usage=usage,
    )
    assert len(embed_resp.vectors[0]) == 3

    tc_resp = TokenCountResponse(
        total_tokens=123,
        model_id="gemini-4-hydrogen",
    )
    assert tc_resp.total_tokens == 123


# -----------------------------------------------------------------------------
# 7. Real Redis integration test (@pytest.mark.redis)
# -----------------------------------------------------------------------------


def _resolve_redis_test_url() -> str | None:
    url = os.environ.get("MAYA_TEST_REDIS_URL")
    env_file = os.environ.get("MAYA_TEST_ENV_FILE")
    if url is None and env_file:
        url = dotenv_values(env_file).get("MAYA_REDIS_URL")
    return url


@pytest.mark.redis
@pytest.mark.asyncio
async def test_budget_tracker_real_redis_integration() -> None:
    url = _resolve_redis_test_url()
    if not url:
        if os.environ.get("MAYA_REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("Local Redis test target is not configured")
        pytest.skip("Local Redis test target is not configured")

    assert url.startswith(("redis://127.0.0.1", "redis://localhost"))
    redis_client = Redis.from_url(url, decode_responses=False, socket_timeout=2)

    workspace_id = uuid4()
    task_id = uuid4()
    expected_key = f"maya:budget:{workspace_id}:{task_id}"

    settings = make_settings(
        redis_url=SecretStr(url),
        budget_enforcement_mode="log_only",
        gemini_budget_task_usd_alert=Decimal("1.50"),
        gemini_max_tool_turns_alert=10,
    )
    tracker = BudgetTracker(redis_client, settings)

    try:
        # Turn 1: 1,000 tokens, $0.05, normal turn (is_tool_turn=False)
        usage1 = TokenUsage(
            prompt_tokens=800,
            candidates_tokens=200,
            total_token_count=1000,
            estimated_cost_usd=Decimal("0.050000"),
        )
        await tracker.record_usage(workspace_id, task_id, usage1, is_tool_turn=False)

        usage_data1 = await tracker.get_usage(workspace_id, task_id)
        assert usage_data1["cost_micro_usd"] == 50_000
        assert usage_data1["cost_usd"] == Decimal("0.050000")
        assert usage_data1["tool_turns"] == 0
        assert usage_data1["total_tokens"] == 1000

        # Verify TTL is set and <= 86400
        ttl = await redis_client.ttl(expected_key)
        assert 0 < ttl <= 86400

        # Turn 2: 2,000 tokens, $0.10, autonomous tool turn (is_tool_turn=True)
        usage2 = TokenUsage(
            prompt_tokens=1500,
            candidates_tokens=500,
            total_token_count=2000,
            estimated_cost_usd=Decimal("0.100000"),
        )
        await tracker.record_usage(workspace_id, task_id, usage2, is_tool_turn=True)

        usage_data2 = await tracker.get_usage(workspace_id, task_id)
        assert usage_data2["cost_micro_usd"] == 150_000
        assert usage_data2["cost_usd"] == Decimal("0.150000")
        assert usage_data2["tool_turns"] == 1
        assert usage_data2["total_tokens"] == 3000

    finally:
        # Clean up ONLY the specific test key
        await tracker.clear_task_budget(workspace_id, task_id)
        assert await redis_client.exists(expected_key) == 0
        await redis_client.aclose()
