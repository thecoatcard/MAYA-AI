"""A shared cooldown for the Gemini Google Search grounding bucket.

Grounding has its own quota. When a call ends in quota exhaustion (429 / RESOURCE_EXHAUSTED on every
key) the whole bucket is treated as unavailable for ``COOLDOWN_SECONDS`` (30 minutes) for EVERY
caller of this process (research steps, the chat web_search fallback). During the cooldown no
grounded call is made at all, so a task never burns through every key again and again; callers fall
back to the native search stack immediately.

The state is one float (the monotonic deadline), written and read without awaiting, so it is safe
under asyncio without a lock. It carries no task or user data.
"""

from __future__ import annotations

import time
from collections.abc import Callable
from typing import Final

COOLDOWN_SECONDS: Final = 30 * 60.0


class GroundingCooldown:
    def __init__(
        self,
        seconds: float = COOLDOWN_SECONDS,
        clock: Callable[[], float] = time.monotonic,
    ) -> None:
        self._seconds = seconds
        self._clock = clock
        self._until = 0.0

    def trip(self) -> None:
        """Quota was exhausted: no grounded call until the cooldown ends."""
        self._until = self._clock() + self._seconds

    def active(self) -> bool:
        return self._clock() < self._until

    def remaining(self) -> float:
        return max(0.0, self._until - self._clock())

    def reset(self) -> None:
        self._until = 0.0


# The one bucket shared by every grounded caller of this process.
shared = GroundingCooldown()
