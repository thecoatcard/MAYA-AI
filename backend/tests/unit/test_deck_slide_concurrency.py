"""Deck slides: MAYA_DECK_SLIDE_CONCURRENCY (default 6, clamped 1..8) and the retry behaviour."""

from __future__ import annotations

import asyncio
from typing import Any

import pytest

from app.tasks import deck_slides
from app.tasks.deck_slides import build_deck_by_slides
from app.tasks.dispatch import SlideSpec
from tests.unit.test_deck_slides import FakeGateway, _as_caller, _outline


class TrackingGateway(FakeGateway):
    """Slide calls take a little time so that overlapping calls can be counted."""

    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, **kwargs)
        self.in_flight = 0
        self.peak = 0

    async def generate_structured(self, **kwargs: Any) -> Any:
        if kwargs["response_schema"] is not SlideSpec:
            return await super().generate_structured(**kwargs)
        self.in_flight += 1
        self.peak = max(self.peak, self.in_flight)
        try:
            await asyncio.sleep(0.01)
            return await super().generate_structured(**kwargs)
        finally:
            self.in_flight -= 1


def test_default_is_six_and_the_range_is_one_to_eight() -> None:
    assert deck_slides.DEFAULT_SLIDE_CONCURRENCY == 6
    assert (deck_slides.MIN_SLIDE_CONCURRENCY, deck_slides.MAX_SLIDE_CONCURRENCY) == (1, 8)
    assert deck_slides.slide_concurrency_from_env({}) == 6


@pytest.mark.parametrize(
    ("raw", "expected"),
    [
        ("", 6),
        ("abc", 6),
        ("2.5", 6),
        ("0", 1),
        ("-4", 1),
        ("1", 1),
        ("3", 3),
        (" 7 ", 7),
        ("8", 8),
        ("9", 8),
        ("99", 8),
    ],
)
def test_environment_value_is_clamped(raw: str, expected: int) -> None:
    environ = {deck_slides.SLIDE_CONCURRENCY_ENV: raw}
    assert deck_slides.slide_concurrency_from_env(environ) == expected


def test_clamp_function_limits_both_ends() -> None:
    assert deck_slides.clamp_slide_concurrency(0) == 1
    assert deck_slides.clamp_slide_concurrency(50) == 8
    assert deck_slides.clamp_slide_concurrency(5) == 5


async def test_slides_in_flight_never_exceed_the_configured_limit(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deck_slides, "SLIDE_CONCURRENCY", 6)
    gateway = TrackingGateway(outline=_outline(12))
    plan = await build_deck_by_slides(
        _as_caller(gateway), prompt="base", slide_count=12, slide_schema=SlideSpec
    )
    assert plan.missing == []
    assert gateway.peak == 6  # overlapped, and the bound held


async def test_failed_slide_is_still_retried_under_concurrency(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.setattr(deck_slides, "SLIDE_CONCURRENCY", 6)
    gateway = TrackingGateway(outline=_outline(8), fail={3: 1})
    plan = await build_deck_by_slides(
        _as_caller(gateway), prompt="base", slide_count=8, slide_schema=SlideSpec
    )
    assert gateway.names().count("SlideSpec") == 9  # eight slides plus the one retry
    assert plan.missing == []
    assert gateway.peak <= 6
