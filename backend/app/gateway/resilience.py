"""Resilience policy for Model Gateway: retry, instant key failover, backoff, and fallback."""

from __future__ import annotations

import asyncio
import inspect
import itertools
import logging
import random
import time
from collections.abc import Callable, Coroutine
from typing import TYPE_CHECKING, Any

from google.genai import errors as genai_errors
from pydantic import SecretStr
from redis.exceptions import RedisError

from app.core.metrics import GEMINI_KEY_CALLS_TOTAL, GEMINI_KEY_FAILOVERS_TOTAL
from app.gateway.exceptions import (
    GatewayConfigurationError,
    GatewayError,
    GatewayFallbackExhaustedError,
    ToolCallCommitmentError,
)
from app.gateway.key_pool import TOOL_GENERATE, Bucket, is_invalid_key_error
from app.gateway.registry import ModelRegistry, ModelSpec
from app.gateway.schemas import GatewayProgressEvent

if TYPE_CHECKING:
    from app.gateway.key_pool import KeyPool

logger = logging.getLogger(__name__)


def accepts_key_arg(fn: Any) -> bool:
    """Return True if callable accepts a 3rd parameter or keyword arguments."""
    try:
        sig = inspect.signature(fn)
        return len(sig.parameters) >= 3 or any(
            p.kind == inspect.Parameter.VAR_KEYWORD for p in sig.parameters.values()
        )
    except (ValueError, TypeError):
        return False


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


def failover_reason(exc: BaseException) -> str:
    """Low-cardinality reason an attempt moved off its key (never error text)."""
    if is_invalid_key_error(exc):
        return "invalid_key"
    if isinstance(exc, genai_errors.APIError):
        code = exc.code
        if code == 429:
            return "quota"
        if code == 408:
            return "timeout"
        return "unavailable"
    class_names = {cls.__name__.lower() for cls in type(exc).__mro__}
    if isinstance(exc, TimeoutError) or any("timeout" in n for n in class_names):
        return "timeout"
    return "connection"


# Failure reasons that say the MODEL (not one key) is struggling: more keys will not help.
OVERLOAD_REASONS = frozenset({"unavailable", "timeout", "connection"})


