"""A fake decision service for offline tests (ADR-0029)."""

from __future__ import annotations

from collections.abc import Callable, Mapping, Sequence
from typing import Any

from app.decisions.questions import (
    Answer,
    Answers,
    ChoiceAnswer,
    NoulAnswer,
    Question,
    ScoreAnswer,
)


def noul_a(p: float) -> NoulAnswer:
    return NoulAnswer(p, 0.9)


def choice_a(pick: str, p: float = 0.95) -> ChoiceAnswer:
    return ChoiceAnswer(pick, {pick: p}, 0.9)


def score_a(value: float) -> ScoreAnswer:
    return ScoreAnswer(value, None, (), 0.9)


class FakeDecider:
    """Answers from a mapping key -> Answer (or a callable). None answers mean no opinion."""

    def __init__(
        self,
        answers: Mapping[str, Answer] | Callable[[str, Sequence[Question]], Answers | None] | None,
        *,
        enabled: bool = True,
    ) -> None:
        self._answers = answers
        self._enabled = enabled
        self.calls: list[tuple[str, Any, list[Question]]] = []

    async def decide(
        self,
        purpose: str,
        input_value: Any,
        questions: Sequence[Question],
        *,
        timeout: float | None = None,  # noqa: ASYNC109
        background: bool = False,
    ) -> Answers | None:
        self.calls.append((purpose, input_value, list(questions)))
        if self._answers is None:
            return None
        if callable(self._answers):
            return self._answers(purpose, questions)
        return Answers(dict(self._answers))

    def available(self, purpose: str) -> bool:
        return self._enabled

    def status(self) -> dict[str, Any]:
        return {"enabled": self._enabled}
