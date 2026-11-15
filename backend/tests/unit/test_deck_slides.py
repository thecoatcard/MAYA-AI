"""Slide-by-slide deck generation: outline, one call per slide, retries, cap, budget, edit data."""

from __future__ import annotations

import asyncio
import json
import re
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.automation.design import DesignPalette, DesignSpec
from app.gateway.exceptions import BudgetExceededError
from app.tasks import deck_slides
from app.tasks.deck_slides import (
    DeckOutline,
    OutlineSlide,
    build_deck_by_slides,
    call_cap,
    regenerate_slide,
    slide_block,
)
from app.tasks.dispatch import DocumentSpec, FleetSupport, SlideSpec
from app.tasks.queue import SubtaskJob

_NUMBER = re.compile(r"Write slide (\d+) of")


def _slide_number(prompt: str) -> int:
    match = _NUMBER.search(prompt)
    assert match is not None, prompt
    return int(match.group(1))


class FakeGateway:
    """Answers each structured call by its response schema, like the real gateway would."""

    client = None

    def __init__(
        self,
        outline: DeckOutline | None = None,
        spec: DocumentSpec | None = None,
        *,
        fail: dict[int, int] | None = None,
        empty: set[int] | None = None,
        hang: set[int] | None = None,
        budget_on: int | None = None,
        bullets: dict[int, list[str]] | None = None,
    ) -> None:
        self.outline = outline
        self.spec = spec
        self.fail = dict(fail or {})  # slide -> calls that raise (-1 = every call)
        self.empty = set(empty or ())  # slides that come back as a title alone
        self.hang = set(hang or ())  # slides whose call never returns in time
        self.budget_on = budget_on  # slide whose call hits the gateway's budget cap
        self.bullets = dict(bullets or {})
        self.calls: list[tuple[str, str]] = []

    async def generate_structured(self, **kwargs: Any) -> Any:
        schema = kwargs["response_schema"]
        prompt = kwargs["contents"][0]
        self.calls.append((schema.__name__, prompt))
        if schema is DeckOutline:
            if self.outline is None:
                raise RuntimeError("outline call failed")
            return self.outline
        if schema is DocumentSpec:
            return self.spec
        number = _slide_number(prompt)
        if number in self.hang:
            await asyncio.sleep(1)
        if self.budget_on == number:
            raise RuntimeError("Model could not produce the structured input") from (
                BudgetExceededError("Task reached its financial budget cap")
            )
        left = self.fail.get(number, 0)
        if left == -1 or left > 0:
            if left > 0:
                self.fail[number] = left - 1
            raise RuntimeError("provider unavailable")
        if number in self.empty:
            return SlideSpec(layout="bullets", title=f"Slide {number} title")
        bullets = self.bullets.get(number, [f"Point {number} a", f"Point {number} b"])
        return SlideSpec(layout="bullets", title=f"Slide {number} title", bullets=bullets)

    def names(self) -> list[str]:
        return [name for name, _ in self.calls]

    def slide_prompts(self) -> list[str]:
        return [p for name, p in self.calls if name == "SlideSpec"]


def _outline(count: int, design: DesignSpec | None = None) -> DeckOutline:
    slides = [
        OutlineSlide(
            title=f"Slide {i} title",
            layout="title" if i == 1 else "bullets",
            goal=f"Goal {i}",
            key_points=[f"Fact {i}"],
        )
        for i in range(1, count + 1)
    ]
    return DeckOutline(
        title="Scooter deck",
        filename="scooters.pptx",
        theme="executive",
        design=design,
        slides=slides,
    )


def _single_spec() -> DocumentSpec:
    return DocumentSpec(
        file_type="pptx",
        filename="scooters.pptx",
        title="Scooters",
        slides=[
            SlideSpec(layout="title", title="Scooters"),
            SlideSpec(layout="bullets", title="Fact", bullets=["one"]),
        ],
    )


def _job() -> SubtaskJob:
    return SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="document",
    )


def _support(gateway: FakeGateway, tmp_path: Path) -> FleetSupport:
    return FleetSupport(gateway=gateway, artifacts_dir=tmp_path)  # type: ignore[arg-type]


def _desc(count: int) -> str:
    return f"Make a {count}-slide deck on electric scooters"


def _as_caller(gateway: FakeGateway):  # type: ignore[no-untyped-def]
    async def call(prompt: str, schema: type, system: str) -> Any:
        return await gateway.generate_structured(
            contents=[prompt], response_schema=schema, system_instruction=system
        )

    return call


