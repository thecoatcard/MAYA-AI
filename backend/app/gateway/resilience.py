"""Resilience policy for Model Gateway: retry, exponential backoff, jitter, and fallback."""

import asyncio
import logging
import random
import time
from collections.abc import Callable, Coroutine
from typing import Any

from app.gateway.exceptions import (
    GatewayFallbackExhaustedError,
    ToolCallCommitmentError,
)
from app.gateway.registry import ModelRegistry, ModelSpec

logger = logging.getLogger(__name__)


def classify_gateway_error(exc: Exception) -> str:
    """Classify an error from the provider API into an action category:

    - "retry_with_backoff": 429 (quota / rate limit) and network/socket timeouts.
    - "failover_immediate": 5xx server errors (500, 502, 503, 504) and connection resets.
    - "non_retryable": 400, 401, 403, and safety blocks (never retry or fall back).
    """
    if isinstance(exc, (TimeoutError, asyncio.TimeoutError)):
        return "retry_with_backoff"

    # Inspect HTTP status code if present on exception (e.g. google.genai.errors.APIError)
    code = getattr(exc, "code", None)
    if isinstance(code, int):
        if code == 429:
            return "retry_with_backoff"
        if 500 <= code < 600:
            return "failover_immediate"
        if code in (400, 401, 403):
            return "non_retryable"

    error_str = str(exc).lower()
    if any(term in error_str for term in ("safety", "blocked", "harmful", "recitation")):
        return "non_retryable"
    if "429" in error_str or "resource_exhausted" in error_str or "rate limit" in error_str:
        return "retry_with_backoff"
    if any(term in error_str for term in ("500", "502", "503", "504", "internal server error")):
        return "failover_immediate"

    # Default unknown errors: non-retryable to fail safe
    return "non_retryable"


class ResiliencePolicy:
    """Configurable resilience executor with injected clock, sleep, and random."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Coroutine[Any, Any, None]] = asyncio.sleep,
        random_uniform: Callable[[float, float], float] = random.uniform,
        max_retries_per_model: int = 4,
        base_delays: tuple[float, ...] = (2.0, 4.0, 8.0, 16.0),
    ) -> None:
        self.clock = clock
        self.sleep = sleep
        self.random_uniform = random_uniform
        self.max_retries_per_model = max_retries_per_model
        self.base_delays = base_delays

    async def execute[T](
        self,
        registry: ModelRegistry,
        start_alias: str,
        operation_fn: Callable[[ModelSpec, bool], Coroutine[Any, Any, T]],
        *,
        allow_fallback: bool = True,
        is_tool_call_committed: bool = False,
    ) -> tuple[T, ModelSpec, bool]:
        """Execute operation_fn across the model fallback chain with retries and backoff.

        Args:
            registry: Configured ModelRegistry.
            start_alias: Initial role alias to attempt.
            operation_fn: Async callable taking (spec, was_fallback) and returning result.
            allow_fallback: If True, traverse chain; if False, attempt start_alias only.
            is_tool_call_committed: If True, raise ToolCallCommitmentError on retry/fallback.

        Returns:
            tuple[result, final_spec, was_fallback]

        Raises:
            ToolCallCommitmentError: If retry or fallback attempted when tool calls are committed.
            UnsupportedAliasTransportError: If alias transport is not REST.
            GatewayFallbackExhaustedError: If all candidate models in the chain fail.
        """
        chain = registry.resolve_fallback_chain(start_alias) if allow_fallback else [start_alias]
        last_exception: Exception | None = None

        for alias_idx, alias in enumerate(chain):
            spec = registry.get(alias)
            was_fallback = alias_idx > 0

            # Guard against unsupported transport on fallback
            spec.check_supported_transport()

            for attempt in range(self.max_retries_per_model + 1):
                try:
                    result = await operation_fn(spec, was_fallback)
                    return result, spec, was_fallback
                except Exception as exc:
                    last_exception = exc
                    if is_tool_call_committed:
                        raise ToolCallCommitmentError(
                            f"Cannot retry or fall back after tool call commitment: {exc}"
                        ) from exc

                    category = classify_gateway_error(exc)

                    if category == "non_retryable":
                        # 400, 401, 403, safety: fail immediately
                        raise exc

                    if category == "retry_with_backoff":
                        if attempt < self.max_retries_per_model:
                            delay = self.base_delays[min(attempt, len(self.base_delays) - 1)]
                            jittered_delay = self.random_uniform(0.0, delay)
                            logger.warning(
                                "gateway_retry_backoff alias=%s attempt=%d delay=%.2f reason=%s",
                                alias,
                                attempt + 1,
                                jittered_delay,
                                exc,
                            )
                            await self.sleep(jittered_delay)
                            continue
                        # Retries exhausted on this model -> fall back to next model in chain
                        logger.warning(
                            "gateway_model_retries_exhausted alias=%s fallback_next=%s",
                            alias,
                            chain[alias_idx + 1] if alias_idx + 1 < len(chain) else "none",
                        )
                        break

                    if category == "failover_immediate":
                        # 5xx: immediate fallback without retries
                        logger.warning(
                            "gateway_5xx_immediate_failover alias=%s reason=%s fallback_next=%s",
                            alias,
                            exc,
                            chain[alias_idx + 1] if alias_idx + 1 < len(chain) else "none",
                        )
                        break

        raise GatewayFallbackExhaustedError(
            f"All candidate models in fallback chain exhausted for alias '{start_alias}'. "
            f"Last error: {last_exception}"
        ) from last_exception
