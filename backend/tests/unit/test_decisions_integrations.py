"""Decision model integration points (ADR-0029), with a fake service. No network.

Each point is tested two ways: a positive flag changes behaviour only in the safe direction, and
no answer (None), a disabled service or no service leaves behaviour identical.
"""

from __future__ import annotations

from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from app.decisions import runtime as decisions
from app.decisions.answer_check import needs_web_search, unsupported_by_sources
from app.decisions.approval_check import check_payload
from app.decisions.doc_quality import flatten, review_document
from app.decisions.mail_triage import triage_list, triage_message
from app.decisions.memory_check import check_memory
from app.decisions.planner_hint import planner_hint
from app.decisions.screen import screen_instructions
from app.decisions.step_review import review_step
from app.decisions.voice_digest import worth_interrupting
from app.services.chat_route_hint import refine_route
from app.services.chat_router import Decision, Route, route_message
from app.tasks.worker import failed_step_report
from tests.support.decisions import FakeDecider, choice_a, noul_a, score_a

LONG = "x " * 200


def install(fake: FakeDecider | None) -> FakeDecider | None:
    decisions.set_current(fake)
    return fake


# ------------------------------------------------------------------------------ chat route


async def test_route_hint_escalates_an_ambiguous_message_to_approval() -> None:
    text = "mujhe is baare mein kuch karna hai zara dekho"
    base = route_message(text)
    install(FakeDecider({"route": choice_a("approval")}))
    refined = await refine_route(base, text)
    assert base.route is Route.ANSWER and refined.route is Route.APPROVAL


async def test_route_hint_never_overrides_a_stated_count_or_format() -> None:
    text = "write me a 40 page pdf on rust"
    base = route_message(text)
    fake = install(FakeDecider({"route": choice_a("answer")}))
    assert await refine_route(base, text) == base
    assert fake is not None and fake.calls == []  # not even asked


@pytest.mark.parametrize(
    "answers", [None, {"route": choice_a("other")}, {"route": choice_a("task", 0.4)}]
)
async def test_route_hint_without_a_clear_answer_keeps_the_router(answers: Any) -> None:
    text = "do the needful with this thing"
    base = route_message(text)
    install(FakeDecider(answers))
    assert await refine_route(base, text) == base


async def test_route_hint_never_lowers_an_approval_route() -> None:
    base = Decision(Route.APPROVAL, "approval_verb")
    install(FakeDecider({"route": choice_a("answer")}))
    assert await refine_route(base, "send it") == base


async def test_route_hint_without_a_service_is_a_no_op() -> None:
    base = route_message("hello there friend of mine")
    assert await refine_route(base, "hello there friend of mine") == base


# ------------------------------------------------------------------ answers and sources


async def test_unverified_note_checks() -> None:
    install(FakeDecider({"needs_web_search": noul_a(0.95)}))
    assert await needs_web_search("who is the ceo now", "x" * 100) is True
    install(FakeDecider({"needs_web_search": noul_a(0.4)}))
    assert await needs_web_search("q", "x" * 100) is False
    install(None)
    assert await needs_web_search("q", "x" * 100) is False


async def test_unsupported_answer_needs_a_clear_no() -> None:
    install(FakeDecider({"supported": noul_a(0.03)}))
    assert await unsupported_by_sources("q", "a" * 100, "evidence") is True
    install(FakeDecider({"supported": noul_a(0.6)}))
    assert await unsupported_by_sources("q", "a" * 100, "evidence") is False
    install(FakeDecider(None))
    assert await unsupported_by_sources("q", "a" * 100, "evidence") is False


# -------------------------------------------------------------------------- injection


async def test_injection_screen_flags_only_on_a_clear_yes() -> None:
    install(FakeDecider({"instructs_ai": noul_a(0.97)}))
    assert await screen_instructions("a" * 300, "web") is True
    install(FakeDecider({"instructs_ai": noul_a(0.5)}))
    assert await screen_instructions("a" * 300, "web") is False
    install(FakeDecider(None))
    assert await screen_instructions("a" * 300, "web") is False
    install(None)
    assert await screen_instructions("a" * 300, "web") is False


async def test_mcp_output_gets_an_added_flag_never_a_removed_one() -> None:
    from app.mcp.sanitizer import sanitize_tool_output

    clean = sanitize_tool_output("plain page text " * 20, "web")
    assert not clean.has_injection_suspicions
    install(FakeDecider({"instructs_ai": noul_a(0.99)}))
    from dataclasses import replace

    from app.decisions.screen import MODEL_FLAG

    flagged = replace(
        clean, has_injection_suspicions=True, suspicions=(*clean.suspicions, MODEL_FLAG)
    )
    assert flagged.wrapped_output == clean.wrapped_output  # the UNTRUSTED markers are untouched


