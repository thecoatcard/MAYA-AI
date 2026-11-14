"""The decision service: fail-open calls with a circuit breaker and a call budget (ADR-0029).

``decide`` returns typed answers or None. None means "no opinion": the caller keeps today's
behaviour. It never raises (except for task cancellation), never logs content or keys, and a
decision may only make MAYA safer or more accurate; no caller treats it as an approval.
"""

from __future__ import annotations

import logging
import time
from collections import deque
from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.core.metrics import DECISION_CALLS_TOTAL
from app.decisions.client import DecisionBlocked, DecisionClient, DecisionError
from app.decisions.questions import Answers, Question

logger = logging.getLogger("maya.decisions")

PURPOSES: tuple[str, ...] = (
    "route",
    "search_need",
    "grounding",
    "task_verify",
    "doc_quality",
    "approval_check",
    "injection",
    "mail_triage",
    "voice_digest",
    "memory",
    "planner_hint",
    "has_topic",
    "notion_parent",
    "startup_check",
    "memory_gate",
)


class CircuitBreaker:
    """Opens after ``threshold`` consecutive failures and stays open for ``cooldown`` seconds."""

    def __init__(
        self,
        threshold: int = 5,
        cooldown: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._threshold = threshold
        self._cooldown = cooldown
        self._clock = clock
        self._failures = 0
        self._opened_at: float | None = None

    @property
    def state(self) -> str:
        if self._opened_at is None:
            return "closed"
        return "open" if self._clock() - self._opened_at < self._cooldown else "half_open"

    def allow(self) -> bool:
        return self.state != "open"

    def success(self) -> None:
        self._failures = 0
        self._opened_at = None

    def failure(self) -> None:
        self._failures += 1
        if self._failures >= self._threshold:
            self._opened_at = self._clock()  # a failed half-open probe re-opens the circuit


class _Window:
    """Sliding one-minute call counter."""

    def __init__(self, limit: int, clock: Callable[[], float]) -> None:
        self._limit = limit
        self._clock = clock
        self._stamps: deque[float] = deque()

    def take(self) -> bool:
        now = self._clock()
        while self._stamps and now - self._stamps[0] >= 60.0:
            self._stamps.popleft()
        if len(self._stamps) >= self._limit:
            return False
        self._stamps.append(now)
        return True


class DecisionService:
    def __init__(
        self,
        client: DecisionClient | None,
        *,
        enabled: bool = True,
        disabled_purposes: Sequence[str] = (),
        timeout: float = 3.0,
        background_timeout: float = 8.0,
        max_per_minute: int = 60,
        per_purpose_per_minute: int = 20,
        breaker_threshold: int = 5,
        breaker_cooldown: float = 60.0,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._client = client
        self._enabled = enabled
        self._disabled = frozenset(p.strip() for p in disabled_purposes if p.strip())
        self._timeout = timeout
        self._background_timeout = background_timeout
        self._breaker = CircuitBreaker(breaker_threshold, breaker_cooldown, clock)
        self._global = _Window(max_per_minute, clock)
        self._per_purpose_limit = per_purpose_per_minute
        self._windows: dict[str, _Window] = {}
        self._clock = clock
        self._counters: dict[str, dict[str, int]] = {}
        # Result of the non-blocking startup self-test (set by app.main); shown in status().
        self.startup_check: dict[str, str] = {"state": "pending", "reason": ""}

    @property
    def model(self) -> str:
        return self._client.model if self._client is not None else ""

    @property
    def enabled(self) -> bool:
        return self._enabled and self._client is not None

    def available(self, purpose: str) -> bool:
        """Cheap pre-check so callers can skip building a request when it cannot run."""
        return self.enabled and purpose not in self._disabled and self._breaker.allow()

    def _count(self, purpose: str, outcome: str) -> None:
        bucket = self._counters.setdefault(purpose, {})
        bucket[outcome] = bucket.get(outcome, 0) + 1
        DECISION_CALLS_TOTAL.inc(labels={"purpose": purpose, "outcome": outcome})

    async def decide(
        self,
        purpose: str,
        input_value: str | Sequence[Mapping[str, Any]],
        questions: Sequence[Question],
        *,
        timeout: float | None = None,  # noqa: ASYNC109
        background: bool = False,
    ) -> Answers | None:
        if self._client is None or not self._enabled or purpose in self._disabled:
            return None
        if not self._breaker.allow():
            self._count(purpose, "circuit_open")
            return None
        window = self._windows.setdefault(purpose, _Window(self._per_purpose_limit, self._clock))
        if not (window.take() and self._global.take()):
            self._count(purpose, "rate_limited")
            return None
        if timeout is not None:
            limit = timeout
        else:
            limit = self._background_timeout if background else self._timeout
        started = self._clock()
        try:
            answers = await self._client.create(input_value, questions, timeout=limit)
        except DecisionBlocked as exc:
            # The service answered; a refusal says nothing about its health.
            self._finish(purpose, exc.reason, started)
            return None
        except DecisionError as exc:
            self._breaker.failure()
            self._finish(purpose, exc.reason, started)
            return None
        except Exception:  # noqa: BLE001 - fail open on anything unexpected
            self._breaker.failure()
            self._finish(purpose, "error", started)
            return None
        self._breaker.success()
        self._finish(purpose, "ok", started)
        return answers

    def _finish(self, purpose: str, outcome: str, started: float) -> None:
        self._count(purpose, outcome)
        logger.info(
            "decision_call purpose=%s outcome=%s latency_ms=%d",
            purpose,
            outcome,
            int((self._clock() - started) * 1000),
        )

    def status(self) -> dict[str, Any]:
        """Switches, circuit state and counters. No content, no keys."""
        return {
            "enabled": self.enabled,
            "model": self.model,
            "circuit": self._breaker.state,
            "disabled_purposes": sorted(self._disabled),
            "purposes": {p: dict(self._counters.get(p, {})) for p in PURPOSES},
            "startup_check": dict(self.startup_check),
        }

    async def aclose(self) -> None:
        if self._client is not None:
            await self._client.aclose()
