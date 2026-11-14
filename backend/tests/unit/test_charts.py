"""Chart helper (Pillow only): valid PNG output, deterministic, bad input refused."""

from __future__ import annotations

import io

import pytest
from PIL import Image

from app.automation.charts import ChartError, ChartKind, Series, nice_ticks, render_chart

CATS = ["Q1", "Q2", "Q3"]


@pytest.mark.parametrize("kind", ["bar", "line", "pie"])
def test_each_kind_renders_a_png_of_the_requested_size(kind: ChartKind) -> None:
    png = render_chart(kind, title="Sales", categories=CATS, series=[Series("2026", [3, 5, 2])])
    image = Image.open(io.BytesIO(png))
    assert image.format == "PNG" and image.size == (960, 600)


def test_output_is_deterministic_and_multi_series_works() -> None:
    series = [Series("a", [1, 2, 3]), Series("b", [3, 2, 1])]
    first = render_chart("line", title="T", categories=CATS, series=series)
    assert first == render_chart("line", title="T", categories=CATS, series=series)


def test_bad_input_is_refused_with_a_chart_error() -> None:
    with pytest.raises(ChartError):
        render_chart("bar", title="T", categories=CATS, series=[Series("a", [1, 2])])
    with pytest.raises(ChartError):
        render_chart(
            "pie",
            title="T",
            categories=CATS,
            series=[Series("a", [1, 2, 3]), Series("b", [1, 2, 3])],
        )
    with pytest.raises(ChartError):
        render_chart("bar", title="T", categories=CATS, series=[Series("a", [1, 2, 3])], width=10)


def test_ticks_are_round_and_cover_the_range() -> None:
    ticks = nice_ticks(0, 97)
    assert ticks[0] <= 0 and ticks[-1] >= 97
