"""Process-wide handle to the decision service, so call sites need no new constructor argument.

``decide`` is the one entry point integrations use. With no service installed (tests, no key,
switch off) it returns None at once, which every caller treats as "no opinion".
"""

from __future__ import annotations

from collections.abc import Mapping, Sequence
from typing import Any, Protocol

from app.decisions.questions import Answers, Question


class Decider(Protocol):
    async def decide(
        self,
        purpose: str,
        input_value: str | Sequence[Mapping[str, Any]],
        questions: Sequence[Question],
        *,
        timeout: float | None = None,  # noqa: ASYNC109
        background: bool = False,
    ) -> Answers | None: ...

    def available(self, purpose: str) -> bool: ...

    def status(self) -> dict[str, Any]: ...


_current: Decider | None = None


def set_current(service: Decider | None) -> None:
    global _current
    _current = service


def current() -> Decider | None:
    return _current


def available(purpose: str) -> bool:
    svc = _current
    return svc is not None and svc.available(purpose)


async def decide(
    purpose: str,
    input_value: str | Sequence[Mapping[str, Any]],
    questions: Sequence[Question],
    *,
    timeout: float | None = None,  # noqa: ASYNC109
    background: bool = False,
) -> Answers | None:
    """Ask the installed service; None when there is none or it has no answer. Never raises."""
    svc = _current
    if svc is None:
        return None
    try:
        return await svc.decide(
            purpose, input_value, questions, timeout=timeout, background=background
        )
    except Exception:  # noqa: BLE001 - a decision must never break the caller
        return None