@pytest.mark.parametrize("count", [6, 10])
async def test_outline_then_one_call_per_slide_returns_every_block(
    tmp_path: Path, count: int
) -> None:
    gateway = FakeGateway(outline=_outline(count))
    ctx = await _support(gateway, tmp_path).build_context(_job(), {"description": _desc(count)})

    assert gateway.names() == ["DeckOutline"] + ["SlideSpec"] * count
    assert [s["title"] for s in ctx["slides"]] == [f"Slide {i} title" for i in range(1, count + 1)]
    assert ctx["slide_plan"]["missing"] == []
    assert ctx["file_type"] == "pptx" and ctx["expected_slides"] == count


@pytest.mark.parametrize("count", [4, 5])
async def test_short_deck_keeps_the_single_structured_call(tmp_path: Path, count: int) -> None:
    gateway = FakeGateway(spec=_single_spec())
    ctx = await _support(gateway, tmp_path).build_context(_job(), {"description": _desc(count)})

    assert gateway.names() == ["DocumentSpec"]
    assert ctx["slide_plan"] is None
    assert [s["layout"] for s in ctx["slides"]] == ["cover", "bullets"]


async def test_outline_failure_falls_back_to_the_single_call(tmp_path: Path) -> None:
    gateway = FakeGateway(outline=None, spec=_single_spec())
    ctx = await _support(gateway, tmp_path).build_context(_job(), {"description": _desc(8)})

    assert gateway.names() == ["DeckOutline", "DocumentSpec"]
    assert ctx["slide_plan"] is None and len(ctx["slides"]) == 2


async def test_outline_of_the_wrong_type_is_an_outline_error() -> None:
    async def wrong(prompt: str, schema: type, system: str) -> Any:
        return _single_spec()

    with pytest.raises(deck_slides.DeckOutlineError):
        await build_deck_by_slides(wrong, prompt="x", slide_count=6, slide_schema=SlideSpec)


async def test_failed_slide_retries_once_and_then_succeeds(tmp_path: Path) -> None:
    gateway = FakeGateway(outline=_outline(10), fail={3: 1})
    ctx = await _support(gateway, tmp_path).build_context(_job(), {"description": _desc(10)})

    assert gateway.names().count("SlideSpec") == 11  # ten slides plus one retry
    assert len(ctx["slides"]) == 10 and ctx["slide_plan"]["missing"] == []


async def test_slide_that_fails_twice_is_marked_missing_and_reported(tmp_path: Path) -> None:
    gateway = FakeGateway(outline=_outline(10), fail={3: -1})
    ctx = await _support(gateway, tmp_path).build_context(_job(), {"description": _desc(10)})

    assert gateway.names().count("SlideSpec") == 11  # the retry was made, then given up
    assert len(ctx["slides"]) == 9
    assert ctx["slide_plan"]["missing"] == [3]
    assert "provider unavailable" not in json.dumps(ctx["slide_plan"])  # no raw provider text
    assert any("slide 3" in w and "could not produce" in w for w in ctx["spec_warnings"])


async def test_title_only_slide_is_rejected_and_reported(tmp_path: Path) -> None:
    gateway = FakeGateway(outline=_outline(7), empty={4})
    ctx = await _support(gateway, tmp_path).build_context(_job(), {"description": _desc(7)})

    assert ctx["slide_plan"]["missing"] == [4]
    assert "was invalid" in ctx["slide_plan"]["failures"]["4"]
    assert all(s.get("bullets") for s in ctx["slides"][1:])


def test_slide_block_rejects_title_only_and_missing_columns() -> None:
    with pytest.raises(ValueError):
        slide_block(
            SlideSpec(layout="bullets", title="T"), OutlineSlide(layout="bullets", title="T")
        )
    with pytest.raises(ValueError):
        slide_block(
            SlideSpec(layout="two_column", title="T"),
            OutlineSlide(layout="two-column", title="T"),
        )


async def test_invented_email_is_stripped_from_the_deck(tmp_path: Path) -> None:
    gateway = FakeGateway(
        outline=_outline(7),
        bullets={2: ["Scooter sales growth", "Write to sales@made-up.example for a quote"]},
    )
    ctx = await _support(gateway, tmp_path).build_context(_job(), {"description": _desc(7)})

    assert "made-up.example" not in json.dumps(ctx["slides"])
    assert any(h["attempt"] == "contacts_removed" for h in ctx["spec_healing"])


async def test_design_from_the_outline_reaches_every_slide(tmp_path: Path) -> None:
    design = DesignSpec(base="midnight", palette=DesignPalette(primary="#123456"))
    gateway = FakeGateway(outline=_outline(8, design=design))
    ctx = await _support(gateway, tmp_path).build_context(_job(), {"description": _desc(8)})

    assert ctx["design"]["palette"]["primary"] == "#123456"
    prompts = gateway.slide_prompts()
    assert len(prompts) == 8
    assert all("#123456" in p and "midnight" in p for p in prompts)


