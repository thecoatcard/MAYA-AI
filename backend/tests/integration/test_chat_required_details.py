"""Required-details gate through the in-memory chat harness (no model, no network).

A notes / document / deck / Notion-save request whose own words lack the topic, depth or Notion
page gets ONE ask_user card and NO task, even when the (fake) model reaches for submit_task with a
topic it invented. After the answer the task starts, the first words are system-written, and a
completion claim in the model's text is removed.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from tests.integration.test_chat_inline_files import IdempotentSubmitter, call
from tests.integration.test_chat_latency import wire
from tests.integration.test_chat_memory import Prefs
from tests.integration.test_chat_streaming import (
    World,
    events,
    make_world,
    names,
    reply_text,
    send,
    sign_in,
    stream_calls,
)

SCREENSHOT_PROMPT = "make a detailed notes and save to notion"
INVENTED = "Create detailed Java notes: Architecture, Memory Management, OOPs, Collections"
CLAIM = "Maine aapke liye detailed notes ka document taiyar kar diya gaya hai."


@pytest.fixture
async def world() -> AsyncIterator[World]:
    async with make_world() as value:
        value.submitter = IdempotentSubmitter()  # type: ignore[attr-defined]
        wire(value, prefs=Prefs(memory_on=False), submitter=value.submitter)  # type: ignore[attr-defined]
        yield value


def cards(frames: list[Any]) -> list[dict[str, Any]]:
    return [f.data for f in frames if f.event == "input.requested"]


def submitted(world: World) -> list[dict[str, Any]]:
    return world.submitter.submitted  # type: ignore[attr-defined, no-any-return]


async def test_a_topicless_notes_request_asks_one_card_and_creates_no_task(world: World) -> None:
    account = await sign_in(world)
    # The model ignores the rule and submits a task with a topic it invented.
    world.models.queued_stream_scripts = [
        call("submit_task", {"instruction": INVENTED, "title": "Java notes"}),
        [{"text": CLAIM}],
    ]
    frames = events(await send(world, account, SCREENSHOT_PROMPT))

    (card,) = cards(frames)
    assert [q["header"] for q in card["questions"]] == ["Topic", "Depth", "Destination"]
    assert card["questions"][0]["options"] == []  # no invented topic suggestions
    assert [o["label"] for o in card["questions"][1]["options"]] == [
        "Quick",
        "Standard",
        "Full book",
    ]
    assert submitted(world) == []  # nothing was created, nothing invented
    assert not any(f.event == "task.created" for f in frames)
    assert "taiyar" not in reply_text(frames)  # the false completion claim never reaches the user
    (assistant,) = world.chat.rows("orchestrator")
    assert assistant.metadata["input_request"]["ask_id"] == card["ask_id"]
    assert len(stream_calls(world)) <= 1  # the card ends the turn


@pytest.mark.parametrize("prompt", ["notes banao", "ek PDF banao", "ppt banao", "research karo"])
async def test_hinglish_topicless_requests_ask_for_the_topic(world: World, prompt: str) -> None:
    account = await sign_in(world)
    world.models.queued_stream_scripts = [call("submit_task", {"instruction": INVENTED})]
    frames = events(await send(world, account, prompt))
    (card,) = cards(frames)
    assert card["questions"][0]["header"] == "Topic"
    assert submitted(world) == []


async def test_after_the_answer_the_task_starts_with_the_honest_text(world: World) -> None:
    account = await sign_in(world)
    world.models.queued_stream_scripts = [call("submit_task", {"instruction": INVENTED})]
    first = events(await send(world, account, SCREENSHOT_PROMPT))
    (card,) = cards(first)
    session_id = first[0].data["session_id"]

    world.models.queued_stream_scripts = [
        call(
            "submit_task",
            {"instruction": "Detailed notes on Java streams, save to Notion", "title": "Java"},
        ),
        [{"text": f"{CLAIM} Progress is on the Tasks page."}],
    ]
    answer = {
        "ask_id": card["ask_id"],
        "answers": [
            {"selected": [], "text": "Java streams"},
            {"selected": ["Standard"]},
            {"selected": [], "text": "Study Hub"},
        ],
    }
    frames = events(await send(world, account, "x", session_id=session_id, answer=answer))

    assert cards(frames) == []
    assert any(f.event == "task.created" for f in frames)
    assert len(submitted(world)) == 1
    text = reply_text(frames)
    assert text.startswith("Started: Java.")
    assert "I will update you here (and by voice if voice is on) when it is done." in text
    assert "taiyar" not in text and text.rstrip().endswith("(still working)")
    assert names(frames)[-1] == "done"


async def test_a_request_with_a_topic_and_a_length_starts_without_a_card(world: World) -> None:
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        call("submit_task", {"instruction": "Java streams notes, 10 pages", "title": "Streams"}),
        [{"text": "Working on it."}],
    ]
    frames = events(await send(world, account, "Java streams ke 10 page ke notes banao"))
    assert cards(frames) == []
    assert len(submitted(world)) == 1
    # a long request is routed by the router, so the title is the default one
    assert reply_text(frames).startswith("Started: ")


async def test_isi_par_after_a_topic_in_the_conversation_asks_nothing(world: World) -> None:
    account = await sign_in(world)
    world.models.queued_stream_scripts = [[{"text": "Kafka consumer groups share partitions."}]]
    first = events(await send(world, account, "Explain Kafka consumer groups"))
    session_id = first[0].data["session_id"]

    world.models.queued_stream_scripts = [
        call("submit_task", {"instruction": "A 10 page PDF about Kafka", "title": "Kafka PDF"}),
        [{"text": "Started."}],
    ]
    frames = events(
        await send(world, account, "isi par 10 page ka PDF banao", session_id=session_id)
    )
    assert cards(frames) == []
    assert len(submitted(world)) == 1


async def test_a_notion_url_as_the_destination_needs_no_destination_question(
    world: World,
) -> None:
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        call("submit_task", {"instruction": "Java streams notes, 10 pages", "title": "Streams"}),
        [{"text": "Working on it."}],
    ]
    url = "https://www.notion.so/Study-Hub-" + "a" * 32
    frames = events(
        await send(world, account, f"Java streams ke 10 page ke notes banao, Notion: {url}")
    )
    assert cards(frames) == []
    assert len(submitted(world)) == 1


async def test_a_model_sentence_pointing_at_a_missing_approval_card_is_replaced(
    world: World,
) -> None:
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        [
            {
                "text": (
                    "Yeh email taiyar kiya ja raha hai. "
                    "Kripya is task card par Approve par click karein."
                )
            }
        ]
    ]
    frames = events(await send(world, account, "good send this to same mail"))
    text = reply_text(frames)
    assert "Approve par" not in (world.chat.rows("orchestrator")[0].content)
    assert "No approval card exists yet" in text


# ------------------------------------------------- Notion options, same-mail, voice words


async def test_the_destination_question_lists_real_notion_pages_with_the_first_suggested(
    tmp_path: Any,
) -> None:
    from tests.integration.test_chat_phase2_tools import wire as wire_phase2
    from tests.integration.test_chat_routing_regressions import WorkspaceNotionRepo
    from tests.support.notion_fake import FakeNotionApi

    api = FakeNotionApi()
    page = "11112222333344445555666677778888"
    api.pages[page] = {
        "object": "page",
        "id": page,
        "url": "https://notion.so/study-hub",
        "parent": {"type": "workspace"},
        "properties": {"title": {"type": "title", "title": [{"plain_text": "Study Hub"}]}},
    }
    api.children[page] = []
    async with make_world(artifacts_dir=tmp_path / "artifacts", chat_tool_loop=True) as loop_world:
        wire_phase2(
            loop_world, submitter=IdempotentSubmitter(), repo_tools=WorkspaceNotionRepo(api)
        )
        account = await sign_in(loop_world)
        loop_world.models.queued_stream_scripts = [call("submit_task", {"instruction": INVENTED})]
        frames = events(
            await send(loop_world, account, "Java streams ke notes banao aur notion me save karo")
        )
    (card,) = cards(frames)
    destination = next(q for q in card["questions"] if q["header"] == "Destination")
    assert [o["label"] for o in destination["options"]] == ["Study Hub", "A different page"]
    assert destination["options"][0]["description"] == "Suggested"


async def test_send_to_same_mail_asks_to_confirm_the_address_from_the_conversation(
    world: World,
) -> None:
    account = await sign_in(world)
    world.models.queued_stream_scripts = [[{"text": "Draft ready for coatcard.ai@gmail.com."}]]
    first = events(await send(world, account, "mail the summary to coatcard.ai@gmail.com"))
    session_id = first[0].data["session_id"]

    world.models.queued_stream_scripts = [
        call(
            "send_email",
            {"to": "coatcard.ai@gmail.com", "subject": "Summary", "body": "Hello"},
        ),
        [
            {
                "text": (
                    "Yeh email taiyar kiya ja raha hai. "
                    "Kripya is task card par Approve par click karein"
                )
            }
        ],
    ]
    frames = events(
        await send(world, account, "good send this to same mail", session_id=session_id)
    )
    (card,) = cards(frames)
    question = card["questions"][0]
    assert question["question"] == "Send to coatcard.ai@gmail.com?"
    assert question["header"] == "Recipient"
    assert submitted(world) == []  # nothing was prepared before the user confirmed
    assert "Approve par" not in reply_text(frames)


def test_voice_feeds_the_spoken_words_to_the_gate() -> None:
    import asyncio

    from app.voice.connection import VoiceConnection

    connection = object.__new__(VoiceConnection)
    connection._user_said = []
    connection._user_now = ""
    for delta in ("ek PDF ", "banao"):
        connection._note_user_words(
            {"type": "TRANSCRIPT", "role": "user", "final": False, "delta": delta}
        )
    questions = asyncio.run(
        connection._required_questions("A PDF about the Java collections framework")
    )
    assert questions and questions[0]["header"] == "Topic"  # the model's topic is not the user's
    connection._note_user_words(
        {"type": "TRANSCRIPT", "role": "user", "final": True, "delta": "", "text": "ek PDF banao"}
    )
    connection._note_user_words(
        {"type": "TRANSCRIPT", "role": "user", "final": False, "delta": "Java streams par PDF"}
    )
    assert asyncio.run(connection._required_questions("PDF about Java streams")) is None