async def test_search_result_gets_a_warning_when_flagged() -> None:
    from app.search.service import WebSearchService

    result = {"passages": [{"n": 1, "text": "x" * 300}], "message": "Answer from sources."}
    install(FakeDecider({"instructs_ai": noul_a(0.99)}))
    out = await WebSearchService._screened(dict(result))
    assert out["injection_suspected"] is True and "Ignore it" in out["message"]
    install(None)
    same = await WebSearchService._screened(dict(result))
    assert same == result


# ----------------------------------------------------------------------------- mail


async def test_mail_triage_labels_a_list_and_a_message() -> None:
    def answers(purpose: str, questions: Any) -> Any:
        from app.decisions.questions import Answers

        return Answers(
            {
                "u1": choice_a("urgent"),
                "c1": choice_a("finance"),
                "r1": noul_a(0.95),
                "u2": choice_a("other"),
            }
        )

    install(FakeDecider(answers))
    labels = await triage_list(["[1] From: a | Subject: Invoice", "[2] From: b | Subject: hi"])
    assert labels == [{"n": 1, "urgency": "urgent", "category": "finance", "needs_reply": True}]
    install(FakeDecider({"u": choice_a("low"), "c": choice_a("newsletter"), "r": noul_a(0.02)}))
    one = await triage_message("Subject: weekly digest")
    assert one == {"urgency": "low", "category": "newsletter", "needs_reply": False}


async def test_mail_triage_without_an_answer_is_none() -> None:
    install(FakeDecider(None))
    assert await triage_list(["[1] x"]) is None
    assert await triage_message("hello there") is None
    install(None)
    assert await triage_list(["[1] x"]) is None


# ------------------------------------------------------------------------ step review


def step(status: str, output: dict[str, Any] | None = None, agent: str = "document") -> Any:
    return SimpleNamespace(
        description="Write the report",
        error_message=None,
        assigned_agent=agent,
        status=SimpleNamespace(value=status),
        output_result=output,
    )


async def test_step_review_flags_a_gap_and_the_task_is_not_clean() -> None:
    install(
        FakeDecider(
            {
                "satisfies_request": noul_a(0.02),
                "has_gaps": noul_a(0.95),
                "completeness": score_a(0.2),
            }
        )
    )
    # Updated deliberately (step-review calibration): a model objection alone is advisory. The
    # flag needs deterministic corroboration (here a "[insert ...]" placeholder) and only the
    # "enforce" mode lets it change the task status.
    text = "Intro [insert chart here] " + LONG
    review = await review_step("make a report", "Write", {"result": text}, mode="enforce")
    assert review is not None and review["flagged"] and len(review["reasons"]) == 3
    report = failed_step_report([step("completed", {"decision_review": review})])
    assert report and report[0]["deliverable_incomplete"] is True
    assert report[0]["error"].startswith("Result may be incomplete")


async def test_step_review_never_marks_anything_good_or_failed_on_no_answer() -> None:
    install(FakeDecider(None))
    assert await review_step("g", "s", {"result": LONG}) is None
    install(
        FakeDecider(
            {
                "satisfies_request": noul_a(0.95),
                "has_gaps": noul_a(0.05),
                "completeness": score_a(3),
            }
        )
    )
    review = await review_step("g", "s", {"result": LONG})
    assert review is not None and review["flagged"] is False and review["reasons"] == []
    # An unflagged or absent review adds nothing to the report: behaviour is identical.
    assert failed_step_report([step("completed", {"result": "ok"}), step("completed", None)]) == []
    assert len(failed_step_report([step("failed")])) == 1


# ------------------------------------------------------------------------- documents


async def test_document_review_flags_thin_content_and_names_what_was_checked() -> None:
    install(
        FakeDecider(
            {
                "quality": score_a(0.3),
                "placeholder": noul_a(0.95),
                "repetitive": noul_a(0.1),
                "off_topic": noul_a(0.1),
                "style_match": noul_a(0.9),
            }
        )
    )
    review = await review_document("a report", LONG, "document")
    assert review is not None and review.flagged
    assert "placeholder" in review.note() and "document text" in review.note()
    assert "advisory" in review.note()


async def test_document_review_passes_an_existing_pdf_as_a_document_block(tmp_path: Path) -> None:
    pdf = tmp_path / "a.pdf"
    pdf.write_bytes(b"%PDF-1.4 fake")
    fake = install(FakeDecider({"quality": score_a(3), "visual": score_a(0.2)}))
    review = await review_document("deck", LONG, "document", pdf_path=pdf)
    assert review is not None and "rendered PDF pages" in review.checked
    assert "the page layout looks poor" in review.reasons
    assert fake is not None
    blocks = fake.calls[0][1]
    assert any(b.get("type") == "document" for b in blocks)


