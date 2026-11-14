"""Step review calibration: false positives collapse, real failures still count.

Owner report: ~90% of tasks showed "Completed with errors" (for example a completed
'@python-reviewer review main.py' step reported as "Failed: review main.py: Result may be
incomplete: the result does not appear to satisfy the request; the result looks incomplete").
Cause: one weak model opinion was enough to mark the task partial. Now a model objection is an
advisory note unless a deterministic check corroborates it AND the mode is "enforce"."""

from __future__ import annotations

from collections.abc import Mapping
from types import SimpleNamespace
from typing import Any, cast

import pytest

from app.core.config import Settings
from app.decisions import runtime as decisions
from app.decisions.questions import Answer
from app.decisions.step_review import deterministic_evidence, result_text, review_step
from app.services.task_result import PARTIAL, _failure_lines
from app.tasks.worker import failed_step_report, step_review_notes
from tests.support.decisions import FakeDecider, noul_a, score_a

# The model is as negative as it can be: the worst false-positive condition seen in production.
NEGATIVE: dict[str, Answer] = {
    "satisfies_request": noul_a(0.02),
    "has_gaps": noul_a(0.95),
    "completeness": score_a(0.2),
}
REVIEW_OUTPUT = (
    "main.py review: 1) the loop on line 12 shadows `i`; 2) open() is not closed, use `with`; "
    "3) the function parse() has no type hints. Overall the script is small and readable."
)
RESEARCH_SUMMARY = "Only two sources were found. Source A says X; source B says Y. " * 3
DOCUMENT = "Chapter 1 explains the market in detail with figures and a clear recommendation. " * 4
NOTION = "Saved the page 'Weekly notes' to Notion and linked it from the project index page."
FILE_STEP = {"path": "reports/summary.docx", "file_name": "summary.docx", "bytes": 18234}

FALSE_POSITIVE_OUTPUTS: dict[str, Mapping[str, Any]] = {
    "custom_agent_review": {"result": REVIEW_OUTPUT},
    "research_summary_few_sources": {"result": RESEARCH_SUMMARY},
    "document_with_correct_count": {"result": DOCUMENT},
    "notion_save": {"result": NOTION},
    "file_step": FILE_STEP,
}


def _step(output: dict[str, Any] | None, status: str = "completed") -> Any:
    return SimpleNamespace(
        description="review main.py",
        error_message=None,
        assigned_agent="custom",
        status=SimpleNamespace(value=status),
        output_result=output,
    )


@pytest.mark.parametrize("name", sorted(FALSE_POSITIVE_OUTPUTS))
@pytest.mark.parametrize("mode", ["advisory", "enforce"])
async def test_a_model_objection_alone_never_marks_the_task_partial(name: str, mode: str) -> None:
    decisions.set_current(FakeDecider({**NEGATIVE, "has_gaps": noul_a(0.1)}))

    review = await review_step(
        "@python-reviewer review main.py",
        "review main.py",
        FALSE_POSITIVE_OUTPUTS[name],
        mode=mode,
    )

    assert review is not None
    assert review["reasons"], "the objection is still recorded"
    assert review["flagged"] is False and review["evidence"] == []
    steps = [_step({**FALSE_POSITIVE_OUTPUTS[name], "decision_review": review})]
    assert failed_step_report(steps) == []
    assert step_review_notes(steps)[0].startswith("Note: ")


@pytest.mark.parametrize(
    ("text", "proof", "evidence"),
    [
        ("Intro lorem ipsum dolor sit amet and more text here", None, "placeholder text"),
        ("Dear [insert client name], we are pleased to send", None, "placeholder text"),
        ("Traceback (most recent call last): File x, line 1", None, "error marker"),
        ("Error: could not read the requested file at all.", None, "error marker"),
        (REVIEW_OUTPUT, {"error": "file_missing"}, "failed step check"),
        (REVIEW_OUTPUT, {"verified": False}, "failed step check"),
    ],
)
async def test_true_positives_are_flagged_and_counted_in_enforce_mode(
    text: str, proof: dict[str, Any] | None, evidence: str
) -> None:
    decisions.set_current(FakeDecider(NEGATIVE))

    review = await review_step("do it", "step", {"result": text}, proof=proof, mode="enforce")

    assert review is not None and review["flagged"] is True
    assert evidence in review["evidence"]
    report = failed_step_report([_step({"decision_review": review})])
    assert len(report) == 1 and report[0]["step_completed"] is True
    assert step_review_notes([_step({"decision_review": review})]) == []


async def test_advisory_mode_records_but_never_changes_the_status() -> None:
    decisions.set_current(FakeDecider(NEGATIVE))

    review = await review_step(
        "g", "s", {"result": "TODO: write this part, lorem ipsum filler"}, mode="advisory"
    )

    assert review is not None and review["flagged"] is True and review["mode"] == "advisory"
    steps = [_step({"decision_review": review})]
    assert failed_step_report(steps) == []
    assert step_review_notes(steps)


async def test_off_mode_never_asks_the_model() -> None:
    fake = FakeDecider(NEGATIVE)
    decisions.set_current(fake)

    assert await review_step("g", "s", {"result": REVIEW_OUTPUT}, mode="off") is None
    assert fake.calls == []


async def test_clear_and_no_answer_are_unchanged() -> None:
    decisions.set_current(FakeDecider(None))
    assert await review_step("g", "s", {"result": REVIEW_OUTPUT}, mode="enforce") is None


def test_a_completed_step_is_never_worded_as_failed_in_the_result() -> None:
    review = {"flagged": True, "reasons": ["the result looks incomplete"], "mode": "enforce"}
    view = SimpleNamespace(
        failed_steps=failed_step_report([_step({"decision_review": review})]), steps=[]
    )
    lines = _failure_lines(view)  # type: ignore[arg-type]
    assert lines and not any(line.lower().startswith("- failed") for line in lines)
    assert PARTIAL == "partial"


def test_a_real_failed_step_still_marks_the_task_partial() -> None:
    failed = _step(None, status="failed")
    failed.error_message = "tool crashed"
    assert len(failed_step_report([failed])) == 1


def test_deterministic_evidence_ignores_clean_results() -> None:
    assert deterministic_evidence(REVIEW_OUTPUT, {"file_name": "a.docx"}) == []
    assert deterministic_evidence("Plan: TODOS app review, wheel of fortune", None) == []


def test_a_long_result_keeps_its_head_and_its_conclusion() -> None:
    text = "START " + "x " * 9000 + " FINAL CONCLUSION"
    excerpt = result_text({"result": text})
    assert excerpt.startswith("START") and excerpt.endswith("FINAL CONCLUSION")
    assert len(excerpt) < 9000


def test_the_mode_setting_defaults_to_advisory_and_rejects_unknown_values() -> None:
    from pydantic import TypeAdapter

    field = Settings.model_fields["step_review_mode"]
    assert field.default == "advisory"
    adapter = TypeAdapter(cast(Any, field.annotation))
    assert [adapter.validate_python(m) for m in ("off", "advisory", "enforce")] == [
        "off",
        "advisory",
        "enforce",
    ]
    with pytest.raises(ValueError):
        adapter.validate_python("loud")


def test_set_mode_installs_the_configured_mode_and_unknown_means_advisory() -> None:
    from app.decisions import step_review

    try:
        step_review.set_mode("enforce")
        assert step_review.current_mode() == "enforce"
        step_review.set_mode("nonsense")
        assert step_review.current_mode() == "advisory"
    finally:
        step_review.set_mode("advisory")
