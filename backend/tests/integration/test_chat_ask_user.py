"""ask_user: MAYA asks a question card, the turn ends, the answer resumes the request. No network.

The card is not an approval: no task, approval or write is created or approved by asking or by
answering. State is derived from the messages (awaiting, answered, dismissed), never rewritten.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from typing import Any

import pytest

from tests.integration.test_chat_inline_files import IdempotentSubmitter
from tests.integration.test_chat_latency import wire
from tests.integration.test_chat_memory import Prefs, tool_names
from tests.integration.test_chat_streaming import (
    CHAT,
    World,
    events,
    make_world,
    names,
    send,
    sign_in,
    stream_calls,
)

QUESTIONS = {
    "questions": [
        {
            "question": "Which language should I use?",
            "header": "Language",
            "options": [
                {"label": "Java", "description": "Strongly typed"},
                {"label": "Python"},
                {"label": "Go"},
            ],
        },
        {
            "question": "Which theme?",
            "header": "Theme",
            "options": [{"label": "glass-aurora"}, {"label": "plain"}],
            "multi_select": True,
            "allow_text": False,
        },
    ]
}


@pytest.fixture
async def world() -> AsyncIterator[World]:
    async with make_world() as value:
        value.submitter = IdempotentSubmitter()  # type: ignore[attr-defined]
        wire(value, prefs=Prefs(memory_on=False), submitter=value.submitter)  # type: ignore[attr-defined]
        yield value


def ask_script(args: dict[str, Any] | None = None, lead: str = "") -> list[dict[str, Any]]:
    chunks: list[dict[str, Any]] = [{"text": lead}] if lead else []
    chunks.append({"call": {"name": "ask_user", "args": args or QUESTIONS}})
    return chunks


def card_events(frames: list[Any]) -> list[dict[str, Any]]:
    return [f.data for f in frames if f.event == "input.requested"]


async def ask_once(world: World, account: Any) -> tuple[str, dict[str, Any], list[Any]]:
    world.models.queued_stream_scripts = [ask_script(lead="Quick check first.")]
    frames = events(await send(world, account, "Write a sorting program"))
    (card,) = card_events(frames)
    return frames[0].data["session_id"], card, frames


async def test_ask_user_is_offered_with_the_guidance_and_ends_the_turn(world: World) -> None:
    account = await sign_in(world)
    session_id, card, frames = await ask_once(world, account)

    (only,) = stream_calls(world)  # no model continuation: the turn ends at the card
    assert "ask_user" in tool_names(only, with_ask=True)
    assert "ask_user tool" in str(only["config"].system_instruction)
    order = names(frames)
    assert order.index("input.requested") < order.index("done") and "error" not in order
    assert card["state"] == "awaiting_input" and card["source"] == "chat"
    assert card["ask_id"].startswith("ask_")
    assert [q["header"] for q in card["questions"]] == ["Language", "Theme"]
    assert card["questions"][1]["multi_select"] is True and card["questions"][0]["allow_text"]
    (assistant,) = world.chat.rows("orchestrator")
    stored = assistant.metadata["input_request"]
    assert (
        stored["ask_id"] == card["ask_id"] and stored["call"]["id"]
    )  # call id kept for the answer
    assert assistant.content == "Quick check first."
    assert not any(f.event == "task.created" for f in frames)
    assert session_id


async def test_a_question_alone_gets_its_text_as_the_message(world: World) -> None:
    account = await sign_in(world)
    world.models.queued_stream_scripts = [ask_script()]
    frames = events(await send(world, account, "Write a sorting program"))
    (assistant,) = world.chat.rows("orchestrator")
    assert assistant.content == "Which language should I use?"  # never an empty message
    assert names(frames)[-1] == "done"


async def test_the_card_arguments_are_clamped_and_scrubbed(world: World) -> None:
    account = await sign_in(world)
    many = {
        "questions": [
            {
                "question": "Q" * 900 + "\x00\x07",
                "header": "A very long header here",
                "options": [{"label": f"opt {i}", "description": "d" * 500} for i in range(12)],
            }
        ]
        + [{"question": f"extra {i}"} for i in range(8)]
    }
    world.models.queued_stream_scripts = [ask_script(many)]
    frames = events(await send(world, account, "x y z"))
    (card,) = card_events(frames)
    assert len(card["questions"]) == 4
    first = card["questions"][0]
    assert len(first["question"]) <= 300 and "\x00" not in first["question"]
    assert len(first["header"]) <= 12 and len(first["options"]) == 6
    assert all(len(o["description"]) <= 160 for o in first["options"])
    # no options means a short text question, and its free-text field is on
    assert card["questions"][1]["options"] == [] and card["questions"][1]["allow_text"] is True


async def test_unusable_questions_show_no_card_and_the_model_continues(world: World) -> None:
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        ask_script({"questions": []}),
        [{"text": "I will use Python."}],
    ]
    frames = events(await send(world, account, "Write a sorting program"))
    assert card_events(frames) == []
    assert "I will use Python." in "".join(f.data.get("text", "") for f in frames)


async def test_the_answer_is_the_function_response_of_the_pending_call(world: World) -> None:
    submitter = world.submitter  # type: ignore[attr-defined]
    account = await sign_in(world)
    session_id, card, _ = await ask_once(world, account)

    world.models.queued_stream_scripts = [[{"text": "Here is the Java program."}]]
    answer = {
        "ask_id": card["ask_id"],
        "answers": [
            {"selected": ["Java", "Python"], "text": "ignored for a card with options"},
            {"selected": ["glass-aurora", "not-an-option"], "text": "no text field here"},
        ],
    }
    frames = events(
        await send(
            world, account, "whatever the client typed", session_id=session_id, answer=answer
        )
    )
    assert names(frames)[-1] == "done"
    assert "Here is the Java program." in "".join(f.data.get("text", "") for f in frames)

    second = stream_calls(world)[1]["contents"]
    call_turn, response_turn = second[-2], second[-1]
    assert call_turn["role"] == "model" and response_turn["role"] == "user"
    function_call = next(p for p in call_turn["parts"] if "function_call" in p)["function_call"]
    function_response = response_turn["parts"][0]["function_response"]
    assert function_call["name"] == function_response["name"] == "ask_user"
    assert function_call["id"] == function_response["id"]  # the pairing is valid
    answers = function_response["response"]["answers"]
    assert answers[0]["selected"] == ["Java"]  # one choice on a single-choice question
    assert answers[1]["selected"] == ["glass-aurora"]  # a made-up option is dropped
    assert answers[0]["text"] == "ignored for a card with options"  # allow_text defaults on
    assert answers[1]["text"] == ""  # no free text where the card turned it off
    assert len(response_turn["parts"]) == 1  # nothing else rides along the function response

    user_rows = world.chat.rows("user")
    assert (
        user_rows[-1].content
        == "Language: Java, ignored for a card with options; Theme: glass-aurora"
    )  # server-built, readable
    assert user_rows[-1].metadata["answers_to"] == card["ask_id"]
    # The card is not an approval: no task was started and nothing was written.
    assert submitter.submitted == [] and not any(f.event == "task.created" for f in frames)

    page = await world.client.get(f"{CHAT}/sessions/{session_id}/messages", headers=account.headers)
    items = page.json()["items"]
    assert items[1]["metadata"]["input_request"]["state"] == "answered"
    assert items[2]["metadata"]["answer"]["ask_id"] == card["ask_id"]


async def test_typing_instead_dismisses_the_card_and_is_answered_normally(world: World) -> None:
    account = await sign_in(world)
    session_id, card, _ = await ask_once(world, account)

    page = await world.client.get(f"{CHAT}/sessions/{session_id}/messages", headers=account.headers)
    assert page.json()["items"][1]["metadata"]["input_request"]["state"] == "awaiting_input"

    world.models.queued_stream_scripts = [[{"text": "Sure, Rust it is."}]]
    frames = events(await send(world, account, "Actually use Rust", session_id=session_id))
    assert names(frames)[-1] == "done"
    second = stream_calls(world)[1]["contents"]
    assert all("function_response" not in p for turn in second for p in turn["parts"])
    page = await world.client.get(f"{CHAT}/sessions/{session_id}/messages", headers=account.headers)
    assert page.json()["items"][1]["metadata"]["input_request"]["state"] == "dismissed"
    assert card["ask_id"]


async def test_an_answer_to_a_card_that_is_no_longer_pending_is_a_normal_message(
    world: World,
) -> None:
    account = await sign_in(world)
    session_id, card, _ = await ask_once(world, account)
    world.models.queued_stream_scripts = [[{"text": "Ok."}]]
    events(await send(world, account, "typed", session_id=session_id))  # dismisses the card
    world.models.queued_stream_scripts = [[{"text": "Noted."}]]
    stale = {"ask_id": card["ask_id"], "answers": [{"selected": ["Java"]}, {"selected": []}]}
    events(await send(world, account, "late reply", session_id=session_id, answer=stale))
    last = stream_calls(world)[-1]["contents"]
    assert all("function_response" not in p for turn in last for p in turn["parts"])
    assert world.chat.rows("user")[-1].content == "late reply"
    assert "answers_to" not in world.chat.rows("user")[-1].metadata


async def test_an_empty_answer_is_refused_and_the_card_still_waits(world: World) -> None:
    account = await sign_in(world)
    session_id, card, _ = await ask_once(world, account)
    empty = {"ask_id": card["ask_id"], "answers": [{"selected": []}, {"selected": []}]}
    response = await send(world, account, "x", session_id=session_id, answer=empty)
    assert response.status_code == 422
    assert [m.sender for m in world.chat.messages].count("user") == 1  # nothing was stored


async def test_the_answer_text_is_scrubbed_and_capped(world: World) -> None:
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        ask_script({"questions": [{"question": "Which folder?", "header": "Folder"}]})
    ]
    frames = events(await send(world, account, "save it"))
    (card,) = card_events(frames)
    world.models.queued_stream_scripts = [[{"text": "Saved."}]]
    noisy = "reports\x00\x07 " + "x" * 1800 + " sk-" + "a" * 40
    answer = {"ask_id": card["ask_id"], "answers": [{"text": noisy}]}
    events(
        await send(
            world,
            account,
            "typed",
            session_id=frames[0].data["session_id"],
            answer=answer,
        )
    )
    text = world.chat.rows("user")[-1].content
    assert (
        text.startswith("Folder: reports") and "\x00" not in text and "sk-" + "a" * 40 not in text
    )
    assert len(text) <= 600


async def test_only_one_card_waits_per_conversation(world: World) -> None:
    account = await sign_in(world)
    session_id, first, _ = await ask_once(world, account)
    # A new question after typing: the earlier card is dismissed, the new one waits.
    world.models.queued_stream_scripts = [ask_script({"questions": [{"question": "Name?"}]})]
    events(await send(world, account, "different task", session_id=session_id))
    page = await world.client.get(f"{CHAT}/sessions/{session_id}/messages", headers=account.headers)
    states = [
        i["metadata"]["input_request"]["state"]
        for i in page.json()["items"]
        if i["metadata"]["input_request"] is not None
    ]
    assert states == ["dismissed", "awaiting_input"] and first["ask_id"]


async def test_the_request_schema_rejects_extra_answer_fields(world: World) -> None:
    account = await sign_in(world)
    bad = {"ask_id": "ask_x", "answers": [{"selected": [], "approve": True}]}
    response = await send(world, account, "x", answer=bad)
    assert response.status_code == 422
