"""Validation and sanitising of schedule input (Settings form, chat card, voice). Plain messages."""

from __future__ import annotations

import re
from typing import Any, Final

from app.agents.specialized.email import normalize_recipient
from app.schedules.models import (
    DAY_NAMES,
    DEFAULT_SOURCES,
    FILE_FORMATS,
    MAX_RECIPIENTS,
    MAX_SOURCES,
    Cadence,
    Deliver,
    Depth,
)
from app.schedules.next_run import CadenceError, load_zone, parse_time

NAME_MAX: Final = 80
INSTRUCTIONS_MAX: Final = 2000
INSTRUCTIONS_MIN: Final = 8
TOPIC_MAX: Final = 200

_CONTROL = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f]")
_SECRET_PATTERNS: Final = (
    re.compile(r"AIza[0-9A-Za-z_\-]{30,}"),
    re.compile(r"\bsk-[A-Za-z0-9_\-]{20,}"),
    re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"),
    re.compile(r"\bsecret_[A-Za-z0-9]{30,}"),
    re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"),
    re.compile(r"(?i)\b(?:password|passwd|api[_ -]?key|secret|token)\s*[:=]\s*\S{6,}"),
    re.compile(r"\b[A-Fa-f0-9]{40,}\b"),
)
_DAY_ALIASES: Final = {
    "mon": "mon",
    "monday": "mon",
    "tue": "tue",
    "tues": "tue",
    "tuesday": "tue",
    "wed": "wed",
    "wednesday": "wed",
    "thu": "thu",
    "thur": "thu",
    "thurs": "thu",
    "thursday": "thu",
    "fri": "fri",
    "friday": "fri",
    "sat": "sat",
    "saturday": "sat",
    "sun": "sun",
    "sunday": "sun",
}
FORMATS: Final = ("chat", *FILE_FORMATS)


class ScheduleValidationError(ValueError):
    """Invalid schedule input. ``field`` names the form field the message belongs to."""

    def __init__(self, message: str, field: str = "") -> None:
        super().__init__(message)
        self.field = field


def clean_text(value: Any, label: str, *, maximum: int, minimum: int = 0) -> str:
    text = _CONTROL.sub("", str(value or "")).strip()
    text = re.sub(r"[ \t]+\n", "\n", text)
    if len(text) < minimum:
        raise ScheduleValidationError(f"The {label} is too short.", label)
    if len(text) > maximum:
        raise ScheduleValidationError(
            f"The {label} is too long (at most {maximum} characters).", label
        )
    for pattern in _SECRET_PATTERNS:
        if pattern.search(text):
            raise ScheduleValidationError(
                f"The {label} looks like it contains a password or key. Remove it: a schedule "
                "never stores secrets.",
                label,
            )
    return text


def normalise_days(raw: Any) -> list[str]:
    """Weekday names ('mon'..'sun'); also accepts full names and 0..6 with Monday = 0."""
    found: list[str] = []
    for item in raw if isinstance(raw, list | tuple) else []:
        if isinstance(item, bool):
            raise ScheduleValidationError("Days must be weekday names such as 'mon'.", "days")
        if isinstance(item, int):
            if not 0 <= item <= 6:
                raise ScheduleValidationError(
                    "A day number must be 0 (Monday) to 6 (Sunday).", "days"
                )
            name = DAY_NAMES[item]
        else:
            name = _DAY_ALIASES.get(str(item).strip().lower(), "")
        if not name:
            raise ScheduleValidationError(f"'{str(item)[:20]}' is not a weekday.", "days")
        if name not in found:
            found.append(name)
    return [d for d in DAY_NAMES if d in found]


def validate_cadence(cadence: Cadence) -> Cadence:
    try:
        parse_time(cadence.time)
        load_zone(cadence.timezone)
    except CadenceError as error:
        raise ScheduleValidationError(str(error), "cadence") from None
    days = normalise_days(cadence.days)
    if cadence.type in ("weekly", "custom_days") and not days:
        raise ScheduleValidationError("Pick at least one day of the week.", "cadence")
    if cadence.type == "weekly" and len(days) != 1:
        raise ScheduleValidationError("A weekly schedule runs on exactly one day.", "cadence")
    hours, minutes = cadence.time.strip().split(":")
    return Cadence(
        type=cadence.type,
        time=f"{int(hours):02d}:{int(minutes):02d}",
        days=days if cadence.type in ("weekly", "custom_days") else [],
        timezone=cadence.timezone.strip(),
    )


def validate_deliver(deliver: Deliver) -> Deliver:
    recipients: list[str] = []
    for raw in deliver.recipients:
        try:
            address = normalise_address(raw)
        except ValueError as error:
            raise ScheduleValidationError(str(error), "deliver") from None
        if address not in recipients:
            recipients.append(address)
    if len(recipients) > MAX_RECIPIENTS:
        raise ScheduleValidationError(
            f"At most {MAX_RECIPIENTS} mail recipients per schedule.", "deliver"
        )
    if deliver.mail and not recipients:
        raise ScheduleValidationError("Choose who the mail goes to.", "deliver")
    if not (deliver.mail or deliver.chat_artifact or deliver.voice_on_open):
        # Always keep one place the result lands: the chat / Artifacts card.
        return Deliver(
            mail=False,
            recipients=recipients,
            chat_artifact=True,
            voice_on_open=False,
        )
    return Deliver(
        mail=deliver.mail,
        recipients=recipients,
        chat_artifact=deliver.chat_artifact,
        voice_on_open=deliver.voice_on_open,
    )


def normalise_address(raw: str) -> str:
    return normalize_recipient(raw).lower()


def validate_depth(depth: Depth) -> Depth:
    target = depth.sources_target if depth.sources_target else DEFAULT_SOURCES
    if not 1 <= target <= MAX_SOURCES:
        raise ScheduleValidationError(f"Sources per run must be 1 to {MAX_SOURCES}.", "depth")
    return Depth(sources_target=target, use_deep_research=depth.use_deep_research)


def validate_format(value: str) -> str:
    if value not in FORMATS:
        raise ScheduleValidationError(
            "The format must be one of chat, pdf, docx, pptx, xlsx or md.", "format"
        )
    return value


def estimate_model_calls(depth: Depth) -> int:
    """A rough honest estimate for the preview card (not a promise)."""
    if depth.use_deep_research:
        return min(40, 14 + depth.sources_target)
    return max(6, 4 + depth.sources_target // 2 + 2)
