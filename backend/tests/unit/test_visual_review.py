"""Visual review: deterministic checks on fixtures, the strict vision schema, the bounded repair
loop with a fake gateway and fake page images, and the summary used by the task proof."""

from __future__ import annotations

import hashlib
from collections.abc import Callable
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest
from pydantic import ValidationError

from app.automation import visual_review as vr
from app.automation.preview import PreviewResult
from app.automation.visual_review import (
    PageFacts,
    TextBox,
    VisualReview,
    apply_pptx_fixes,
    check_pages,
    check_palette,
    contrast_ratio,
    plan_fixes,
    pptx_facts,
    run_visual_loop,
    summarize_built_file,
)
from app.gateway.registry import AGENT_ALIAS_CHOICES

PNG = b"\x89PNG\r\n\x1a\n" + b"0" * 64


def build_fixture_deck(path: Path) -> Path:
    """Five slides: title only, text overflow, tiny font, empty, and a clean slide."""
    from pptx import Presentation
    from pptx.util import Inches, Pt

    deck = Presentation()
    deck.slide_width = Inches(13.333)
    deck.slide_height = Inches(7.5)
    title_only = deck.slides.add_slide(deck.slide_layouts[5])
    title_only.shapes.title.text = "Only a title"
    overflow = deck.slides.add_slide(deck.slide_layouts[6])
    box = overflow.shapes.add_textbox(Inches(1), Inches(1), Inches(3), Inches(0.5))
    box.text_frame.text = "word " * 400
    box.text_frame.paragraphs[0].runs[0].font.size = Pt(18)
    tiny = deck.slides.add_slide(deck.slide_layouts[6])
    small = tiny.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(2))
    small.text_frame.text = "Fine print about 2026 revenue of 42"
    small.text_frame.paragraphs[0].runs[0].font.size = Pt(6)
    deck.slides.add_slide(deck.slide_layouts[6])
    clean = deck.slides.add_slide(deck.slide_layouts[6])
    body = clean.shapes.add_textbox(Inches(1), Inches(1), Inches(8), Inches(2))
    body.text_frame.text = "Revenue grew 42 percent in 2026 across all regions."
    body.text_frame.paragraphs[0].runs[0].font.size = Pt(18)
    deck.save(str(path))
    return path


def _box(page: int = 1, **overrides: Any) -> TextBox:
    values: dict[str, Any] = {
        "page": page,
        "shape_index": 0,
        "text": "Some text",
        "font_pt": 18.0,
        "left_in": 1.0,
        "top_in": 1.0,
        "width_in": 8.0,
        "height_in": 2.0,
        "color": None,
        "is_title": False,
        "needed_in": 1.0,
        "available_in": 2.0,
    }
    values.update(overrides)
    return TextBox(**values)


def _page(
    *boxes: TextBox, number: int = 1, has_visual: bool = False, bg: str | None = None
) -> PageFacts:
    return PageFacts(number, 13.333, 7.5, tuple(boxes), has_visual, bg)


# ------------------------------------------------------------------ deterministic checks


def test_contrast_ratio_reference_values() -> None:
    assert contrast_ratio("#000000", "#FFFFFF") == pytest.approx(21.0, abs=0.01)
    assert contrast_ratio("#FFFFFF", "#FFFFFF") == pytest.approx(1.0)
    assert contrast_ratio("#BBBBBB", "#FFFFFF") < 4.5


def test_low_contrast_text_is_flagged_and_strong_text_is_not() -> None:
    weak = check_pages([_page(_box(color="#BBBBBB"), bg="#FFFFFF")])
    strong = check_pages([_page(_box(color="#111111"), bg="#FFFFFF")])
    assert [f.category for f in weak] == ["low_contrast"]
    assert strong == []


def test_palette_contrast_flags_the_theme_pair() -> None:
    theme = SimpleNamespace(colors=SimpleNamespace(text="#CCCCCC", page_bg="#FFFFFF"))
    findings = check_palette(theme)  # type: ignore[arg-type]
    assert [f.category for f in findings] == ["low_contrast"]
    assert findings[0].page == 0


