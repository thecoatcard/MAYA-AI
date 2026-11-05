"""Budget guards: pre-call enforcement and visibility of calls whose cost is unknown."""

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

from app.gateway.cost_tracker import BudgetTracker
from app.gateway.exceptions import BudgetExceededError
from app.gateway.schemas import TokenUsage
from tests.support.gateway_factory import make_settings

ENFORCE = {
    "budget_enforcement_mode": "enforce",
    "gemini_budget_task_usd_cap": Decimal("2.00"),
    "gemini_max_tool_turns_cap": 3,
}


def redis_with_usage(**fields: int) -> MagicMock:
    redis = MagicMock(spec=Redis)
    redis.hgetall = AsyncMock(return_value={k.encode(): str(v).encode() for k, v in fields.items()})
    return redis


@pytest.mark.asyncio
async def test_pre_check_is_a_noop_in_log_only_mode_and_without_a_task() -> None:
    redis = redis_with_usage(cost_micro_usd=9_000_000)
    await BudgetTracker(redis, make_settings()).ensure_within_budget(uuid4(), uuid4())
    await BudgetTracker(redis, make_settings(**ENFORCE)).ensure_within_budget(uuid4(), None)
    redis.hgetall.assert_not_awaited()


@pytest.mark.asyncio
async def test_pre_check_refuses_another_call_once_the_cost_cap_is_reached() -> None:
    tracker = BudgetTracker(redis_with_usage(cost_micro_usd=2_000_000), make_settings(**ENFORCE))
    with pytest.raises(BudgetExceededError, match="reached its financial budget cap"):
        await tracker.ensure_within_budget(uuid4(), uuid4())

    below = BudgetTracker(redis_with_usage(cost_micro_usd=1_999_999), make_settings(**ENFORCE))
    await below.ensure_within_budget(uuid4(), uuid4())


@pytest.mark.asyncio
async def test_tool_turn_cap_applies_to_tool_calls_only() -> None:
    tracker = BudgetTracker(
        redis_with_usage(cost_micro_usd=10, tool_turns=3), make_settings(**ENFORCE)
    )
    await tracker.ensure_within_budget(uuid4(), uuid4())  # a final plain answer is still allowed
    with pytest.raises(BudgetExceededError, match="reached its tool turns cap"):
        await tracker.ensure_within_budget(uuid4(), uuid4(), for_tool_call=True)


@pytest.mark.asyncio
async def test_pre_check_requires_a_workspace_with_a_task() -> None:
    tracker = BudgetTracker(redis_with_usage(), make_settings(**ENFORCE))
    with pytest.raises(ValueError, match="workspace_id is required"):
        await tracker.ensure_within_budget(None, uuid4())


@pytest.mark.asyncio
async def test_unknown_cost_is_counted_separately_not_added_as_money(
    caplog: pytest.LogCaptureFixture,
) -> None:
    increments: list[tuple[str, int]] = []
    pipe = MagicMock()
    pipe.hincrby = lambda _key, field, amount: increments.append((field, amount))
    pipe.expire = MagicMock()
    pipe.execute = AsyncMock(return_value=[0, 0, 150, 1, True])

    async def enter(*_args: Any, **_kwargs: Any) -> MagicMock:
        return pipe

    pipe.__aenter__ = enter
    pipe.__aexit__ = AsyncMock(return_value=None)
    redis = MagicMock(spec=Redis)
    redis.pipeline.return_value = pipe
    tracker = BudgetTracker(redis, make_settings())

    unknown_cost = TokenUsage(prompt_tokens=100, candidates_tokens=50, estimated_cost_usd=None)
    with caplog.at_level(logging.WARNING, logger="app.gateway"):
        await tracker.record_usage(uuid4(), uuid4(), unknown_cost)

    assert ("cost_micro_usd", 0) in increments
    assert ("unpriced_calls", 1) in increments
    assert "budget_usage_unpriced" in caplog.text


# -----------------------------------------------------------------------------
# Real Redis (marker `redis`)
# -----------------------------------------------------------------------------


def _redis_url() -> str | None:
    url = os.environ.get("MAYA_TEST_REDIS_URL")
    env_file = os.environ.get("MAYA_TEST_ENV_FILE")
    if url is None and env_file:
        url = dotenv_values(env_file).get("MAYA_REDIS_URL")
    return url


@pytest.mark.redis
@pytest.mark.asyncio
async def test_real_redis_pre_check_blocks_after_cap_and_counts_unpriced_calls() -> None:
    url = _redis_url()
    if not url:
        if os.environ.get("MAYA_REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail("Local Redis test target is not configured")
        pytest.skip("Local Redis test target is not configured")
    assert url.startswith(("redis://127.0.0.1", "redis://localhost"))

    redis = Redis.from_url(url, decode_responses=False, socket_timeout=2)
    workspace_id, task_id = uuid4(), uuid4()
    settings = make_settings(
        redis_url=SecretStr(url),
        budget_enforcement_mode="enforce",
        gemini_budget_task_usd_cap=Decimal("0.50"),
        gemini_max_tool_turns_cap=5,
    )
    tracker = BudgetTracker(redis, settings)
    try:
        await tracker.ensure_within_budget(workspace_id, task_id)  # nothing spent yet

        # Within the cap: recorded normally and the next call is still allowed.
        await tracker.record_usage(
            workspace_id, task_id, TokenUsage(prompt_tokens=10, estimated_cost_usd=Decimal("0.30"))
        )
        await tracker.ensure_within_budget(workspace_id, task_id)

        # A call of unknown cost adds tokens and an unpriced marker but no money.
        await tracker.record_usage(workspace_id, task_id, TokenUsage(prompt_tokens=20))
        usage = await tracker.get_usage(workspace_id, task_id)
        assert usage["cost_micro_usd"] == 300_000
        assert usage["unpriced_calls"] == 1
        assert usage["total_tokens"] == 30

        # Reaching the cap blocks the next call before it is made.
        with pytest.raises(BudgetExceededError):
            await tracker.record_usage(
                workspace_id,
                task_id,
                TokenUsage(prompt_tokens=10, estimated_cost_usd=Decimal("0.30")),
            )
        with pytest.raises(BudgetExceededError, match="reached its financial budget cap"):
            await tracker.ensure_within_budget(workspace_id, task_id)
    finally:
        await tracker.clear_task_budget(workspace_id, task_id)
        assert await redis.exists(f"maya:budget:{workspace_id}:{task_id}") == 0
        await redis.aclose()
