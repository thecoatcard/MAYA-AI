"""Per-engine health (cooldown after failures) and per-engine request spacing.

``EngineHealth``: a blocked, rate-limited or timed-out engine is not asked again until a cooldown
has passed. The cooldown doubles with every consecutive failure (2, 4, 8, 16, then 30 minutes at
most) and a success resets it, so one blocked engine never blocks the chain.

``Pacer``: a minimum, jittered gap between two requests to the same engine, so a burst of queries
does not look like a bot. Both take injected clocks so tests run without real time.
"""

from __future__ import annotations

import asyncio
import random
import time
from collections.abc import Awaitable, Callable, Iterable
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, Final

BASE_COOLDOWN_SECONDS: Final = 120.0
MAX_COOLDOWN_SECONDS: Final = 1800.0
COOLING_CAUSES: Final = frozenset(
    {"blocked", "rate_limited", "timeout", "network", "server", "auth", "unknown"}
)
_BLOCKING_CAUSES: Final = frozenset({"blocked", "rate_limited", "auth"})
MIN_SPACING_SECONDS: Final = 0.4
SPACING_JITTER_SECONDS: Final = 0.25


@dataclass(slots=True)
class _State:
    failures: int = 0
    until: float = 0.0
    cause: str = ""


class EngineHealth:
    """Failure counts and cooldown deadlines by engine key. Wall-clock seconds (``time.time``)."""

    def __init__(
        self,
        *,
        clock: Callable[[], float] = time.time,
        base_seconds: float = BASE_COOLDOWN_SECONDS,
        max_seconds: float = MAX_COOLDOWN_SECONDS,
    ) -> None:
        self._clock = clock
        self._base = base_seconds
        self._max = max_seconds
        self._states: dict[str, _State] = {}

    def now(self) -> float:
        return self._clock()

    def succeeded(self, key: str) -> None:
        self._states.pop(key, None)

    def failed(self, key: str, cause: str) -> float:
        """Record a failure and return the cooldown (seconds) that now applies. A cause that is
        not an engine problem (for example ``no_results``) is not a failure."""
        if cause not in COOLING_CAUSES:
            return 0.0
        state = self._states.setdefault(key, _State())
        state.failures += 1
        state.cause = cause
        seconds = float(min(self._max, self._base * (2 ** (state.failures - 1))))
        state.until = self._clock() + seconds
        return seconds

    def cooling_until(self, key: str) -> float | None:
        """The time (epoch seconds) the engine may be asked again, or ``None`` when it is ready."""
        state = self._states.get(key)
        if state is None or state.until <= self._clock():
            return None
        return state.until

    def earliest(self, keys: Iterable[str]) -> float | None:
        """When at least one of ``keys`` is ready again; ``None`` when one is ready now (or no
        keys): the group is cooling only if every key is."""
        times: list[float] = []
        for key in keys:
            until = self.cooling_until(key)
            if until is None:
                return None
            times.append(until)
        return min(times) if times else None

    def snapshot(self, key: str) -> dict[str, Any]:
        """A plain status: ``ok``, ``cooling`` or ``blocked`` (after a block). No secrets."""
        state = self._states.get(key)
        until = self.cooling_until(key)
        if state is None or until is None:
            return {"engine": key, "state": "ok", "cooling_until": None, "failures": 0}
        return {
            "engine": key,
            "state": "blocked" if state.cause in _BLOCKING_CAUSES else "cooling",
            "cooling_until": datetime.fromtimestamp(until, tz=UTC).isoformat(timespec="seconds"),
            "failures": state.failures,
            "last_cause": state.cause,
        }


class Pacer:
    """At least ``spacing`` seconds (plus a little jitter) between two starts per key."""

    def __init__(
        self,
        *,
        spacing: float = MIN_SPACING_SECONDS,
        jitter: float = SPACING_JITTER_SECONDS,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], Awaitable[None]] = asyncio.sleep,
        rand: Callable[[], float] = random.random,
    ) -> None:
        self._spacing = spacing
        self._jitter = jitter
        self._clock = clock
        self._sleep = sleep
        self._rand = rand
        self._next: dict[str, float] = {}

    async def wait(self, key: str) -> None:
        """Reserve the next slot for ``key`` and sleep until it. The slot is taken before the
        sleep, so parallel callers queue one after another."""
        now = self._clock()
        start = max(now, self._next.get(key, 0.0))
        self._next[key] = start + self._spacing + self._jitter * self._rand()
        if start > now:
            await self._sleep(start - now)
