from datetime import UTC, datetime
from typing import Protocol


class Clock(Protocol):
    """Single time source, so expiry and cooldown logic can be tested without sleeping."""

    def now(self) -> datetime: ...


class SystemClock:
    def now(self) -> datetime:
        return datetime.now(UTC)
