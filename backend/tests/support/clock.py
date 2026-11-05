from datetime import UTC, datetime, timedelta

START = datetime(2026, 11, 5, 12, 0, 0, tzinfo=UTC)


class ManualClock:
    """Deterministic clock: time moves only when a test says so."""

    def __init__(self, start: datetime = START) -> None:
        self._now = start

    def now(self) -> datetime:
        return self._now

    def advance(self, *, seconds: float = 0, minutes: float = 0, hours: float = 0) -> None:
        self._now += timedelta(seconds=seconds, minutes=minutes, hours=hours)

    def set(self, value: datetime) -> None:
        self._now = value
