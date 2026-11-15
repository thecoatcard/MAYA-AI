"""Owner rule: no file unless the user's own words name one. Golden prompts (typed and voice
transcripts, English and Hinglish) for ``requested_deliverable``, the planner, the voice
``submit_task``, the deep-research phrase and the failure wording. Fakes only; no network."""

from __future__ import annotations

from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.agents.planner import DAGPlanner, PlanDecompositionResponse, SubTaskPlanItem
from app.services.deliverable_intent import (
    OFFER_LINE,
    offers_format_line,
    requested_deliverable,
    wants_deep_research,
    wants_file,
)
from app.tasks.dispatch import (
    RESEARCH_FAILED_CHAT_MESSAGE,
    RESEARCH_FAILED_MESSAGE,
    research_failed_message,
)
from app.voice.tools import VoiceExecutionContext, VoiceToolRegistry

DEEP_VOICE = (
    "Deep research about the future of AI agents and how autonomous AI systems will transform "
    "software engineering by 2030."
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (DEEP_VOICE, "chat"),
        ("notes banao on Java streams", "chat"),
        ("detailed notes on photosynthesis", "chat"),
        ("write a report on solar energy", "chat"),
        ("write a 500 word essay", "chat"),
        ("write an essay on slide design", "chat"),
        ("what is a cheat sheet", "chat"),
        ("Java streams notes PDF me banao", "pdf"),
        ("ppt banao on Y", "pptx"),
        ("research X and make a 10-slide deck", "pptx"),
        ("make me a presentation on climate", "pptx"),
        ("Notion me save karo", "notion"),
        ("save it to Notion", "notion"),
        ("isko pdf bana do", "pdf"),
        ("excel sheet banao for my budget", "xlsx"),
        ("document banao for the leave policy", "file"),
        ("make a website for my cafe", "html"),
        ("email it to me", "mail"),
        ("mail karo isko", "mail"),
        ("save this", "file"),
        ("export as a word document", "docx"),
        ("create a todo aplication", "zip"),
        ("create a todo application", "zip"),
        ("build a react app", "zip"),
        ("what is a web application", "chat"),
    ],
)
def test_requested_deliverable_golden_prompts(text: str, expected: str) -> None:
    assert requested_deliverable(text) == expected


def test_a_follow_up_takes_the_format_named_earlier() -> None:
    assert requested_deliverable("haan kar do", ["Java notes pdf banao"]) == "pdf"
    assert requested_deliverable("haan kar do", ["Java notes banao"]) == "chat"
    # A message with a topic of its own is never read as a follow-up.
    assert requested_deliverable("notes on Rust ownership", ["make a pdf"]) == "chat"


def test_mail_is_not_a_file() -> None:
    assert wants_file("email it to me") is False
    assert wants_file("make a pdf about cats") is True


@pytest.mark.parametrize(
    "text",
    [
        DEEP_VOICE,
        "do deep research on EV batteries",
        "deeply research quantum computing",
        "EV batteries par deep research karo",
        "gehri research karo AI par",
    ],
)
def test_the_deep_research_phrase_is_recognised(text: str) -> None:
    assert wants_deep_research(text)


def test_ordinary_research_is_not_deep_research() -> None:
    assert not wants_deep_research("research solar panels")
    assert not wants_deep_research("how deep is the Pacific")


def test_the_optional_format_line_is_for_notes_without_a_format_only() -> None:
    assert offers_format_line("notes banao on Java streams")
    assert not offers_format_line("notes on Java streams as a PDF")
    assert not offers_format_line("what is a stream")
    assert "PDF" in OFFER_LINE and "Notion" in OFFER_LINE


# ------------------------------------------------------------------------------------- planner
def _item(item_id: str, role: str, description: str, deps: list[str] | None = None) -> Any:
    return SubTaskPlanItem(
        id=item_id, assigned_agent=role, description=description, dependencies=deps or []
    )


def _builder_plan() -> PlanDecompositionResponse:
    return PlanDecompositionResponse(
        intent="x",
        subtasks=[
            _item("r", "research", "Research the future of AI agents with sources"),
            _item("d", "document", "Create the report", ["r"]),
        ],
    )


