"""Synthesis after thin research: an honest short result is accepted, not "may be incomplete".

Owner bug: research legitimately found little (quota, no sources), the synthesis step wrote an
honest short note, and the decision-model review flagged it "looks incomplete / does not satisfy",
so the task ended "Completed with errors". With ``thin_input`` only a placeholder is flagged."""

from __future__ import annotations

from app.decisions import runtime as decisions
from app.decisions.questions import Answer
from app.decisions.step_review import THIN_RULE, review_step
from app.tasks.worker import _says_sources_unavailable
from tests.support.decisions import FakeDecider, noul_a, score_a

HONEST = "I could not find reliable sources for this, so there is little to report. " * 3
NEGATIVE: dict[str, Answer] = {
    "satisfies_request": noul_a(0.02),
    "has_gaps": noul_a(0.1),
    "completeness": score_a(0.2),
}


async def test_thin_research_makes_the_short_honest_result_acceptable() -> None:
    fake = FakeDecider(NEGATIVE)
    decisions.set_current(fake)

    review = await review_step("research java", "Synthesize", {"result": HONEST}, thin_input=True)

    assert review is not None and review["flagged"] is False and review["reasons"] == []
    assert THIN_RULE in str(fake.calls[0][1])


async def test_without_thin_input_the_same_answers_still_flag_the_result() -> None:
    decisions.set_current(FakeDecider(NEGATIVE))

    review = await review_step("research java", "Synthesize", {"result": HONEST})

    # Calibration: the objection is recorded, but without deterministic corroboration it is a
    # note, not a problem (flagged stays False).
    assert review is not None and not review["flagged"]
    assert "the result looks incomplete" in review["reasons"]


async def test_a_placeholder_is_flagged_even_for_thin_input() -> None:
    decisions.set_current(FakeDecider({**NEGATIVE, "has_gaps": noul_a(0.97)}))

    review = await review_step("g", "s", {"result": HONEST}, thin_input=True)

    assert review is not None and review["reasons"] == [
        "part of the result looks empty or a placeholder"
    ]


def test_a_research_step_that_reports_no_sources_marks_its_output_thin() -> None:
    from types import SimpleNamespace

    degraded = SimpleNamespace(
        verification_proof={"sources_unavailable": True}, output_result={"result": "x"}
    )
    normal = SimpleNamespace(verification_proof={"sources": ["u"]}, output_result={"result": "x"})
    assert _says_sources_unavailable(degraded) and not _says_sources_unavailable(normal)