async def test_each_slide_call_sees_only_the_previous_slide_title(tmp_path: Path) -> None:
    gateway = FakeGateway(outline=_outline(6))
    await _support(gateway, tmp_path).build_context(_job(), {"description": _desc(6)})

    by_number = {_slide_number(p): p for p in gateway.slide_prompts()}
    assert 'Previous slide title: "Slide 2 title"' in by_number[3]
    assert "This is the first slide." in by_number[1]


async def test_call_cap_bounds_every_attempt_including_retries() -> None:
    gateway = FakeGateway(outline=_outline(10), fail={n: -1 for n in range(1, 11)})
    with pytest.raises(RuntimeError, match="No slide could be generated"):
        await build_deck_by_slides(
            _as_caller(gateway),
            prompt="base",
            slide_count=10,
            slide_schema=SlideSpec,
        )
    assert call_cap(10) == 14  # outline + 10 slides + max(2, 10 // 3) retries
    assert len(gateway.calls) == 14


async def test_cap_leaves_a_partial_deck_with_reasons_for_the_rest() -> None:
    gateway = FakeGateway(outline=_outline(10), fail={n: -1 for n in range(2, 11)})
    plan = await build_deck_by_slides(
        _as_caller(gateway), prompt="base", slide_count=10, slide_schema=SlideSpec
    )
    assert plan.calls == 14 and len(gateway.calls) == 14
    assert plan.missing == list(range(2, 11))
    assert any("call limit" in reason for reason in plan.failures.values())


def test_call_cap_formula() -> None:
    assert call_cap(3) == 6
    assert call_cap(10) == 14
    assert call_cap(60) == 81


async def test_budget_error_stops_every_remaining_call(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deck_slides, "SLIDE_CONCURRENCY", 1)
    gateway = FakeGateway(outline=_outline(6), budget_on=2)
    plan = await build_deck_by_slides(
        _as_caller(gateway), prompt="base", slide_count=6, slide_schema=SlideSpec
    )
    assert gateway.names().count("SlideSpec") == 2  # slide 1 and the call that hit the cap
    assert plan.missing == [2, 3, 4, 5, 6]
    assert all("cost budget" in plan.failures[n] for n in plan.missing)


async def test_slow_slide_times_out_and_is_reported(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(deck_slides, "SLIDE_TIMEOUT_SECONDS", 0.05)
    gateway = FakeGateway(outline=_outline(4), hang={2})
    plan = await build_deck_by_slides(
        _as_caller(gateway), prompt="base", slide_count=4, slide_schema=SlideSpec
    )
    assert plan.missing == [2]
    assert "timed out" in plan.failures[2]


async def test_regenerate_slide_asks_only_for_that_slide() -> None:
    gateway = FakeGateway(outline=_outline(6))
    plan = await build_deck_by_slides(
        _as_caller(gateway), prompt="base", slide_count=6, slide_schema=SlideSpec
    )
    stored = plan.to_dict()
    gateway.calls.clear()

    updated = await regenerate_slide(
        _as_caller(gateway), stored, 3, SlideSpec, change="make it shorter"
    )

    assert gateway.names() == ["SlideSpec"]
    prompt = gateway.slide_prompts()[0]
    assert "Write slide 3 of 6" in prompt and "make it shorter" in prompt
    assert updated["slides"][2]["title"] == "Slide 3 title"
    for index in (0, 1, 3, 4, 5):
        assert updated["slides"][index] == stored["slides"][index]


async def test_slide_plan_is_stored_in_the_pptx_proof(tmp_path: Path) -> None:
    from app.agents.specialized.document import DocumentAgent

    gateway = FakeGateway(outline=_outline(10))
    job = _job()
    ctx = await _support(gateway, tmp_path).build_context(job, {"description": _desc(10)})

    workspace = tmp_path / str(job.workspace_id)
    workspace.mkdir(parents=True, exist_ok=True)
    agent = DocumentAgent(artifacts_base_dir=tmp_path)
    built = await agent._produce(
        ctx, "scooters.pptx", "pptx", workspace / "scooters.pptx", workspace, job
    )
    assert isinstance(built, tuple)
    proof, _ = built

    assert proof["status"] == "verified"
    assert proof["slide_plan"]["outline"]["title"] == "Scooter deck"
    assert len(proof["slide_plan"]["slides"]) == 10
    assert proof["slide_plan"]["missing"] == []