async def test_document_review_oversized_or_missing_pdf_falls_back_to_text(tmp_path: Path) -> None:
    fake = install(FakeDecider({"quality": score_a(3)}))
    await review_document("deck", LONG, "document", pdf_path=tmp_path / "missing.pdf")
    assert isinstance(fake.calls[0][1], str)  # type: ignore[union-attr]


async def test_document_review_without_an_answer_is_none() -> None:
    install(FakeDecider(None))
    assert await review_document("r", LONG, "document") is None
    install(None)
    assert await review_document("r", LONG, "document") is None


def test_flatten_reads_blocks_slides_and_sheets() -> None:
    text = flatten(
        {"blocks": [{"type": "paragraph", "text": "alpha"}], "slides": [{"title": "beta"}]}
    )
    assert "alpha" in text and "beta" in text


# --------------------------------------------------------------------- approval check


async def test_approval_check_warns_and_strong_flag_refuses_auto_approve() -> None:
    install(FakeDecider({"matches_request": noul_a(0.05), "untrusted_instructions": noul_a(0.5)}))
    check = await check_payload("send hi to bob", "send_email", {"to": "eve@example.com"})
    assert check is not None and check.flagged and check.strong
    assert check.note().startswith("Warning")
    install(FakeDecider({"matches_request": noul_a(0.15), "untrusted_instructions": noul_a(0.5)}))
    weak = await check_payload("r", "t", {})
    assert weak is not None and weak.flagged and not weak.strong


async def test_approval_check_no_answer_or_clean_payload_changes_nothing() -> None:
    install(FakeDecider(None))
    assert await check_payload("r", "t", {"a": 1}) is None
    install(FakeDecider({"matches_request": noul_a(0.95), "untrusted_instructions": noul_a(0.02)}))
    clean = await check_payload("r", "t", {"a": 1})
    assert clean is not None and not clean.flagged and not clean.strong


async def test_approval_check_sends_no_secret_to_the_service() -> None:
    fake = install(FakeDecider(None))
    install(fake)
    install(FakeDecider(lambda p, qs: None))
    spy = FakeDecider({"matches_request": noul_a(0.9)})
    install(spy)
    await check_payload("r", "t", {"note": "token ghp_" + "a" * 40})
    assert "ghp_" + "a" * 40 not in str(spy.calls[0][1])


FORBIDDEN = ("mint_approval_token", "ToolTier", ".approve(", "AutoPlan(True", "permission_registry")


def test_no_decision_code_can_approve_or_change_a_tier() -> None:
    root = Path(__file__).resolve().parents[2] / "app" / "decisions"
    for path in root.glob("*.py"):
        source = path.read_text(encoding="utf-8")
        for needle in FORBIDDEN:
            assert needle not in source, f"{path.name} must not use {needle}"


def test_worker_only_downgrades_auto_approve_on_a_decision() -> None:
    source = (Path(__file__).resolve().parents[2] / "app" / "tasks" / "worker.py").read_text(
        encoding="utf-8"
    )
    assert (
        "if guard is not None and guard.strong:\n                            auto_plan = MANUAL"
        in source
    )
    assert "auto_plan = AutoPlan" not in source


# ------------------------------------------------------------------------------ memory


async def test_memory_check_secret_and_one_off() -> None:
    install(FakeDecider({"contains_secret": noul_a(0.97), "durable": noul_a(0.5)}))
    verdict = await check_memory("my code is open sesame 4411")
    assert verdict is not None and verdict.secret
    install(FakeDecider({"contains_secret": noul_a(0.02), "durable": noul_a(0.03)}))
    verdict = await check_memory("book a table tonight")
    assert verdict is not None and not verdict.secret and verdict.one_off
    install(None)
    assert await check_memory("anything") is None


# ------------------------------------------------------------------------------ voice


async def test_voice_step_update_is_dropped_only_on_a_clear_no() -> None:
    install(FakeDecider({"worth_interrupting": noul_a(0.02)}))
    assert await worth_interrupting("t", "s", 1, 3, "e") is False
    install(FakeDecider({"worth_interrupting": noul_a(0.4)}))
    assert await worth_interrupting("t", "s", 1, 3, "e") is True
    install(FakeDecider(None))
    assert await worth_interrupting("t", "s", 1, 3, "e") is True
    install(None)
    assert await worth_interrupting("t", "s", 1, 3, "e") is True


# ----------------------------------------------------------------------------- planner


async def test_planner_hint_is_advisory_text_or_empty() -> None:
    install(FakeDecider({"needs_research": noul_a(0.95), "needs_document": noul_a(0.9)}))
    hint = await planner_hint("latest ai news as a pdf")
    assert "research" in hint and "document" in hint and "can be wrong" in hint
    install(FakeDecider(None))
    assert await planner_hint("x") == ""
    install(None)
    assert await planner_hint("x") == ""