def test_overflow_estimate_flags_an_overfull_box() -> None:
    findings = check_pages([_page(_box(needed_in=5.0, available_in=2.0))])
    assert findings[0].category == "overflow" and findings[0].severity == "high"
    assert findings[0].fix == "adjust_spacing"
    assert check_pages([_page(_box(needed_in=1.0, available_in=2.0))]) == []


def test_title_only_and_empty_pages_are_flagged() -> None:
    title = _box(is_title=True, text="Only a title")
    flagged = check_pages([_page(title, number=2), _page(number=3)])
    assert [(f.page, f.category) for f in flagged] == [(2, "title_only"), (3, "empty_page")]
    with_picture = check_pages([_page(title, has_visual=True)])
    assert with_picture == []


def test_tiny_font_is_flagged_with_a_font_floor_fix() -> None:
    findings = check_pages([_page(_box(font_pt=6.0))])
    assert findings[0].category == "tiny_font" and findings[0].severity == "high"
    assert findings[0].fix == "raise_font_floor" and findings[0].font_floor_pt == 10.0


def test_margin_issue_is_reported_but_not_repaired() -> None:
    findings = check_pages([_page(_box(left_in=0.05))])
    assert findings[0].category == "margin" and findings[0].fix == "none"
    assert findings[0].severity == "low"


def test_pptx_fixture_deck_is_read_and_checked(tmp_path: Path) -> None:
    deck = build_fixture_deck(tmp_path / "deck.pptx")
    pages = pptx_facts(deck)
    assert len(pages) == 5
    categories = {(f.page, f.category) for f in check_pages(pages)}
    assert (1, "title_only") in categories
    assert (2, "overflow") in categories
    assert (3, "tiny_font") in categories
    assert (4, "empty_page") in categories
    assert not any(page == 5 for page, _ in categories)


def test_summary_for_built_files(tmp_path: Path) -> None:
    deck = build_fixture_deck(tmp_path / "deck.pptx")
    summary = summarize_built_file(deck, "pptx")
    assert summary["status"] == "issues_found" and summary["deterministic"] == "checked"
    assert summary["vision_review"].startswith("not available")
    assert summarize_built_file(tmp_path / "x.pdf", "pdf")["status"] == "not_checked"
    broken = summarize_built_file(tmp_path / "missing.pptx", "pptx")
    assert broken["status"] == "error"


# -------------------------------------------------------------------------- vision schema


def _issue(
    page: int = 1,
    severity: str = "high",
    category: str = "overflow",
    fix: dict[str, Any] | None = None,
) -> dict[str, Any]:
    return {
        "page": page,
        "severity": severity,
        "category": category,
        "description": "Text runs past the box.",
        "fix": fix or {"action": "adjust_spacing"},
    }


def test_valid_review_parses() -> None:
    review = VisualReview.model_validate(
        {"issues": [_issue(fix={"action": "adjust_spacing", "shape_index": 0})], "summary": "ok"}
    )
    assert review.issues[0].fix.shape_index == 0


@pytest.mark.parametrize(
    "bad",
    [
        {"issues": [dict(_issue(), extra_field=1)]},
        {"issues": [dict(_issue(), severity="critical")]},
        {"issues": [{**_issue(), "fix": {"action": "delete_slide"}}]},
        {"issues": [{**_issue(), "fix": {"action": "shorten_text", "new_text": "x" * 2000}}]},
        {"issues": [{**_issue(), "page": 0}]},
        {"issues": "not a list"},
    ],
)
def test_invalid_review_is_refused(bad: dict[str, Any]) -> None:
    with pytest.raises(ValidationError):
        VisualReview.model_validate(bad)


# ----------------------------------------------------------------------- repair loop


