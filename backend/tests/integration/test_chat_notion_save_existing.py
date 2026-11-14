"""'Add this notes in Notion as it is' through the in-memory chat harness (no model, no network).

Existing content in the conversation is SAVED: the destination is the only question (a card with
real Notion pages), no 'Topic' question, no 'Draft ...' task, no model call that rewrites the
text, and the one approval step carries the exact text. Nothing reaches Notion.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any

import pytest

from app.services.notion_source import NOTION_TOOL
from tests.integration.test_chat_inline_files import IdempotentSubmitter
from tests.integration.test_chat_phase2_tools import wire
from tests.integration.test_chat_routing_regressions import WorkspaceNotionRepo
from tests.integration.test_chat_streaming import (
    World,
    events,
    make_world,
    reply_text,
    send,
    sign_in,
)
from tests.support.notion_fake import FakeNotionApi

PAGE = "11112222333344445555666677778888"
NOTES = (
    "# Java Streams\n\nA stream is a pipeline of aggregate operations over a source. It does "
    "not store data. Intermediate operations such as filter and map are lazy; a terminal "
    "operation such as collect starts the work.\n\n## Operations\n\n- filter\n- map\n- collect\n\n"
    "```java\nlist.stream().filter(x -> x > 1).toList();\n```\n"
)


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    api = FakeNotionApi()
    api.pages[PAGE] = {
        "object": "page",
        "id": PAGE,
        "url": "https://notion.so/ai-notes",
        "parent": {"type": "workspace"},
        "properties": {"title": {"type": "title", "title": [{"plain_text": "Ai NOTES"}]}},
    }
    api.children[PAGE] = []
    async with make_world(artifacts_dir=tmp_path / "artifacts", chat_tool_loop=True) as value:
        value.submitter = IdempotentSubmitter()  # type: ignore[attr-defined]
        wire(value, submitter=value.submitter, repo_tools=WorkspaceNotionRepo(api))  # type: ignore[attr-defined]
        yield value


def cards(frames: list[Any]) -> list[dict[str, Any]]:
    return [f.data for f in frames if f.event == "input.requested"]


def submitted(world: World) -> list[dict[str, Any]]:
    return world.submitter.submitted  # type: ignore[attr-defined, no-any-return]


async def with_notes(world: World, account: Any) -> str:
    world.models.queued_stream_scripts = [[{"text": NOTES}]]
    first = events(await send(world, account, "explain java streams"))
    return str(first[0].data["session_id"])


async def test_the_transcript_asks_only_the_destination_then_prepares_the_exact_text(
    world: World,
) -> None:
    account = await sign_in(world)
    session_id = await with_notes(world, account)
    world.models.queued_stream_scripts = []  # a model call would find nothing to run

    first = events(
        await send(world, account, "add this notes in notion as it is", session_id=session_id)
    )
    (card,) = cards(first)
    assert [q["header"] for q in card["questions"]] == ["Destination"]  # never "Topic"
    assert [o["label"] for o in card["questions"][0]["options"]][0] == "Ai NOTES"
    assert card["questions"][0]["options"][0]["description"] == "Suggested"
    assert submitted(world) == []

    answer = {"ask_id": card["ask_id"], "answers": [{"selected": [], "text": "Ai NOTES"}]}
    frames = events(await send(world, account, "x", session_id=session_id, answer=answer))

    assert cards(frames) == []
    (call,) = submitted(world)
    (step,) = call["steps"]
    hints = step.input_data["notion_book"]
    assert step.tool == NOTION_TOOL
    assert hints["inline_markdown"] == NOTES.strip()  # the existing text, not regenerated
    assert hints["as_is"] is True and hints["parent_page_id"] == PAGE
    # the task is named for the save, never for the card answers
    assert call["instruction"].startswith("Save existing content to Notion: Java Streams")
    assert "Destination:" not in call["title"] and "Topic" not in call["instruction"]
    text = reply_text(frames)
    assert "exactly as it is" in text and "Nothing was rewritten" in text
    assert "nothing is in Notion yet" in text
    assert "Started: Destination" not in text


async def test_save_without_as_it_is_still_asks_only_for_the_destination(world: World) -> None:
    account = await sign_in(world)
    session_id = await with_notes(world, account)
    frames = events(await send(world, account, "Notion me save kardo", session_id=session_id))
    (card,) = cards(frames)
    assert [q["header"] for q in card["questions"]] == ["Destination"]
    assert submitted(world) == []


async def test_with_a_notion_link_the_step_is_prepared_at_once(world: World) -> None:
    account = await sign_in(world)
    session_id = await with_notes(world, account)
    url = "https://www.notion.so/Ai-NOTES-" + PAGE
    frames = events(
        await send(world, account, f"save this in notion {url} jaisa hai", session_id=session_id)
    )
    assert cards(frames) == []
    (call,) = submitted(world)
    hints = call["steps"][0].input_data["notion_book"]
    assert hints["parent_page_id"] == PAGE and hints["inline_markdown"] == NOTES.strip()


async def test_with_nothing_to_save_it_asks_which_notes_never_the_topic(world: World) -> None:
    account = await sign_in(world)
    world.models.queued_stream_scripts = []
    frames = events(await send(world, account, "add this notes in notion as it is"))
    (card,) = cards(frames)
    headers = [q["header"] for q in card["questions"]]
    assert headers == ["Source", "Destination"] and "Topic" not in headers
    assert "Which notes" in card["questions"][0]["question"]
    assert submitted(world) == []


async def test_a_short_assistant_message_is_not_the_notes(world: World) -> None:
    account = await sign_in(world)
    world.models.queued_stream_scripts = [[{"text": "Java_Streams.pdf (built 3 pages)"}]]
    first = events(await send(world, account, "hi"))
    session_id = str(first[0].data["session_id"])
    url = "https://www.notion.so/Ai-NOTES-" + PAGE
    frames = events(
        await send(world, account, f"add this notes in notion {url}", session_id=session_id)
    )
    # nothing substantive and no readable file in the chat state: honest, nothing prepared
    assert submitted(world) == []
    assert "could not find notes" in reply_text(frames)
