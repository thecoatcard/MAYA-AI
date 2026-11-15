"""Required-details gate (topic, depth, Notion destination, code language), the honest start text
and the completion-claim guard. Pure functions: no model, no database."""

from __future__ import annotations

import pytest

from app.services import required_details as rd
from app.services import start_guard


def slots(request: str, **kwargs: object) -> tuple[str, ...]:
    return rd.check(rd.GateInput(request=request, **kwargs)).slots  # type: ignore[arg-type]


# ----------------------------------------------------------------------------- golden prompts


def test_the_screenshot_prompt_asks_topic_depth_and_destination() -> None:
    assert slots("make a detailed notes and save to notion") == ("topic", "depth", "destination")


@pytest.mark.parametrize(
    "request_text",
    [
        "notes banao",
        "ek PDF banao",
        "ppt banao",
        "research karo",
        "detailed notes banao",
        "make a ppt",
        "excel sheet banao",
    ],
)
def test_a_deliverable_with_no_subject_asks_for_the_topic(request_text: str) -> None:
    assert "topic" in slots(request_text)


@pytest.mark.parametrize(
    "request_text",
    [
        "Java streams ke notes banao",
        "python decorators ppt banao",
        "solar energy par ek PDF banao",
        "research karo AI chips par",
        "hello, how are you?",
        "what is a pdf?",
    ],
)
def test_a_request_with_a_subject_does_not_ask_for_the_topic(request_text: str) -> None:
    assert "topic" not in slots(request_text)


def test_a_reference_to_the_previous_message_counts_as_the_topic() -> None:
    previous = ["Explain Kafka consumer groups"]
    assert slots("isi par 10 page ka PDF banao", history_user=previous) == ()
    assert slots("make a ppt on this", history_user=previous) == ()
    # ... but a reference with nothing to refer to still asks.
    assert slots("isi par 10 page ka PDF banao") == ("topic",)


def test_an_attached_file_is_the_topic() -> None:
    assert slots("ppt banao", has_attachment=True) == ()


def test_notion_save_after_a_document_asks_only_for_the_destination() -> None:
    assert slots("Notion me save karo", has_prior_content=True) == ("destination",)
    assert slots("save this to Notion", has_prior_content=True) == ("destination",)


@pytest.mark.parametrize(
    "request_text",
    [
        "Java streams notes banao 10 pages notion me save karo https://www.notion.so/Java-Notes-"
        + "a" * 32,
        "Java streams 10 page notes, save in Notion page called 'Study Hub'",
    ],
)
def test_a_notion_url_or_named_page_is_a_destination(request_text: str) -> None:
    assert "destination" not in slots(request_text)


def test_hinglish_variants_ask_the_same_things() -> None:
    assert slots("notes bana do aur notion me save kar do") == ("topic", "depth", "destination")
    assert slots("ek detailed ppt bana do") == ("topic",)


def test_depth_is_asked_for_notes_only_and_only_without_a_stated_length() -> None:
    assert "depth" in slots("Java streams ke notes banao")
    assert "depth" not in slots("Java streams ke 10 page ke notes banao")
    assert "depth" not in slots("Java streams quick notes banao")
    assert "depth" not in slots("Java streams PDF banao")


def test_saved_preference_never_supplies_the_topic_only_the_language() -> None:
    prefs = "The user prefers Java for coding."
    # 'notes banao' with a Java preference still asks for the topic.
    assert "topic" in slots("notes banao", preferences=prefs)
    # With a topic, the preference picks the code language: no language question.
    assert "language" not in slots("data structures notes banao", preferences=prefs)
    # Without a preference a programming topic asks once; naming the language removes the ask.
    assert "language" in slots("data structures notes banao")
    assert "language" not in slots("Java streams notes banao")
    assert "language" not in slots("data structures notes banao in Python")


def test_answer_turn_resolves_each_slot() -> None:
    asked = "make a detailed notes and save to notion"
    answered = f"{asked}\nTopic: Java streams; Depth: Standard; Destination: Study Hub"
    assert slots(answered) == ()
    skipped = f"{asked}\nTopic: (skipped); Depth: Standard; Destination: Study Hub"
    assert slots(skipped) == ("topic",)
    # An answer word such as 'Standard' is never mistaken for the topic.
    only_depth = "notes banao\nTopic: (skipped); Depth: Standard"
    assert "topic" in slots(only_depth)


# ---------------------------------------------------------------------------------- the card


def test_card_has_at_most_three_questions_and_no_invented_options() -> None:
    missing = rd.check(rd.GateInput(request="make a detailed notes and save to notion"))
    questions = rd.build_questions(missing, notion_pages=["Study Hub", "Inbox"])
    assert [q["header"] for q in questions] == ["Topic", "Depth", "Destination"]
    assert questions[0]["options"] == []  # no fabricated topic suggestions
    assert [o["label"] for o in questions[1]["options"]] == ["Quick", "Standard", "Full book"]
    assert [o["label"] for o in questions[2]["options"]] == ["Study Hub", "Inbox"]