class FakeGateway:
    """Returns queued answers for ``generate_structured``; counts the calls."""

    def __init__(self, *answers: Any) -> None:
        self.answers = list(answers)
        self.calls: list[dict[str, Any]] = []

    async def generate_structured(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if not self.answers:
            return VisualReview()
        answer = self.answers.pop(0)
        if isinstance(answer, Exception):
            raise answer
        return answer


def _fake_render(calls: list[int]) -> Callable[..., PreviewResult]:
    """A render stand-in: one PNG per slide, no LibreOffice or browser needed."""

    def render(
        source: Path, kind: str, *, out_dir: Path, document: Any = None, renderer: Any = None
    ) -> PreviewResult:
        calls.append(1)
        out_dir.mkdir(parents=True, exist_ok=True)
        pages = []
        for number in range(1, 6):
            target = out_dir / f"page-{number:03d}.png"
            target.write_bytes(PNG)
            pages.append(target)
        return PreviewResult("libreoffice", pages, 5, False, ["fake render"])

    return render


@pytest.fixture
def fake_render(monkeypatch: pytest.MonkeyPatch) -> list[int]:
    calls: list[int] = []
    monkeypatch.setattr(vr, "render_preview", _fake_render(calls))
    return calls


def _review(*issues: dict[str, Any]) -> VisualReview:
    return VisualReview.model_validate({"issues": list(issues)})


def _sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


async def test_repair_loop_applies_allowed_fixes_and_stops_after_two_rounds(
    tmp_path: Path, fake_render: list[int]
) -> None:
    deck = build_fixture_deck(tmp_path / "deck.pptx")
    before = _sha(deck)
    gateway = FakeGateway(
        *[
            _review(_issue(page=2, fix={"action": "adjust_spacing", "shape_index": 0}))
            for _ in range(5)
        ]
    )
    result = await run_visual_loop(
        deck, "pptx", work_dir=tmp_path / "work", gateway=gateway, alias="lite"
    )
    assert len(gateway.calls) == 3  # round 0, after fix 1, after fix 2: never a fourth
    assert len(result.rounds) == 3 and len(fake_render) == 3
    assert result.applied_fixes  # the spacing fix was applied to the copy
    assert _sha(deck) == before  # the source is never changed
    assert result.vision_review == "ran"
    assert result.status == "issues_remaining"  # the overfull box is still reported honestly


async def test_repair_loop_refuses_a_shortened_text_that_changes_a_number(
    tmp_path: Path, fake_render: list[int]
) -> None:
    deck = build_fixture_deck(tmp_path / "deck.pptx")
    changed = _review(
        _issue(
            page=5,
            severity="medium",
            category="readability",
            fix={
                "action": "shorten_text",
                "shape_index": 0,
                "new_text": "Revenue grew 43 percent.",
            },
        )
    )
    gateway = FakeGateway(changed, VisualReview(), VisualReview())
    result = await run_visual_loop(
        deck, "pptx", work_dir=tmp_path / "work", gateway=gateway, alias="lite"
    )
    assert not any("slide 5 shorten_text" in a for a in result.applied_fixes)
    assert any("refused" in s and "numbers" in s for s in result.skipped_fixes)


async def test_invalid_vision_answer_means_no_changes(
    tmp_path: Path, fake_render: list[int]
) -> None:
    deck = build_fixture_deck(tmp_path / "deck.pptx")
    before = _sha(deck)
    gateway = FakeGateway(ValueError("the model answered with invalid JSON"))
    result = await run_visual_loop(
        deck, "pptx", work_dir=tmp_path / "work", gateway=gateway, alias="lite"
    )
    assert result.vision_review.startswith("invalid answer, no changes")
    assert result.applied_fixes == [] and result.repaired_copy is None
    assert len(result.rounds) == 1  # the loop stops: nothing was changed to check again
    assert _sha(deck) == before


async def test_schema_invalid_dict_answer_means_no_changes(
    tmp_path: Path, fake_render: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    deck = build_fixture_deck(tmp_path / "deck.pptx")

    class DictGateway:
        async def generate_structured(self, **kwargs: Any) -> Any:
            return {"issues": [{"page": 2, "rogue": True}]}

    result = await run_visual_loop(
        deck, "pptx", work_dir=tmp_path / "work", gateway=DictGateway(), alias="lite"
    )
    assert result.vision_review.startswith("invalid answer")
    assert result.applied_fixes == [] and result.repaired_copy is None


async def test_vision_not_configured_runs_deterministic_checks_only(
    tmp_path: Path, fake_render: list[int], monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(vr.VISION_ALIAS_ENV, raising=False)
    deck = build_fixture_deck(tmp_path / "deck.pptx")
    gateway = FakeGateway()
    result = await run_visual_loop(deck, "pptx", work_dir=tmp_path / "work", gateway=gateway)
    assert gateway.calls == []
    assert result.vision_review == f"not available: {vr.VISION_ALIAS_ENV} is not set"
    assert result.applied_fixes  # deterministic overflow and tiny-font fixes still ran
    assert result.rounds[0]["findings"]


async def test_repaired_copy_is_kept_only_when_it_improves(
    tmp_path: Path, fake_render: list[int]
) -> None:
    deck = build_fixture_deck(tmp_path / "deck.pptx")
    gateway = FakeGateway(VisualReview(), VisualReview())
    result = await run_visual_loop(
        deck, "pptx", work_dir=tmp_path / "work", gateway=gateway, alias="lite"
    )
    # Deterministic tiny-font and overflow fixes lower the blocking count, so a copy is kept.
    assert result.repaired_copy is not None
    assert result.repaired_copy.parent == tmp_path / "work"
    assert result.repaired_copy != deck


def test_split_slide_is_reported_not_applied(tmp_path: Path) -> None:
    deck = build_fixture_deck(tmp_path / "deck.pptx")
    plan = plan_fixes(
        [],
        _review(_issue(page=2, fix={"action": "split_slide", "shape_index": 0})).issues,
    )
    outcome = apply_pptx_fixes(deck, tmp_path / "out.pptx", plan)
    assert outcome.applied == []
    assert any("rebuild" in s for s in outcome.skipped)


def test_font_floor_fix_raises_only_small_runs(tmp_path: Path) -> None:
    deck = build_fixture_deck(tmp_path / "deck.pptx")
    plan = plan_fixes(
        [],
        _review(
            _issue(
                page=3, fix={"action": "raise_font_floor", "shape_index": 0, "font_floor_pt": 10}
            )
        ).issues,
    )
    outcome = apply_pptx_fixes(deck, tmp_path / "out.pptx", plan)
    assert outcome.applied and not outcome.skipped
    from pptx import Presentation

    shape = Presentation(str(tmp_path / "out.pptx")).slides[2].shapes[0]
    assert shape.text_frame.paragraphs[0].runs[0].font.size.pt == 10


# ------------------------------------------------ the statement of which review ran


def test_without_a_confirmed_alias_every_path_states_deterministic_only(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(vr.VISION_ALIAS_ENV, raising=False)
    assert vr.review_statement() == vr.DETERMINISTIC_ONLY
    deck = build_fixture_deck(tmp_path / "deck.pptx")
    summary = summarize_built_file(deck, "pptx")
    assert summary["statement"] == vr.DETERMINISTIC_ONLY and summary["mode"] == "deterministic"


async def test_review_built_file_never_calls_a_model_without_a_confirmed_alias(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.delenv(vr.VISION_ALIAS_ENV, raising=False)
    deck = build_fixture_deck(tmp_path / "deck.pptx")
    gateway = FakeGateway()
    summary = await vr.review_built_file(deck, "pptx", work_dir=tmp_path / "w", gateway=gateway)
    assert gateway.calls == [] and summary["statement"] == vr.DETERMINISTIC_ONLY


async def test_a_confirmed_alias_without_a_gateway_says_the_vision_review_did_not_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    alias = next(iter(AGENT_ALIAS_CHOICES))
    monkeypatch.setenv(vr.VISION_ALIAS_ENV, alias)
    deck = build_fixture_deck(tmp_path / "deck.pptx")
    summary = await vr.review_built_file(deck, "pptx", work_dir=tmp_path / "w", gateway=None)
    assert "the vision review was not run" in summary["statement"]
    assert "vision model not confirmed" not in summary["statement"]
