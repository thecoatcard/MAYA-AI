"""Next occurrence of a cadence. Pure functions over an injected ``after`` moment (no clock).

DST rules: the local wall time is what the user chose. A time that does not exist on a spring
forward day runs at the first instant after the gap; a time that happens twice on a fall back
day runs at its first occurrence. A weekly cadence never skips a week because of a shift.
"""

from __future__ import annotations

from datetime import UTC, date, datetime, time, timedelta
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from app.schedules.models import DAY_NAMES, Cadence

_WEEKDAYS = ("mon", "tue", "wed", "thu", "fri")


class CadenceError(ValueError):
    """The cadence cannot be used (plain, user-facing text)."""


def parse_time(value: str) -> time:
    try:
        hours, minutes = value.strip().split(":")
        parsed = time(int(hours), int(minutes))
    except (ValueError, AttributeError):
        raise CadenceError("The time must look like 08:00 (24-hour HH:MM).") from None
    return parsed


def load_zone(name: str) -> ZoneInfo:
    try:
        return ZoneInfo(name)
    except (ZoneInfoNotFoundError, ValueError, OSError):
        raise CadenceError(
            f"'{name[:60]}' is not a known time zone (use e.g. Asia/Kolkata)."
        ) from None


def days_of(cadence: Cadence) -> tuple[str, ...]:
    """The weekday names a cadence runs on, in week order."""
    if cadence.type == "daily":
        return DAY_NAMES
    if cadence.type == "weekdays":
        return _WEEKDAYS
    return tuple(d for d in DAY_NAMES if d in cadence.days)


def local_to_utc(day: date, at: time, zone: ZoneInfo) -> datetime:
    """UTC instant of ``day`` ``at`` wall time in ``zone`` (gap: after the gap; fold: first)."""
    naive = datetime.combine(day, at)
    return naive.replace(tzinfo=zone).astimezone(UTC)


def next_run(cadence: Cadence, after: datetime) -> datetime:
    """The first occurrence strictly after ``after`` (an aware datetime), in UTC."""
    if after.tzinfo is None:
        raise ValueError("after must be timezone-aware")
    zone = load_zone(cadence.timezone)
    at = parse_time(cadence.time)
    allowed = days_of(cadence)
    if not allowed:
        raise CadenceError("Pick at least one day.")
    start = after.astimezone(zone).date() - timedelta(days=1)  # a zone shift can move the date
    for offset in range(0, 16):
        day = start + timedelta(days=offset)
        if DAY_NAMES[day.weekday()] not in allowed:
            continue
        moment = local_to_utc(day, at, zone)
        if moment > after:
            return moment
    raise CadenceError("No upcoming run could be computed for this cadence.")


def occurrences_between(
    cadence: Cadence, start_exclusive: datetime, end_inclusive: datetime, limit: int = 400
) -> list[datetime]:
    """Every planned occurrence in ``(start, end]`` (used to coalesce missed runs)."""
    found: list[datetime] = []
    cursor = start_exclusive
    while len(found) < limit:
        moment = next_run(cadence, cursor)
        if moment > end_inclusive:
            break
        found.append(moment)
        cursor = moment
    return found
