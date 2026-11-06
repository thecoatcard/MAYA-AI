"""Resilience policy for Model Gateway: retry, exponential backoff, jitter, and fallback."""

import asyncio
import logging
import random
import time
from collections.abc import Callable, Coroutine
from typing import Any

from google.genai import errors as genai_errors
from redis.exceptions import RedisError

from app.gateway.exceptions import (
    GatewayError,
    GatewayFallbackExhaustedError,
    ToolCallCommitmentError,
)
from app.gateway.registry import ModelRegistry, ModelSpec

logger = logging.getLogger(__name__)

# Transport libraries used by the SDK (httpx, httpx2 or aiohttp, chosen at runtime) raise their
# own exception classes. They are recognised by class name along the MRO so that no single HTTP
# library has to be imported here and ambient SDK configuration cannot change the classification.
_TRANSPORT_ERROR_NAMES = frozenset(
    {
        "TransportError",
        "NetworkError",
        "ConnectError",
        "ConnectionError",
        "RemoteProtocolError",
        "ReadError",
        "WriteError",
        "ClientConnectionError",
        "ClientConnectorError",
        "ClientOSError",
        "ServerDisconnectedError",
    }
)


def describe_error(exc: BaseException | None) -> str:
    """Return a log-safe description (exception class and HTTP status only, never its text).

    Provider error text can echo request content, so it is never logged or embedded in
    gateway exceptions.
    """
    if exc is None:
        return "none"
    code = exc.code if isinstance(exc, genai_errors.APIError) else None
    name = type(exc).__name__
    return f"{name}({code})" if isinstance(code, int) else name


def classify_gateway_error(exc: Exception) -> str:
    """Classify an error from the provider API into an action category.

    - "retry_with_backoff": 429 (quota / rate limit), 408, and timeouts.
    - "failover_immediate": 5xx server errors and connection-level transport failures.
    - "non_retryable": 400, 401, 403, other 4xx, gateway errors (including budget and content
      blocks) and anything unrecognised (fail safe, never spend money on an unknown failure).

    Classification uses exception types and HTTP status codes only. It never searches the error
    message: messages can contain identifiers or echoed content that merely looks like a status.
    """
    if isinstance(exc, (GatewayError, RedisError)):
        return "non_retryable"

    if isinstance(exc, genai_errors.APIError):
        code = exc.code
        if isinstance(code, int):
            if code in (408, 429):
                return "retry_with_backoff"
            if 500 <= code < 600:
                return "failover_immediate"
        return "non_retryable"

    class_names = {cls.__name__ for cls in type(exc).__mro__}
    if isinstance(exc, TimeoutError) or any("timeout" in name.lower() for name in class_names):
        return "retry_with_backoff"
    if isinstance(exc, OSError) or class_names & _TRANSPORT_ERROR_NAMES:
        return "failover_immediate"

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
                            "Cannot retry or fall back after tool call commitment "
                            f"({describe_error(exc)})"
                        ) from exc

                    category = classify_gateway_error(exc)

                    if category == "non_retryable":
                        # 400, 401, 403, safety, budget: fail immediately
                        raise

                    if category == "retry_with_backoff":
                        if attempt < self.max_retries_per_model:
                            delay = self.base_delays[min(attempt, len(self.base_delays) - 1)]
                            jittered_delay = self.random_uniform(0.0, delay)
                            logger.warning(
                                "gateway_retry_backoff alias=%s attempt=%d delay=%.2f error=%s",
                                alias,
                                attempt + 1,
                                jittered_delay,
                                describe_error(exc),
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
                        # 5xx / transport failure: immediate fallback without retries
                        logger.warning(
                            "gateway_immediate_failover alias=%s error=%s fallback_next=%s",
                            alias,
                            describe_error(exc),
                            chain[alias_idx + 1] if alias_idx + 1 < len(chain) else "none",
                        )
                        break

        raise GatewayFallbackExhaustedError(
            f"All candidate models in fallback chain exhausted for alias '{start_alias}'. "
            f"Last error: {describe_error(last_exception)}"
        ) from last_exception
