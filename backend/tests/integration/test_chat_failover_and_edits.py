"""Chat turn under model overload, and an edit of the file just made (fakes only, no network).

Covered: the exact owner prompts. A deck request shows no failed step; the Hinglish follow-up
"slide 3 mein ek point add karo" is an edit turn on the working-state file (not a new task); a
model that fails on every key ends the turn with the plain overload message, a stored interrupted
reply (Retry control) and a closed stream, never an endless spinner.
"""

import logging
from collections.abc import AsyncIterator
from pathlib import Path
from uuid import UUID

import pytest
from google.genai.errors import ServerError

from tests.integration.test_chat_inline_files import IdempotentSubmitter
from tests.integration.test_chat_progress import all_ended, final_states, progress
from tests.integration.test_chat_streaming import (
    World,
    events,
    make_world,
    names,
    send,
    sign_in,
)
from tests.integration.test_chat_tool_loop import SQL_DECK, call, wire
from tests.support.models import LITE, ORCH

DECK_PROMPT = "6 slide deck AI in healthcare par, dark theme"
EDIT_PROMPT = "slide 3 mein ek point add karo"


@pytest.fixture
async def loop_world(tmp_path: Path) -> AsyncIterator[World]:
    async with make_world(artifacts_dir=tmp_path / "artifacts", chat_tool_loop=True) as value:
        yield value


async def test_the_owner_deck_prompt_shows_no_failed_step(loop_world: World) -> None:
    wire(loop_world, submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("create_document", {"format": "pptx", "title": "AI in Healthcare", "spec": SQL_DECK}),
        [{"text": "Your deck is ready."}],
    ]
    frames = events(await send(loop_world, account, DECK_PROMPT))

    assert names(frames)[-1] == "done"
    assert all_ended(frames)
    states = {s["label"]: s["state"] for s in final_states(progress(frames)).values()}
    assert "failed" not in states.values(), states


async def test_the_hinglish_edit_after_a_deck_is_an_edit_turn_not_a_new_task(
    loop_world: World, caplog: pytest.LogCaptureFixture
) -> None:
    wire(loop_world, submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("create_document", {"format": "pptx", "title": "AI in Healthcare", "spec": SQL_DECK}),
        [{"text": "Your deck is ready."}],
    ]
    first = events(await send(loop_world, account, DECK_PROMPT))
    session_id = UUID(first[0].data["session_id"])
    (card,) = [f.data for f in first if f.event == "file.created"]

    loop_world.models.queued_stream_scripts = [
        call(
            "create_document",
            {
                "format": "pptx",
                "title": "AI in Healthcare",
                "spec": SQL_DECK,
                "edits_file_id": card["file_id"],
            },
        ),
        [{"text": "Added the point to slide 3."}],
    ]
    with caplog.at_level(logging.INFO, logger="app.services.chat_service"):
        second = events(await send(loop_world, account, EDIT_PROMPT, session_id=session_id))

    assert "chat_route route=model reason=edit_working_file" in caplog.text
    assert "reason=binary_deliverable" not in caplog.text.split("edit_working_file")[-1]
    assert names(second)[-1] == "done"
    assert [f for f in second if f.event == "file.created"]


async def test_a_model_failing_on_every_key_ends_with_the_overload_message_and_retry(
    loop_world: World, caplog: pytest.LogCaptureFixture
) -> None:
    account = await sign_in(loop_world)
    boom = ServerError(503, {})
    loop_world.fake.aio.models.enqueue_errors(ORCH, [boom, boom, boom, boom])
    loop_world.fake.aio.models.enqueue_errors(LITE, [boom, boom, boom, boom])

    with caplog.at_level(logging.INFO, logger="app.services.chat_service"):
        response = await send(loop_world, account, "anything")

    frames = events(response)
    text = "".join(f.data["text"] for f in frames if f.event == "delta")
    # Wording unified across chat, tasks and voice (owner request); Retry is the web control.
    assert "Gemini is overloaded right now" in text and "please try again in a minute" in text
    assert names(frames)[-1] == "error"
    assert frames[-1].data["retryable"] is True
    assert all_ended(frames)  # no step is left spinning
    assert "chat_turn_timing outcome=failed" in caplog.text
    assert "failover_reason=" in caplog.text
    assert loop_world.fake.aio.models.open_streams == 0
