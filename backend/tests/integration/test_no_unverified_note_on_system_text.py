"""The decision-model "general knowledge, not checked against the web" note belongs only to a
model-written factual answer. Owner bug: it was appended to the code-written "Started: ..." text of
two tasks. Fakes only; no network."""

from __future__ import annotations

from collections.abc import AsyncIterator
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.decisions import runtime as decisions
from app.decisions.answer_check import UNVERIFIED_NOTE
from app.schemas.chat import TaskCreatedEvent
from app.services.chat_service import _LiveTurn
from app.services.start_guard import started_text
from tests.integration.test_chat_deep_research import search_service, wire
from tests.integration.test_chat_inline_files import IdempotentSubmitter
from tests.integration.test_chat_streaming import (
    World,
    events,
    make_world,
    reply_text,
    send,
    sign_in,
)
from tests.support.decisions import FakeDecider, noul_a

ASKS_FOR_THE_WEB = FakeDecider({"needs_web_search": noul_a(0.99)})


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    async with make_world(artifacts_dir=tmp_path / "artifacts") as value:
        yield value


async def test_the_deep_research_start_text_never_gets_the_unverified_note(world: World) -> None:
    decisions.set_current(ASKS_FOR_THE_WEB)
    wire(world, search_service(), IdempotentSubmitter())
    account = await sign_in(world)

    frames = events(
        await send(
            world, account, "Research solar battery storage costs in India", mode="deep_research"
        )
    )

    text = reply_text(frames)
    assert text.startswith("Started: deep research on ")
    assert "general knowledge" not in text and UNVERIFIED_NOTE not in text


async def test_a_deep_research_phrase_in_the_message_gets_no_note_either(world: World) -> None:
    decisions.set_current(ASKS_FOR_THE_WEB)
    wire(world, search_service(), IdempotentSubmitter())
    account = await sign_in(world)

    frames = events(await send(world, account, "deep research the latest Java features"))

    assert "general knowledge" not in reply_text(frames)


def bare_service(**progress: Any) -> Any:
    """A live turn with only the attributes ``_answer_check_notes`` reads.

    Typed ``Any`` on purpose: the object is built with ``object.__new__`` and only some of its
    attributes are set, with stand-in objects (SimpleNamespace) that are not the real types.
    """
    service: Any = object.__new__(_LiveTurn)
    service._plan = SimpleNamespace(content="Research the latest Java features with sources")
    base: dict[str, Any] = {
        "task": None,
        "input_request": None,
        "files": [],
        "agent_drafts": [],
        "schedule_drafts": [],
        "memories": [],
    }
    service._progress = SimpleNamespace(**{**base, **progress})
    service._gate_card = None
    service._code_written_reply = False
    service._web_searches = 0
    service._failures = []
    service._w = SimpleNamespace(web_search=object())
    return service


EXACT_START_TEXTS = [
    started_text("Research the latest Java features with sources", 2),
    started_text("Deep research: Research solar battery storage costs in India", 1),
    started_text("Make a PDF about solar batteries", None),
]


@pytest.mark.parametrize("text", EXACT_START_TEXTS)
async def test_a_turn_that_started_a_task_gets_no_note_for_its_start_text(text: str) -> None:
    decisions.set_current(ASKS_FOR_THE_WEB)
    task = TaskCreatedEvent(task_id=uuid4(), title="t", status="executing", promoted=False)
    service = bare_service(task=task)
    service._progress.parts = [text]

    assert await service._answer_check_notes(text) == []
    assert ASKS_FOR_THE_WEB.calls == []  # the decision model was not even asked


async def test_code_written_cards_and_replies_get_no_note() -> None:
    decisions.set_current(ASKS_FOR_THE_WEB)
    long_text = "Which Notion page should I use? " * 6
    for service in (
        bare_service(input_request=object()),
        bare_service(files=[object()]),
        bare_service(agent_drafts=[object()]),
        bare_service(schedule_drafts=[object()]),
        bare_service(memories=[object()]),
    ):
        assert await service._answer_check_notes(long_text) == []
    gate = bare_service()
    gate._gate_card = object()
    assert await gate._answer_check_notes(long_text) == []
    coded = bare_service()
    coded._code_written_reply = True
    assert await coded._answer_check_notes(long_text) == []


async def test_a_model_written_factual_answer_without_sources_still_gets_the_note() -> None:
    decisions.set_current(ASKS_FOR_THE_WEB)
    service = bare_service()
    answer = "Java 21 is the current long term support release with virtual threads. " * 2

    notes = await service._answer_check_notes(answer)

    assert notes == ["\n\n" + UNVERIFIED_NOTE]