class ResiliencePolicy:
    """Configurable resilience executor with injected clock, sleep, and random.

    Key failover rules (pool with more than one key):

    * A retryable failure (429, 5xx, timeout, connection error) or a key fault (401/403, 400 key
      invalid) moves the very next attempt to a different key IMMEDIATELY, with no sleep.
    * A call never retries a key it already tried; it tries at most ``max_key_attempts``
      different keys per model, then falls back to the next model in the chain or fails honestly.
    * Backoff sleeps happen only when no healthy key is left (every eligible key is cooling),
      or when the pool has a single key.
    * Model-wide overload (503/timeout/connection) is told apart from key faults: after two
      overload failures on DIFFERENT keys for one (model, tool) bucket inside
      ``overload_window_seconds`` the remaining keys are not burned; the chain moves to the next
      model and the bucket is shared-cooled for ``model_cooldown_seconds`` so concurrent calls
      skip it first (it is tried again once the cooldown ends, and always when it is the last
      model left).
    * Quota (429) is per key: the next key is tried, up to ``max_quota_key_attempts`` keys; only
      when every key is cooling for the bucket does the chain move to the next model.
    * Every attempt has a hard ``attempt_timeout_seconds``; a hung attempt fails over at that
      timeout, not at the total deadline.
    """

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Coroutine[Any, Any, None]] = asyncio.sleep,
        random_uniform: Callable[[float, float], float] = random.uniform,
        max_retries_per_model: int = 4,
        base_delays: tuple[float, ...] = (2.0, 4.0, 8.0, 16.0),
        key_pool: KeyPool | None = None,
        max_key_attempts: int = 4,
        attempt_timeout_seconds: float | None = None,
        max_quota_key_attempts: int = 8,
        overload_window_seconds: float = 30.0,
        model_cooldown_seconds: float = 15.0,
    ) -> None:
        self.max_quota_key_attempts = max(1, max_quota_key_attempts)
        self.overload_window_seconds = overload_window_seconds
        self.model_cooldown_seconds = model_cooldown_seconds
        # Shared by every concurrent call: recent overload failures per bucket (key label -> when)
        # and the monotonic time until which a bucket is skipped as overloaded.
        self._overload_events: dict[Bucket, dict[str, float]] = {}
        self._model_cooling_until: dict[Bucket, float] = {}
        self.clock = clock
        self.sleep = sleep
        self.random_uniform = random_uniform
        self.max_retries_per_model = max_retries_per_model
        self.base_delays = base_delays
        self.key_pool = key_pool
        self.max_key_attempts = max(1, max_key_attempts)
        self.attempt_timeout_seconds = attempt_timeout_seconds

    def _delay(self, attempt: int) -> float:
        base = self.base_delays[min(max(attempt, 0), len(self.base_delays) - 1)]
        return self.random_uniform(0.0, base)

    def model_is_cooling(self, bucket: Bucket) -> bool:
        """True while ``bucket`` is in its shared model-wide overload cooldown."""
        return self.clock() < self._model_cooling_until.get(bucket, 0.0)

    def _note_overload(self, bucket: Bucket, key_label: str) -> bool:
        """Record an overload failure; True when two different keys failed inside the window."""
        now = self.clock()
        events = self._overload_events.setdefault(bucket, {})
        for label, at in list(events.items()):
            if now - at > self.overload_window_seconds:
                del events[label]
        events[key_label] = now
        if len(events) >= 2:
            self._model_cooling_until[bucket] = now + self.model_cooldown_seconds
            return True
        return False

    def _note_success(self, bucket: Bucket) -> None:
        self._overload_events.pop(bucket, None)
        self._model_cooling_until.pop(bucket, None)

    async def _run_attempt[T](
        self,
        timeout: float | None,  # noqa: ASYNC109
        operation_fn: Callable[..., Coroutine[Any, Any, T]],
        *args: Any,
    ) -> T:
        if timeout is None:
            return await operation_fn(*args)
        return await asyncio.wait_for(operation_fn(*args), timeout=timeout)

    async def execute[T](
        self,
        registry: ModelRegistry,
        start_alias: str,
        operation_fn: Callable[..., Coroutine[Any, Any, T]],
        *,
        allow_fallback: bool = True,
        is_tool_call_committed: bool = False,
        affinity: str | None = None,
        fallback_alias: str | None = None,
        attempt_timeout_seconds: float | None = None,
        tool: str = TOOL_GENERATE,
        interactive: bool = False,
        first_chunk_timeout_seconds: float | None = None,
        total_deadline_seconds: float | None = None,
        on_progress: Callable[[GatewayProgressEvent], None] | None = None,
        last_resort: bool = False,
    ) -> tuple[T, ModelSpec, bool]:
        """Execute operation_fn across the model fallback chain with retries and key failover.

        Args:
            registry: Configured ModelRegistry.
            start_alias: Initial role alias to attempt.
            operation_fn: Async callable taking (spec, was_fallback[, key]) and returning result.
            allow_fallback: If True, traverse chain; if False, attempt start_alias only.
            is_tool_call_committed: If True, raise ToolCallCommitmentError on retry/fallback.
            affinity: Key affinity (agent role or ``chapter:{i}``) for the key pool.
            fallback_alias: With ``allow_fallback``, the single alias to fall back to instead of
                the registry chain (the agent policy).
            attempt_timeout_seconds: Per-call override of the per-attempt cap (agent calls use a
                longer one); None keeps the policy default.
            tool: Quota bucket of the call (``generate`` or ``grounding``). A quota or
                availability failure cools the key only for ``(model id, tool)``.
            interactive: A live chat turn. Fails fast: after two consecutive 5xx answers from
                different keys, or two timeouts, on one model the chain moves to the next alias at
                once instead of walking every key. Cooldown scope is unchanged (per key, model).
            first_chunk_timeout_seconds: With ``interactive``, a short cap on each attempt (the
                attempt includes waiting for the first chunk), instead of the long default.
            total_deadline_seconds: With ``interactive``, a budget for the whole chain. When it is
                spent the call raises GatewayFallbackExhaustedError(deadline_exceeded=True).
            on_progress: Called (never raising into the policy) before each retry or switch.
            last_resort: With ``allow_fallback``, end the chain at the last-resort text model
                (the ``search`` alias) for plain text generation. Off for embeddings and for
                calls that need features that model may lack.

        Returns:
            tuple[result, final_spec, was_fallback]

        Raises:
            ToolCallCommitmentError: If retry or fallback attempted when tool calls are committed.
            UnsupportedAliasTransportError: If alias transport is not REST.
            GatewayFallbackExhaustedError: If all candidate models in the chain fail.
        """
        chain = registry.candidate_chain(
            start_alias,
            allow_fallback=allow_fallback,
            fallback_alias=fallback_alias,
            last_resort=last_resort,
        )
        last_exception: Exception | None = None

        pool = self.key_pool if self.key_pool is not None and not self.key_pool.is_empty else None
        pool_len = len(pool) if pool is not None else 1
        multi = pool_len > 1
        has_key_fn = accepts_key_arg(operation_fn)
        attempt_cap = (
            attempt_timeout_seconds
            if attempt_timeout_seconds is not None
            else self.attempt_timeout_seconds
        )

        started = self.clock()
        deadline = (
            started + total_deadline_seconds
            if interactive and total_deadline_seconds is not None
            else None
        )
        if interactive and first_chunk_timeout_seconds is not None:
            attempt_cap = (
                first_chunk_timeout_seconds
                if attempt_cap is None
                else min(attempt_cap, first_chunk_timeout_seconds)
            )
        attempts_made = 0
        models_tried: set[str] = set()
        keys_tried: set[str] = set()
        planned = max(2 * len(chain), 1)

        def emit(kind: str, alias: str, reason: str) -> None:
            if on_progress is None:
                return
            try:
                on_progress(
                    GatewayProgressEvent(
                        kind=kind,
                        alias=alias,
                        attempt=attempts_made + 1,
                        limit=max(planned, attempts_made + 1),
                        reason=reason,
                        elapsed_seconds=max(0.0, self.clock() - started),
                    )
                )
            except Exception:  # a progress listener must never change the outcome
                logger.warning("gateway_progress_callback_failed")

        def exhausted(deadline_exceeded: bool = False) -> GatewayFallbackExhaustedError:
            reason = (
                failover_reason(last_exception) if last_exception is not None else "unavailable"
            )
            return GatewayFallbackExhaustedError(
                f"All candidate models in fallback chain exhausted for alias '{start_alias}'. "
                f"Last error: {describe_error(last_exception)}",
                attempts=attempts_made,
                reason=reason,
                overloaded=reason in ("unavailable", "timeout", "quota", "connection"),
                deadline_exceeded=deadline_exceeded,
                models_tried=len(models_tried),
                keys_tried=len(keys_tried) or attempts_made,
            )

        def log_decision(reason: str, alias: str, model_id: str, attempt: int, nxt: str) -> None:
            logger.info(
                "gateway_failover reason=%s alias=%s model=%s attempt=%d next=%s",
                reason,
                alias,
                model_id,
                attempt,
                nxt,
            )

        for alias_idx, alias in enumerate(chain):
            spec = registry.get(alias)
            was_fallback = alias_idx > 0
            next_alias = chain[alias_idx + 1] if alias_idx + 1 < len(chain) else "none"

            # Guard against unsupported transport on fallback
            spec.check_supported_transport()
            bucket = (spec.model_id, tool)
            has_next = alias_idx + 1 < len(chain)
            if has_next and self.model_is_cooling(bucket):
                # Another call just saw this model overloaded: skip it now, retry it later.
                log_decision("overload", alias, spec.model_id, 0, f"model:{next_alias}")
                continue

            # With several keys: at most max_key_attempts DIFFERENT keys per model. With one key
            # (or none): max_retries_per_model + 1 attempts with backoff.
            max_attempts = (
                min(self.max_key_attempts, pool_len) if multi else self.max_retries_per_model + 1
            )
            tried: set[str] = set()
            consecutive_unavailable = 0
            timeouts = 0
            overload_keys: set[str] = set()

            for attempt in itertools.count():
                if attempt >= max_attempts:
                    break
                current_key: SecretStr | None = None
                key_label = "none"
                if pool is not None:
                    try:
                        selection = pool.select(affinity, tried if multi else (), bucket)
                    except ValueError:
                        raise GatewayConfigurationError(
                            "No usable Gemini API key: every key in the pool is invalid or "
                            "unauthorized"
                        ) from last_exception
                    if selection is None:
                        break
                    current_key, key_label = selection.secret, selection.label
                    if multi and not selection.healthy and has_next:
                        # Every eligible key is cooling for this model (quota): next model.
                        log_decision(
                            "quota", alias, spec.model_id, attempt + 1, f"model:{next_alias}"
                        )
                        break
                    if multi:
                        tried.add(key_label)
                    if attempt > 0 and multi and not selection.healthy:
                        # Every eligible key is cooling: the only case where we wait.
                        wait = self._delay(attempt - 1)
                        logger.warning(
                            "gateway_all_keys_cooling alias=%s attempt=%d delay=%.2f key=%s",
                            alias,
                            attempt + 1,
                            wait,
                            key_label,
                        )
                        if wait > 0:
                            await self.sleep(wait)

                this_cap = attempt_cap
                if deadline is not None:
                    remaining = deadline - self.clock()
                    if remaining <= 0:
                        logger.warning(
                            "gateway_deadline_exceeded alias=%s attempts=%d", alias, attempts_made
                        )
                        raise exhausted(deadline_exceeded=True) from last_exception
                    this_cap = remaining if this_cap is None else min(this_cap, remaining)

                try:
                    attempts_made += 1
                    models_tried.add(alias)
                    keys_tried.add(key_label)
                    if has_key_fn and current_key is not None:
                        result = await self._run_attempt(
                            this_cap, operation_fn, spec, was_fallback, current_key
                        )
                    else:
                        result = await self._run_attempt(this_cap, operation_fn, spec, was_fallback)

                    if pool is not None and current_key is not None:
                        pool.report_success(current_key, bucket)
                        self._note_success(bucket)
                        GEMINI_KEY_CALLS_TOTAL.inc(
                            labels={"key": key_label, "alias": alias, "outcome": "ok"}
                        )

                    return result, spec, was_fallback
                except Exception as exc:
                    last_exception = exc
                    if is_tool_call_committed:
                        raise ToolCallCommitmentError(
                            "Cannot retry or fall back after tool call commitment "
                            f"({describe_error(exc)})"
                        ) from exc

                    category = classify_gateway_error(exc)
                    key_fault = is_invalid_key_error(exc)

                    # Report failure to key pool if applicable
                    if pool is not None and current_key is not None:
                        pool.report_failure(current_key, exc, bucket=bucket)
                        GEMINI_KEY_CALLS_TOTAL.inc(
                            labels={"key": key_label, "alias": alias, "outcome": "error"}
                        )

                    if category == "non_retryable" and not (key_fault and multi):
                        # 400, 401, 403 (single key), safety, budget: fail immediately
                        raise

                    reason = failover_reason(exc)

                    if reason == "quota" and multi:
                        # Quota is per key: allow more keys than the 5xx budget.
                        max_attempts = max(
                            max_attempts,
                            min(max(self.max_quota_key_attempts, self.max_key_attempts), pool_len),
                        )
                    if multi and reason in OVERLOAD_REASONS:
                        overload_keys.add(key_label)
                        shared = self._note_overload(bucket, key_label)
                        if (shared or len(overload_keys) >= 2) and has_next:
                            log_decision(
                                reason, alias, spec.model_id, attempt + 1, f"model:{next_alias}"
                            )
                            emit("fallback", next_alias, reason)
                            break

                    if interactive:
                        consecutive_unavailable = (
                            consecutive_unavailable + 1 if reason == "unavailable" else 0
                        )
                        timeouts += 1 if reason == "timeout" else 0
                        if consecutive_unavailable >= 2 or timeouts >= 2:
                            logger.warning(
                                "gateway_fast_fallback alias=%s reason=%s attempts=%d "
                                "fallback_next=%s",
                                alias,
                                reason,
                                attempts_made,
                                next_alias,
                            )
                            if has_next:
                                log_decision(
                                    reason, alias, spec.model_id, attempt + 1, f"model:{next_alias}"
                                )
                                emit("fallback", next_alias, reason)
                            break

                    if multi:
                        if attempt < max_attempts - 1:
                            emit("retry", alias, reason)
                            log_decision(reason, alias, spec.model_id, attempt + 1, "next_key")
                            GEMINI_KEY_FAILOVERS_TOTAL.inc(
                                labels={"key": key_label, "reason": reason}
                            )
                            logger.warning(
                                "gateway_key_failover alias=%s attempt=%d from=%s reason=%s "
                                "error=%s",
                                alias,
                                attempt + 1,
                                key_label,
                                reason,
                                describe_error(exc),
                            )
                            continue
                        logger.warning(
                            "gateway_model_keys_exhausted alias=%s keys_tried=%d fallback_next=%s",
                            alias,
                            len(tried),
                            next_alias,
                        )
                        break

                    if category == "failover_immediate":
                        # 5xx / transport failure on a single key: immediate fallback
                        logger.warning(
                            "gateway_immediate_failover alias=%s error=%s fallback_next=%s",
                            alias,
                            describe_error(exc),
                            next_alias,
                        )
                        break

                    if attempt < max_attempts - 1:
                        emit("retry", alias, reason)
                        jittered_delay = self._delay(attempt)
                        logger.warning(
                            "gateway_retry_backoff alias=%s attempt=%d delay=%.2f key=%s error=%s",
                            alias,
                            attempt + 1,
                            jittered_delay,
                            key_label,
                            describe_error(exc),
                        )
                        if jittered_delay > 0:
                            await self.sleep(jittered_delay)
                        continue

                    # Retries exhausted on this model -> fall back to next model in chain
                    logger.warning(
                        "gateway_model_retries_exhausted alias=%s fallback_next=%s",
                        alias,
                        next_alias,
                    )
                    break

        raise exhausted() from last_exception