def test_card_without_notion_connection_asks_to_connect() -> None:
    missing = rd.check(rd.GateInput(request="Java streams 10 pages notes, save to notion"))
    questions = rd.build_questions(missing, notion_connected=False)
    assert "Connect" in questions[0]["question"]
    assert questions[0]["options"] == []


def test_card_topic_suggestion_is_only_from_the_real_conversation() -> None:
    assert rd.suggestion_from_history([]) == ""
    assert rd.suggestion_from_history(["hi there"]) == ""
    assert "kafka" in rd.suggestion_from_history(["Explain Kafka consumer groups"]).lower()


def test_parse_notion_pages_reads_search_lines() -> None:
    text = (
        "Pages the integration can see (most recently edited first):\n"
        "- Study Hub | id abc | in a workspace | https://notion.so/x\n"
        "- Untitled | id def | in a page | https://notion.so/y\n"
    )
    assert rd.parse_notion_pages(text) == ["Study Hub"]


def test_hinglish_request_gets_hinglish_questions() -> None:
    missing = rd.check(rd.GateInput(request="notes banao"))
    assert missing.hinglish
    assert "chahiye" in rd.build_questions(missing)[0]["question"]


# ------------------------------------------------------------------- never invent a topic


def test_instruction_topic_must_come_from_the_conversation() -> None:
    invented = "Create detailed notes on Java Architecture, Memory Management and Collections"
    assert not rd.topic_supported(invented, ["make a detailed notes and save to notion"])
    assert rd.topic_supported(invented, ["Java memory management ke notes banao"])
    # Translations: a topic word in the user's message is enough.
    assert rd.topic_supported("Notes on summer heat waves", ["garmi ke notes banao"])
    # A saved-preference language as the code language is allowed, as the subject it is not.
    assert rd.topic_supported("Sorting notes with code in Java", ["sorting ke notes banao"])
    assert not rd.topic_supported("Java Collections notes", ["sorting ke notes banao"])
    assert rd.topic_supported("anything", [], grounded=True)


def _row(sender: str, content: str, **meta: object) -> tuple[str, str, dict[str, object]]:
    return (sender, content, dict(meta))


def test_backstop_refuses_a_topicless_request_even_with_a_model_written_instruction() -> None:
    rows = [_row("user", "make a detailed notes and save to notion")]
    refusal = rd.backstop_refusal(rows, "Notes on Java Collections", has_files=False)
    assert refusal is not None and refusal.status == "needs_clarification"
    assert "topic" in refusal.missing and "destination" in refusal.missing
    assert rd.instruction_invents_topic(rows, "Notes on Java Collections", has_files=False)


def test_backstop_accepts_the_answered_request() -> None:
    rows = [
        _row("user", "make a detailed notes and save to notion"),
        _row("orchestrator", "Which topic?"),
        _row(
            "user", "Topic: Java streams; Depth: Standard; Destination: Study Hub", answers_to="a"
        ),
    ]
    rows = list(reversed(rows))  # newest first, as the service reads them
    assert rd.backstop_refusal(rows, "Java streams notes", has_files=False) is None
    assert not rd.instruction_invents_topic(rows, "Java streams notes", has_files=False)


# ---------------------------------------------------------------------- honest start + guard

SCREENSHOT = (
    "Maine aapke liye detailed notes ka document taiyar kar diya gaya hai. "
    "Ab bataiye ise Notion ke kis page ya database mein save karun?"
)


def test_started_text_is_system_written_and_plain() -> None:
    text = start_guard.started_text("Java notes", 13)
    assert text == (
        "Started: Java notes. It has 13 steps; I will update you here (and by voice if voice is "
        "on) when it is done."
    )
    assert "1 step;" in start_guard.started_text("x", 1)
    assert start_guard.started_text("x", None).startswith("Started: x. I will update you")


def test_the_screenshot_completion_claim_is_removed() -> None:
    cleaned, changed = start_guard.strip_completion_claims(SCREENSHOT)
    assert changed
    assert "taiyar" not in cleaned
    assert "Notion" in cleaned  # the useful follow-up question stays
    full = start_guard.guard_text(SCREENSHOT, start_guard.started_text("Java notes", 13))
    assert full.startswith("Started: Java notes.")
    assert "taiyar" not in full and full.endswith("(still working)")


@pytest.mark.parametrize(
    "claim",
    [
        "I have saved the notes to Notion.",
        "I've created the document.",
        "The document is ready.",
        "Your notes are done!",
        "Notion page ban gaya hai.",
        "Maine notes bana diya.",
        "Save kar diya gaya hai.",
        "Successfully saved to Notion.",
    ],
)
def test_completion_claims_in_english_and_hinglish_are_caught(claim: str) -> None:
    assert start_guard.strip_completion_claims(claim) == ("", True)