@pytest.mark.asyncio
async def test_the_planner_never_emits_a_builder_step_without_a_format_word() -> None:
    gateway = AsyncMock()
    gateway.generate_structured.return_value = _builder_plan()
    _, plan = await DAGPlanner(gateway=gateway).plan(DEEP_VOICE)
    assert plan, "a plan keeps at least the research step"
    assert all(str(step.assigned_agent) != "document" for step in plan)


@pytest.mark.asyncio
async def test_the_planner_keeps_the_builder_when_a_format_is_named() -> None:
    gateway = AsyncMock()
    gateway.generate_structured.return_value = _builder_plan()
    _, plan = await DAGPlanner(gateway=gateway).plan(
        "research the latest AI agents with sources and make a 10-slide deck"
    )
    assert any(str(step.assigned_agent) == "document" for step in plan)


@pytest.mark.asyncio
async def test_notes_without_a_format_skip_the_learning_book_pipeline() -> None:
    gateway = AsyncMock()
    gateway.generate_structured.return_value = _builder_plan()
    _, plan = await DAGPlanner(gateway=gateway).plan("detailed notes banao on Java streams")
    assert gateway.generate_structured.await_count == 1  # planned by the model, not in code
    assert all(str(step.assigned_agent) != "document" for step in plan)


# ----------------------------------------------------------------------------------- voice tools
def _context() -> VoiceExecutionContext:
    return VoiceExecutionContext(workspace_id=uuid4(), user_id=uuid4())


async def _voice_submit(args: dict[str, Any]) -> dict[str, Any]:
    registry = VoiceToolRegistry()
    seen: dict[str, Any] = {}

    async def submitter(
        instruction: str, params: dict[str, Any], context: VoiceExecutionContext
    ) -> dict[str, Any]:
        seen.update(instruction=instruction, params=params)
        return {"task_id": "t1", "status": "planning"}

    registry.set_task_submitter(submitter)
    await registry.execute_tool_async("submit_task", args, _context())
    return seen


@pytest.mark.asyncio
async def test_voice_submit_task_rejects_an_unrequested_pdf() -> None:
    seen = await _voice_submit({"instruction": DEEP_VOICE, "deliverable": "pdf"})
    assert seen["params"]["deliverable"] == "none"


@pytest.mark.asyncio
async def test_voice_submit_task_keeps_a_requested_pdf() -> None:
    seen = await _voice_submit(
        {"instruction": "Java streams notes as a PDF, ten pages", "deliverable": "pdf"}
    )
    assert seen["params"]["deliverable"] == "pdf"


@pytest.mark.asyncio
async def test_voice_deep_research_phrase_starts_the_deep_research_path() -> None:
    seen = await _voice_submit({"instruction": DEEP_VOICE, "deliverable": "research"})
    assert seen["params"]["deep_research"] is True
    assert seen["params"]["deliverable"] == "none"


@pytest.mark.asyncio
async def test_voice_deep_research_without_a_topic_asks_for_one() -> None:
    registry = VoiceToolRegistry()
    result = await registry.execute_tool_async(
        "submit_task",
        {"instruction": "deep research karo", "deliverable": "deep_research"},
        _context(),
    )
    assert result["status"] == "needs_clarification"


# ------------------------------------------------------------------------------ failure wording
def test_failure_wording_depends_on_whether_a_file_was_requested() -> None:
    chat = research_failed_message(DEEP_VOICE, {})
    assert chat == RESEARCH_FAILED_CHAT_MESSAGE
    assert "did not build the file" not in chat
    assert "answer from general knowledge" in chat
    file_text = research_failed_message("research AI agents and make a pdf", {})
    assert file_text == RESEARCH_FAILED_MESSAGE
    assert "build from general knowledge" in file_text
    # The user's own words travel in the requirements, not the planner's rewritten step.
    ctx = {"requirements": {"user_request": "make a pdf on AI agents"}}
    assert research_failed_message("Compile findings", ctx) == RESEARCH_FAILED_MESSAGE
