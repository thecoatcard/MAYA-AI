"""Token and financial cost tracker and Redis-backed budget enforcement."""

import asyncio
import logging
from decimal import ROUND_UP, Decimal
from typing import TYPE_CHECKING, Any
from uuid import UUID

from redis.asyncio import Redis

from app.gateway.exceptions import BudgetExceededError
from app.gateway.registry import ModelSpec
from app.gateway.schemas import TokenUsage

if TYPE_CHECKING:
    from app.core.config import Settings

logger = logging.getLogger(__name__)

ONE_MILLION = Decimal("1000000")
ONE_DOLLAR_IN_MICRO = Decimal("1000000")
BUDGET_KEY_TTL_SECONDS = 86400  # 24 hours


def cost_to_micro_usd(cost_usd: Decimal) -> int:
    """Convert USD Decimal cost to micro-USD integer, rounding UP to prevent undercounting."""
    return int((cost_usd * ONE_DOLLAR_IN_MICRO).quantize(Decimal("1"), rounding=ROUND_UP))


def calculate_cost(spec: ModelSpec, usage: TokenUsage) -> tuple[Decimal, bool]:
    """Calculate exact USD cost for a token usage record according to ModelSpec rates.

    Args:
        spec: Target model specification containing pricing rates.
        usage: Measured token counts.

    Returns:
        tuple[Decimal, bool]: (exact_cost_usd, is_complete).
        If pricing is unknown or any needed rate is missing, returns (Decimal("0.000000"), False).
    """
    if spec.price_unknown:
        return (Decimal("0.000000"), False)

    prompt_tokens = usage.prompt_tokens
    cached_tokens = usage.cached_content_token_count
    uncached_tokens = max(0, prompt_tokens - cached_tokens)
    candidates_tokens = usage.candidates_tokens

    # Check needed rates
    if uncached_tokens > 0 or (prompt_tokens == 0 and candidates_tokens == 0):
        if spec.cost_per_1m_input is None:
            return (Decimal("0.000000"), False)

    if cached_tokens > 0:
        if spec.cost_per_1m_cached is None:
            return (Decimal("0.000000"), False)

    if candidates_tokens > 0:
        if spec.cost_per_1m_output is None:
            return (Decimal("0.000000"), False)

    cost = Decimal("0.000000")
    if uncached_tokens > 0 and spec.cost_per_1m_input is not None:
        cost += (Decimal(uncached_tokens) * spec.cost_per_1m_input) / ONE_MILLION

    if cached_tokens > 0 and spec.cost_per_1m_cached is not None:
        cost += (Decimal(cached_tokens) * spec.cost_per_1m_cached) / ONE_MILLION

    if candidates_tokens > 0 and spec.cost_per_1m_output is not None:
        cost += (Decimal(candidates_tokens) * spec.cost_per_1m_output) / ONE_MILLION

    return (cost, True)


class BudgetTracker:
    """Tracks task-level token and financial budgets backed by Redis atomic counters."""

    def __init__(self, redis: Redis, settings: "Settings") -> None:
        self._redis = redis
        self._settings = settings

    async def record_usage(
        self,
        workspace_id: UUID | None,
        task_id: UUID | None,
        usage: TokenUsage,
        is_tool_turn: bool = False,
    ) -> None:
        """Record token usage and financial cost for a task.

        Args:
            workspace_id: Workspace UUID scoping the task.
            task_id: Task UUID. If None, metered in-memory only without Redis writes.
            usage: TokenUsage instance with token metrics and estimated cost.
            is_tool_turn: True if invocation corresponds to an autonomous tool execution turn.

        Raises:
            ValueError: If task_id is provided without workspace_id.
            BudgetExceededError: In enforce mode, if budget caps are exceeded.
        """
        if task_id is not None and workspace_id is None:
            raise ValueError("workspace_id is required whenever task_id is specified")

        if task_id is None:
            # In-memory metering only; do not persist to Redis
            return

        key = f"maya:budget:{workspace_id}:{task_id}"
        cost_micro = cost_to_micro_usd(usage.estimated_cost_usd)
        turns_to_add = 1 if is_tool_turn else 0
        tokens_to_add = usage.total_token_count

        async def _execute_pipeline() -> list[Any]:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.hincrby(key, "cost_micro_usd", cost_micro)
                pipe.hincrby(key, "tool_turns", turns_to_add)
                pipe.hincrby(key, "total_tokens", tokens_to_add)
                pipe.expire(key, BUDGET_KEY_TTL_SECONDS)
                return await pipe.execute()

        # Wrap in asyncio.shield so caller cancellation does not abort billing persistence
        results = await asyncio.shield(_execute_pipeline())

        cum_cost_micro = int(results[0])
        cum_tool_turns = int(results[1])
        cum_total_tokens = int(results[2])
        cum_cost_usd = Decimal(cum_cost_micro) / ONE_DOLLAR_IN_MICRO

        # Check budget caps in enforce mode
        if self._settings.budget_enforcement_mode == "enforce":
            if (
                self._settings.gemini_budget_task_usd_cap is not None
                and cum_cost_usd > self._settings.gemini_budget_task_usd_cap
            ):
                raise BudgetExceededError(
                    f"Task '{task_id}' exceeded financial budget cap: "
                    f"${cum_cost_usd} > ${self._settings.gemini_budget_task_usd_cap}"
                )
            if (
                self._settings.gemini_max_tool_turns_cap is not None
                and cum_tool_turns > self._settings.gemini_max_tool_turns_cap
            ):
                raise BudgetExceededError(
                    f"Task '{task_id}' exceeded tool turns cap: "
                    f"{cum_tool_turns} > {self._settings.gemini_max_tool_turns_cap}"
                )

        # Check alert thresholds (log warning, do not raise)
        if (
            cum_cost_usd > self._settings.gemini_budget_task_usd_alert
            or cum_tool_turns > self._settings.gemini_max_tool_turns_alert
        ):
            logger.warning(
                "budget_alert_threshold_exceeded workspace_id=%s task_id=%s "
                "cost_usd=%s turns=%d total_tokens=%d alert_cost=%s alert_turns=%d",
                workspace_id,
                task_id,
                cum_cost_usd,
                cum_tool_turns,
                cum_total_tokens,
                self._settings.gemini_budget_task_usd_alert,
                self._settings.gemini_max_tool_turns_alert,
            )

    async def get_usage(self, workspace_id: UUID, task_id: UUID) -> dict[str, Any]:
        """Fetch current cumulative usage counters from Redis for a task."""
        key = f"maya:budget:{workspace_id}:{task_id}"
        raw = await self._redis.hgetall(key)
        decoded: dict[str, str] = {
            (k.decode() if isinstance(k, bytes) else str(k)): (
                v.decode() if isinstance(v, bytes) else str(v)
            )
            for k, v in raw.items()
        }
        cost_micro = int(decoded.get("cost_micro_usd", 0) or 0)
        tool_turns = int(decoded.get("tool_turns", 0) or 0)
        total_tokens = int(decoded.get("total_tokens", 0) or 0)
        return {
            "cost_micro_usd": cost_micro,
            "cost_usd": Decimal(cost_micro) / ONE_DOLLAR_IN_MICRO,
            "tool_turns": tool_turns,
            "total_tokens": total_tokens,
        }

    async def clear_task_budget(self, workspace_id: UUID, task_id: UUID) -> None:
        """Clear recorded usage for a workspace task (useful for test cleanup)."""
        key = f"maya:budget:{workspace_id}:{task_id}"
        await self._redis.delete(key)
