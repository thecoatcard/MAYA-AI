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


def calculate_cost(spec: ModelSpec, usage: TokenUsage) -> tuple[Decimal | None, bool]:
    """Calculate exact USD cost for a token usage record according to ModelSpec rates.

    Billing categories (Gemini API semantics):
    - ``prompt_tokens`` includes the cached subset; the uncached part is billed at the input rate
      and the cached part at the cached rate.
    - ``tool_use_prompt_token_count`` (tokens added by built-in tools) is billed at the input rate.
    - ``candidates_tokens`` and ``thoughts_token_count`` are both billed at the output rate.

    Returns:
        tuple[Decimal | None, bool]: ``(cost_usd, is_complete)``. When the price is unknown or any
        rate needed for the reported tokens is missing, returns ``(None, False)``; an unknown
        cost is never reported as zero.
    """
    if spec.price_unknown:
        return (None, False)

    cached_tokens = usage.cached_content_token_count
    input_tokens = max(0, usage.prompt_tokens - cached_tokens) + usage.tool_use_prompt_token_count
    output_tokens = usage.candidates_tokens + usage.thoughts_token_count

    if input_tokens > 0 and spec.cost_per_1m_input is None:
        return (None, False)
    if cached_tokens > 0 and spec.cost_per_1m_cached is None:
        return (None, False)
    if output_tokens > 0 and spec.cost_per_1m_output is None:
        return (None, False)

    cost = Decimal("0.000000")
    if input_tokens > 0 and spec.cost_per_1m_input is not None:
        cost += (Decimal(input_tokens) * spec.cost_per_1m_input) / ONE_MILLION
    if cached_tokens > 0 and spec.cost_per_1m_cached is not None:
        cost += (Decimal(cached_tokens) * spec.cost_per_1m_cached) / ONE_MILLION
    if output_tokens > 0 and spec.cost_per_1m_output is not None:
        cost += (Decimal(output_tokens) * spec.cost_per_1m_output) / ONE_MILLION

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
        # An unknown cost (None) cannot be added to the money counter. It is counted separately so
        # that "spend is incomplete for this task" stays visible instead of silently looking free.
        cost_micro = (
            cost_to_micro_usd(usage.estimated_cost_usd)
            if usage.estimated_cost_usd is not None
            else 0
        )
        unpriced_to_add = 0 if usage.estimated_cost_usd is not None else 1
        turns_to_add = 1 if is_tool_turn else 0
        tokens_to_add = usage.total_token_count

        async def _execute_pipeline() -> list[Any]:
            async with self._redis.pipeline(transaction=True) as pipe:
                pipe.hincrby(key, "cost_micro_usd", cost_micro)
                pipe.hincrby(key, "tool_turns", turns_to_add)
                pipe.hincrby(key, "total_tokens", tokens_to_add)
                pipe.hincrby(key, "unpriced_calls", unpriced_to_add)
                pipe.expire(key, BUDGET_KEY_TTL_SECONDS)
                return await pipe.execute()

        # Wrap in asyncio.shield so caller cancellation does not abort billing persistence
        results = await asyncio.shield(_execute_pipeline())

        cum_cost_micro = int(results[0])
        cum_tool_turns = int(results[1])
        cum_total_tokens = int(results[2])
        cum_cost_usd = Decimal(cum_cost_micro) / ONE_DOLLAR_IN_MICRO

        if unpriced_to_add:
            logger.warning(
                "budget_usage_unpriced workspace_id=%s task_id=%s "
                "(cost unknown for this call; spend counters are a lower bound)",
                workspace_id,
                task_id,
            )

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
        unpriced_calls = int(decoded.get("unpriced_calls", 0) or 0)
        return {
            "cost_micro_usd": cost_micro,
            "cost_usd": Decimal(cost_micro) / ONE_DOLLAR_IN_MICRO,
            "tool_turns": tool_turns,
            "total_tokens": total_tokens,
            "unpriced_calls": unpriced_calls,
        }

    async def ensure_within_budget(
        self,
        workspace_id: UUID | None,
        task_id: UUID | None,
        *,
        for_tool_call: bool = False,
    ) -> None:
        """Pre-call guard: in enforce mode, refuse to start another call once a cap is reached.

        Called before every provider attempt (including retries, fallbacks and repair turns) so
        that those cannot bypass an approved cap. A no-op in ``log_only`` mode and when no
        ``task_id`` is given. The tool-turn cap is checked only for tool-capable calls.

        Raises:
            ValueError: If task_id is provided without workspace_id.
            BudgetExceededError: If the task already reached a configured cap.
        """
        if task_id is None or self._settings.budget_enforcement_mode != "enforce":
            return
        if workspace_id is None:
            raise ValueError("workspace_id is required whenever task_id is specified")

        usage = await self.get_usage(workspace_id, task_id)
        cost_cap = self._settings.gemini_budget_task_usd_cap
        turn_cap = self._settings.gemini_max_tool_turns_cap
        if cost_cap is not None and usage["cost_usd"] >= cost_cap:
            raise BudgetExceededError(
                f"Task '{task_id}' reached its financial budget cap: "
                f"${usage['cost_usd']} >= ${cost_cap}"
            )
        if for_tool_call and turn_cap is not None and usage["tool_turns"] >= turn_cap:
            raise BudgetExceededError(
                f"Task '{task_id}' reached its tool turns cap: {usage['tool_turns']} >= {turn_cap}"
            )

    async def clear_task_budget(self, workspace_id: UUID, task_id: UUID) -> None:
        """Clear recorded usage for a workspace task (useful for test cleanup)."""
        key = f"maya:budget:{workspace_id}:{task_id}"
        await self._redis.delete(key)