@pytest.mark.parametrize(
    "fine",
    [
        "The notes will be ready when the task ends.",
        "I could not save it yet.",
        "Shall I save it to Notion once it is done?",
        "Progress is on the Tasks page.",
        "Kaam shuru ho gaya hai, progress Tasks page par hai.",
    ],
)
def test_plain_status_text_is_left_alone(fine: str) -> None:
    text, changed = start_guard.strip_completion_claims(fine)
    assert not changed
    assert text == fine


@pytest.mark.parametrize(
    "prompt",
    ["Ek PDF banao", "ppt banao", "notes banao", "make a detailed notes and save to notion"],
)
def test_memory_with_a_profile_and_a_java_preference_never_supplies_the_topic(prompt: str) -> None:
    memory = "Anand Kumar Shukla, software engineer. The user prefers Java for coding."
    assert "topic" in slots(prompt, preferences=memory)


def test_a_document_about_the_user_names_its_subject_itself() -> None:
    assert "topic" not in slots("mera resume PDF banao")


def test_a_task_less_turn_loses_sentences_that_point_at_an_approval_card() -> None:
    text = (
        "Yeh email coatcard.ai@gmail.com par bhejne ke liye taiyar kiya ja raha hai. "
        "Kripya is task card par Approve par click karein"
    )
    cleaned, changed = start_guard.strip_approval_claims(text)
    assert changed and "Approve" not in cleaned and "task card" not in cleaned
    assert start_guard.strip_approval_claims("Shall I ask for approval first?")[1] is False


# ------------------------------------------------------------------------ labels and voice


def test_task_steps_show_human_labels_not_agent_names() -> None:
    from app.agents.learning_book_plan import build_learning_book_plan
    from app.tasks.progress_steps import step_label

    _intent, nodes, _facts = build_learning_book_plan("Java streams notes banao notion me save")
    labels = [step_label(n.input_data, str(n.assigned_agent)) for n in nodes]
    assert labels[0] == "Plan the book"
    assert labels[1].startswith("Chapter 1 of ")
    assert "Assemble" in labels and labels[-1] == "Create the Notion page"
    assert not any("agent" in label.lower() for label in labels)
    assert step_label(nodes[1].input_data, "file", "Generics") == (f"{labels[1]}: Generics")
    assert step_label({}, "research") == "Research agent"  # other plans keep the agent label


async def test_voice_submit_task_asks_aloud_and_shows_the_card_when_topic_is_missing() -> None:
    from uuid import uuid4

    from app.voice.tools import VoiceExecutionContext, VoiceToolRegistry

    registry = VoiceToolRegistry()
    shown: list[dict[str, object]] = []
    started: list[str] = []

    async def ask(args: dict[str, object]) -> dict[str, object]:
        shown.append(args)
        return {"status": "shown"}

    async def submit(intent: str, params: object, context: object) -> dict[str, object]:
        started.append(intent)
        return {"task_id": "t1", "status": "planning"}

    spoken = ["ek PDF banao"]

    async def required(instruction: str) -> list[dict[str, object]] | None:
        missing = rd.check(rd.GateInput(request=spoken[-1]))
        return rd.build_questions(missing) if missing else None

    registry.set_ask_handler(ask)
    registry.set_task_submitter(submit)
    registry.set_required_check(required)
    context = VoiceExecutionContext(workspace_id=uuid4(), user_id=uuid4(), session_id=None)
    result = await registry.execute_tool_async(
        "submit_task", {"instruction": "Write a PDF about Java collections framework"}, context
    )
    assert result["status"] == "needs_clarification" and started == []
    assert shown and "Say the first question aloud" in str(result["message"])

    spoken.append("Java streams par ek PDF banao")
    ok = await registry.execute_tool_async(
        "submit_task", {"instruction": "A PDF about Java streams"}, context
    )
    assert started == ["A PDF about Java streams"] and ok["status"] == "planning"


# ------------------------------------------------------------- advisory decisions (fail-open)


async def test_decision_helpers_have_no_opinion_without_a_service() -> None:
    from app.decisions import runtime as decisions
    from app.decisions.required_details_check import has_topic, rank_notion_pages

    decisions.set_current(None)
    assert await has_topic("notes banao") is None
    assert await rank_notion_pages("java", ["B", "A"]) == ["B", "A"]  # recency order kept


async def test_decision_ranking_only_reorders_and_never_invents() -> None:
    from app.decisions import runtime as decisions
    from app.decisions.questions import Answers, ChoiceAnswer
    from app.decisions.required_details_check import rank_notion_pages

    class Fake:
        def available(self, purpose: str) -> bool:
            return True

        async def decide(self, purpose: str, input_value: object, questions: object, **kw: object):  # type: ignore[no-untyped-def]
            return Answers({"best_parent": ChoiceAnswer("p1", {"p1": 0.95}, 1.0)})

        def status(self) -> dict[str, object]:
            return {}

    decisions.set_current(Fake())
    try:
        assert await rank_notion_pages("java", ["Inbox", "Java Hub", "Misc"]) == [
            "Java Hub",
            "Inbox",
            "Misc",
        ]
    finally:
        decisions.set_current(None)
