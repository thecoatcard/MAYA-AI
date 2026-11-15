"""The scheduler services of the running application (set by the lifespan, read by chat/voice)."""

from __future__ import annotations

from app.schedules.factory import ScheduleServices

_current: ScheduleServices | None = None


def set_current(services: ScheduleServices | None) -> None:
    global _current
    _current = services


def current() -> ScheduleServices | None:
    return _current
