"""The live step list on the chat endpoint (fakes only: scripted model, memory storage).

Covered: thought summaries become "think" steps before the reply text; with the flag off no thought
step is shown and the reply text is unchanged; a model that sends no summary gives one honest
skipped step, not an error; the tool loop shows plan, tool, render and save steps in order and a
failed render shows as failed; the steps are stored in the assistant message metadata and replayed
on a repeated idempotency key.
"""

import asyncio
import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest
from google.genai import types

from app.gateway.exceptions import GatewayFallbackExhaustedError
from app.services import chat_service
from app.services.document_tools import JobView
from tests.integration.test_chat_image import (
    RecordingImageClient,
    image_client,  # noqa: F401 (fixture)
)
from tests.integration.test_chat_inline_files import IdempotentSubmitter, RefusingSubmitter
from tests.integration.test_chat_memory import SAVE_CALL, FakeMemory, Prefs
from tests.integration.test_chat_memory import wire as memory_wire
from tests.integration.test_chat_notion_tools import RecordingNotionRepo
from tests.integration.test_chat_phase2_tools import FakeSearchGateway
from tests.integration.test_chat_phase2_tools import wire as phase2_wire
from tests.integration.test_chat_repo_tools import URL, RecordingRepoTools
from tests.integration.test_chat_repo_tools import wire as repo_wire
from tests.integration.test_chat_streaming import (
    World,
    begin,
    chunk,
    events,
    make_world,
    names,
    new_key,
    send,
    sign_in,
    stream_calls,
    wait_until,
)
from tests.integration.test_chat_tool_loop import (
    CSV_BAD,
    CSV_GOOD,
    SQL_DECK,
    FakeJobs,
    FixedJobSubmitter,
    call,
    wire,
)

TERMINAL = {"done", "failed", "skipped"}


def progress(frames: list[Any]) -> list[dict[str, Any]]:
    return [f.data for f in frames if f.event == "progress"]


def final_states(steps: list[dict[str, Any]]) -> dict[str, dict[str, Any]]:
    """The last event of each step: what a client shows once the reply is over."""
    latest: dict[str, dict[str, Any]] = {}
    for step in steps:
        latest[step["step_id"]] = step
    return latest


@pytest.fixture
async def plain_world() -> AsyncIterator[World]:
    async with make_world() as value:
        yield value


@pytest.fixture
async def off_world() -> AsyncIterator[World]:
    async with make_world(thinking_summaries=False) as value:
        yield value


@pytest.fixture
async def loop_world(tmp_path: Path) -> AsyncIterator[World]:
    async with make_world(artifacts_dir=tmp_path / "artifacts", chat_tool_loop=True) as value:
        yield value


async def test_a_thought_summary_is_a_think_step_before_the_reply_text(
    plain_world: World,
) -> None:
    account = await sign_in(plain_world)
    plain_world.models.queued_stream_scripts = [
        [{"thought": "Checking the request."}, {"text": "Hello."}],
    ]
    frames = events(await send(plain_world, account, "Hi"))

    think = [s for s in progress(frames) if s["kind"] == "think"]
    assert [s["state"] for s in think] == ["running", "done"]
    assert think[-1]["thought"] == "Checking the request."
    delta_at = names(frames, with_progress=True).index("delta")
    assert names(frames, with_progress=True).index("progress") < delta_at
    assert "".join(f.data["text"] for f in frames if f.event == "delta") == "Hello."
    assert names(frames)[-1] == "done"


async def test_with_summaries_off_no_thought_is_shown_and_the_text_is_unchanged(
    off_world: World,
) -> None:
    account = await sign_in(off_world)
    off_world.models.queued_stream_scripts = [
        [{"thought": "private reasoning"}, {"text": "Hello."}],
    ]
    frames = events(await send(off_world, account, "Hi"))

    assert [s for s in progress(frames) if s["kind"] == "think"] == []
    assert "private reasoning" not in json.dumps(progress(frames))
    assert "".join(f.data["text"] for f in frames if f.event == "delta") == "Hello."
    assert names(frames)[-1] == "done"


async def test_a_model_that_sends_no_summary_shows_one_skipped_step_not_an_error(
    plain_world: World,
) -> None:
    account = await sign_in(plain_world)
    # The model thought (40 thought tokens were billed) but sent no summary.
    plain_world.models.queued_stream_scripts = [[{"text": "Hello.", "thoughts": 40}]]
    frames = events(await send(plain_world, account, "Hi"))

    think = [s for s in progress(frames) if s["kind"] == "think"]
    assert [s["state"] for s in think] == ["skipped"]
    assert "No reasoning summary" in think[0]["detail"]
    assert "error" not in names(frames) and names(frames)[-1] == "done"


async def test_a_reply_with_no_thinking_at_all_shows_no_steps(plain_world: World) -> None:
    account = await sign_in(plain_world)
    plain_world.models.queued_stream_scripts = [[{"text": "Hello."}]]
    frames = events(await send(plain_world, account, "Hi"))

    assert progress(frames) == []  # nothing to explain, so no step and no metadata key
    assert names(frames)[-1] == "done"


async def test_the_loop_shows_plan_then_tool_then_save_in_order(
    loop_world: World, tmp_path: Path
) -> None:
    wire(loop_world, submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("create_document", {"format": "csv", "title": "Budget", "spec": {"text": CSV_GOOD}}),
        [{"text": "I made Budget.csv with two rows."}],
    ]
    frames = events(await send(loop_world, account, "Make a budget CSV with two rows"))

    steps = progress(frames)
    labels = [(s["kind"], s["label"], s["state"]) for s in steps]
    assert labels[0] == ("plan", "Planning the reply", "done")
    assert ("step", "Planning the document", "done") in labels
    assert ("render", "Rendering CSV", "running") in labels
    assert ("render", "Rendering CSV", "done") in labels
    assert ("save", "Saved Budget.csv", "done") in labels

    # The render step is nested under the create_document tool step, and the save step comes
    # after the tool has finished (the file card follows the tool result).
    tool_step = next(s for s in steps if s["label"] == "Rendering CSV" and s["state"] == "running")
    assert tool_step["parent_id"] is not None
    save_at = next(i for i, s in enumerate(steps) if s["kind"] == "save")
    assert save_at > labels.index(("render", "Rendering CSV", "done"))
    assert len({s["run_id"] for s in steps}) == 1
    assert names(frames)[-1] == "done"


async def test_a_repairable_render_problem_shows_as_an_adjustment_and_the_reply_still_finishes(
    loop_world: World,
) -> None:
    wire(loop_world, submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("create_document", {"format": "csv", "title": "Budget", "spec": {"text": CSV_BAD}}),
        call("create_document", {"format": "csv", "title": "Budget", "spec": {"text": CSV_GOOD}}),
        [{"text": "I made Budget.csv with two rows."}],
    ]
    frames = events(await send(loop_world, account, "Make a budget CSV with two rows"))

    # Changed on purpose: this used to expect state "failed" with the model-facing reason
    # ("CSV row 3 ..."). A problem the model repairs itself is not a failure for the user: the
    # step is the muted "skipped" state with one short sentence, and the long instruction text
    # stays in the tool result for the model only.
    renders = [s for s in progress(frames) if s["label"] == "Rendering CSV"]
    assert not [s for s in renders if s["state"] == "failed"]
    adjusted = [s["detail"] for s in renders if s["state"] == "skipped"]
    assert len(adjusted) == 1 and "call create_document" not in adjusted[0]
    assert len(adjusted[0]) <= 80
    assert any(s["state"] == "done" for s in renders)
    assert names(frames)[-1] == "done"


async def test_the_steps_are_stored_with_the_reply_and_replayed_on_a_repeated_key(
    loop_world: World,
) -> None:
    wire(loop_world, submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("create_document", {"format": "csv", "title": "Budget", "spec": {"text": CSV_GOOD}}),
        [{"text": "I made Budget.csv with two rows."}],
    ]
    key = new_key()
    first = events(await send(loop_world, account, "Make a budget CSV with two rows", key=key))
    first_final = final_states(progress(first))
    assert first_final and all(s["state"] in TERMINAL for s in first_final.values())

    stored = [m for m in loop_world.chat.messages if m.sender == "orchestrator"]
    (reply,) = stored[-1:]
    assert reply.metadata["progress"] and all(
        s["state"] in TERMINAL for s in reply.metadata["progress"]
    )

    replayed = events(await send(loop_world, account, "Make a budget CSV with two rows", key=key))
    replay_final = {s["step_id"]: s for s in progress(replayed)}
    assert set(replay_final) == set(first_final)
    assert {s["label"] for s in replay_final.values()} == {s["label"] for s in first_final.values()}
    assert names(replayed, with_progress=True).index("progress") < names(replayed).index("done")


# ------------------------------------------------------- every path ends its steps (workstream C)


def thought_chunk(text: str) -> types.GenerateContentResponse:
    return types.GenerateContentResponse(
        candidates=[
            types.Candidate(
                content=types.Content(parts=[types.Part(text=text, thought=True)], role="model")
            )
        ]
    )


def states_of(frames: list[Any], label: str) -> list[str]:
    return [s["state"] for s in progress(frames) if s["label"] == label]


def all_ended(frames: list[Any]) -> bool:
    final = final_states(progress(frames))
    return bool(final) and all(s["state"] in TERMINAL for s in final.values())


async def test_with_summaries_on_thinking_is_requested_and_the_summary_is_stored(
    plain_world: World,
) -> None:
    account = await sign_in(plain_world)
    plain_world.models.queued_stream_scripts = [
        [{"thought": "Checking the request."}, {"text": "Hello."}],
    ]
    events(await send(plain_world, account, "Hi"))

    (only,) = stream_calls(plain_world)
    assert only["config"].thinking_config.include_thoughts is True
    (reply,) = plain_world.chat.rows("orchestrator")
    (think,) = [s for s in reply.metadata["progress"] if s["kind"] == "think"]
    assert (think["state"], think["thought"]) == ("done", "Checking the request.")
    assert "Checking the request." not in reply.content  # a summary is never answer text


async def test_with_summaries_off_no_thinking_is_requested_and_no_thought_is_stored(
    off_world: World,
) -> None:
    account = await sign_in(off_world)
    off_world.models.queued_stream_scripts = [
        # Thought tokens were billed and a summary came back anyway: neither may show.
        [{"thought": "private reasoning", "thoughts": 40}, {"text": "Hello."}],
    ]
    key = new_key()
    frames = events(await send(off_world, account, "Hi", key=key))

    (only,) = stream_calls(off_world)
    assert only["config"].thinking_config is None  # no thinking config reaches the model
    assert progress(frames) == []  # no think step, not even the "no summary" note
    (reply,) = off_world.chat.rows("orchestrator")
    assert "progress" not in reply.metadata
    assert "private reasoning" not in json.dumps(reply.metadata) + reply.content
    replayed = events(await send(off_world, account, "Hi", key=key))
    assert progress(replayed) == []


async def test_a_reply_that_breaks_while_thinking_leaves_no_step_running(
    plain_world: World,
) -> None:
    account = await sign_in(plain_world)
    plain_world.models.script = [thought_chunk("Working it out."), ConnectionResetError("cut")]
    frames = events(await send(plain_world, account, "Hi"))

    assert all_ended(frames)
    assert states_of(frames, "Reasoning")[0] == "running"
    assert states_of(frames, "Reply stopped") == ["failed"]
    assert names(frames)[-1] == "error"
    assert names(frames, with_progress=True)[-2] == "progress"  # the steps end before the error


async def test_a_reply_past_its_deadline_says_so_and_leaves_no_step_running() -> None:
    async with make_world(chat_reply_deadline_seconds=1) as world:
        account = await sign_in(world)
        world.models.script = [thought_chunk("Working it out."), asyncio.Event()]
        frames = events(await send(world, account, "Hi"))

        assert all_ended(frames)
        assert states_of(frames, "Reply time limit reached") == ["failed"]
        assert frames[-1].data["code"] == "reply_deadline_exceeded"


async def test_a_cancelled_reply_stores_its_open_step_as_failed(plain_world: World) -> None:
    account = await sign_in(plain_world)
    gate = asyncio.Event()  # never set: the model goes quiet while it is still thinking
    plain_world.models.script = [chunk("alpha "), thought_chunk("Still working."), gate]
    turn = await begin(plain_world, account)
    received: list[str] = []

    async def consume() -> None:
        async for frame in turn.events():
            received.append(frame)

    task = asyncio.create_task(consume())
    await wait_until(lambda: any("Reasoning" in frame for frame in received))
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    await turn.close()

    (reply,) = plain_world.chat.rows("orchestrator")
    assert reply.metadata["status"] == "interrupted"
    (think,) = reply.metadata["progress"]
    assert (think["kind"], think["state"]) == ("think", "failed")


async def test_a_github_read_outside_the_loop_is_a_step_that_ends_done(
    plain_world: World,
) -> None:
    repo_wire(plain_world, RecordingRepoTools([], connected=False))
    account = await sign_in(plain_world)
    plain_world.models.queued_stream_scripts = [
        [{"call": {"name": "github_get_repo", "args": {"repo": URL}}}],
        [{"call": {"name": "github_read_file", "args": {"repo": URL, "path": "missing.md"}}}],
        [{"text": "Review: the repository uses main."}],
    ]
    frames = events(await send(plain_world, account, f"review {URL} using mcp"))

    # The first read worked and the second (a file that does not exist) did not.
    assert states_of(frames, "Reading GitHub") == ["running", "done", "running", "failed"]
    assert all_ended(frames) and names(frames)[-1] == "done"


async def test_a_memory_save_outside_the_loop_is_a_step_that_ends_done(
    plain_world: World,
) -> None:
    memory_wire(plain_world, FakeMemory())
    account = await sign_in(plain_world)
    plain_world.models.queued_stream_scripts = [
        [{"call": SAVE_CALL}],
        [{"text": "Saved: your favourite colour is blue."}],
    ]
    frames = events(await send(plain_world, account, "Remember my favourite colour is blue"))

    assert states_of(frames, "Saving to memory") == ["running", "done"]
    assert states_of(frames, "Saved to memory") == ["done"]
    assert all_ended(frames) and names(frames)[-1] == "done"


async def test_a_task_started_outside_the_loop_is_a_step_and_a_refusal_is_not_done(
    plain_world: World,
) -> None:
    # The default path for a document or deck request: the router starts the task itself, with no
    # model tool call. It still shows one step, and the task card follows (task.created).
    phase2_wire(plain_world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(plain_world)
    plain_world.models.queued_stream_scripts = [[{"text": "The task has started."}]]
    frames = events(await send(plain_world, account, "make a pdf about sleep"))
    assert states_of(frames, "Starting background task") == ["running", "done"]
    assert "task.created" in names(frames) and all_ended(frames)
    (reply,) = plain_world.chat.rows("orchestrator")
    assert [s["state"] for s in reply.metadata["progress"]] == ["done"]

    phase2_wire(plain_world, prefs=Prefs(memory_on=False), submitter=RefusingSubmitter())
    plain_world.models.queued_stream_scripts = [[{"text": "I could not start that."}]]
    refused = events(await send(plain_world, account, "make a pdf about my private repo"))
    assert states_of(refused, "Starting background task") == ["running", "failed"]
    assert "task.created" not in names(refused) and all_ended(refused)


async def test_a_task_the_model_starts_with_submit_task_shows_one_step_only() -> None:
    async with make_world(chat_inline_files=False) as legacy:
        phase2_wire(legacy, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
        account = await sign_in(legacy)
        legacy.models.queued_stream_scripts = [
            call("submit_task", {"instruction": "compare three laptops in a report"}),
            [{"text": "The task has started."}],
        ]
        frames = events(await send(legacy, account, "compare three laptops in a report"))

        assert states_of(frames, "Starting background task") == ["running", "done"]
        assert "task.created" in names(frames) and all_ended(frames)


async def test_web_search_steps_show_done_and_failed(loop_world: World) -> None:
    search = FakeSearchGateway()
    phase2_wire(loop_world, search=search)
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("web_search", {"query": "solar power news"}),
        [{"text": "Solar output rose in 2026 [1]."}],
    ]
    frames = events(await send(loop_world, account, "What is the latest news on solar power?"))
    assert states_of(frames, "Searching the web") == ["running", "done"]
    assert "solar power news" not in json.dumps(progress(frames))  # no raw tool argument

    search.error = GatewayFallbackExhaustedError("quota")
    loop_world.models.queued_stream_scripts = [
        call("web_search", {"query": "solar power news"}),
        [{"text": "I could not search right now."}],
    ]
    failed = events(await send(loop_world, account, "What is the latest news on solar power?"))
    assert states_of(failed, "Searching the web") == ["running", "failed"]
    detail = [s for s in progress(failed) if s["label"] == "Searching the web"][-1]["detail"]
    assert "search" in detail and "Answer from what you know" not in detail
    assert all_ended(failed) and names(failed)[-1] == "done"


async def test_a_notion_read_on_a_loop_turn_is_a_step(loop_world: World) -> None:
    phase2_wire(loop_world, repo_tools=RecordingNotionRepo(connected=True))
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        [{"call": {"name": "notion_list_tasks", "args": {"due": "today"}}}],
        [{"text": "You have one task today: Ship deck."}],
    ]
    frames = events(await send(loop_world, account, "What tasks are on my Notion board today?"))

    assert states_of(frames, "Reading Notion") == ["running", "done"]
    assert "Ship deck" not in json.dumps(progress(frames))  # no tool output in a step
    assert all_ended(frames)


async def test_an_image_on_a_loop_turn_is_an_image_step_then_a_saved_file(
    loop_world: World,
    image_client: RecordingImageClient,  # noqa: F811 (the fixture is imported above)
) -> None:
    phase2_wire(loop_world)
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("generate_image", {"prompt": "a red fox in the snow", "style_hint": "flat"}),
        [{"text": "Your fox image is in the chat."}],
    ]
    frames = events(await send(loop_world, account, "Draw a picture of a red fox in the snow"))

    steps = progress(frames)
    image = [(s["kind"], s["state"]) for s in steps if s["label"] == "Generating image"]
    assert image == [("image", "running"), ("image", "done")]
    assert [s["kind"] for s in steps if s["label"].startswith("Saved ")] == ["save"]
    assert "red fox" not in json.dumps([s for s in steps if s["kind"] == "image"])


async def test_an_email_draft_is_an_email_step_and_starts_its_approval_task(
    tmp_path: Path,
) -> None:
    async with make_world(
        artifacts_dir=tmp_path / "artifacts",
        chat_tool_loop=True,
        mail_address="owner@example.com",
        mail_app_password="fake-app-password-1234",
    ) as mail_world:
        phase2_wire(mail_world, submitter=IdempotentSubmitter())
        account = await sign_in(mail_world)
        mail_world.models.queued_stream_scripts = [
            call(
                "send_email",
                {"to": "rahul@example.com", "subject": "Moved", "body": "It moved to 5 pm."},
            ),
            [{"text": "The email is waiting for your approval."}],
        ]
        frames = events(
            await send(mail_world, account, "Send an email to rahul@example.com about the meeting")
        )

        email = [
            (s["kind"], s["state"]) for s in progress(frames) if s["label"] == "Drafting email"
        ]
        assert email == [("email", "running"), ("email", "done")]
        assert "rahul@example.com" not in json.dumps(progress(frames))
        assert "task.created" in names(frames) and all_ended(frames)


async def test_a_deck_made_in_chat_shows_render_and_save_steps(loop_world: World) -> None:
    wire(loop_world, submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("create_document", {"format": "pptx", "title": "SQL Joins", "spec": SQL_DECK}),
        [{"text": "Your SQL Joins deck is ready."}],
    ]
    frames = events(await send(loop_world, account, "Make a 6-slide PPT on SQL joins, dark theme"))

    steps = progress(frames)
    assert states_of(frames, "Creating document") == ["running", "done"]
    assert states_of(frames, "Rendering PPTX") == ["running", "done"]
    saved = [s["label"] for s in steps if s["kind"] == "save"]
    assert len(saved) == 1 and saved[0].endswith(".pptx")
    parent = next(s["step_id"] for s in steps if s["label"] == "Creating document")
    assert {s["parent_id"] for s in steps if s["label"] == "Rendering PPTX"} == {parent}
    assert all_ended(frames) and names(frames)[-1] == "done"


async def test_a_render_that_crashes_is_shown_failed_not_done(
    loop_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    def crash(**_: Any) -> Any:
        raise RuntimeError("renderer crashed")

    monkeypatch.setattr(chat_service, "build_document", crash)
    wire(loop_world, submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("create_document", {"format": "csv", "title": "Budget", "spec": {"text": CSV_GOOD}}),
        [{"text": "The file could not be made."}],
    ]
    frames = events(await send(loop_world, account, "Make a budget CSV with two rows"))

    assert states_of(frames, "Creating document") == ["running", "failed"]
    assert states_of(frames, "Rendering CSV") == ["running", "failed"]
    assert "renderer crashed" not in json.dumps(progress(frames))
    assert names(frames)[-1] == "done"


async def test_start_job_check_job_and_preview_are_steps(loop_world: World) -> None:
    job_id = uuid4()
    view = JobView(job_id=job_id, title="Solar book", status="in_progress", current_step="Ch 2")
    wire(loop_world, submitter=FixedJobSubmitter(job_id), job_reader=FakeJobs(view))
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("start_job", {"instruction": "Write a 300-page book on solar energy"}),
        [{"text": "The book is running in the background."}],
    ]
    first = events(await send(loop_world, account, "Write a 300-page book on solar energy"))
    assert states_of(first, "Starting background job") == ["running", "done"]
    assert "task.created" in names(first)
    session_id = next(f.data["session_id"] for f in first if f.event == "message.accepted")

    loop_world.models.queued_stream_scripts = [
        call("check_job", {"job_id": str(job_id)}),
        call("check_job", {"job_id": str(uuid4())}),  # not a job of this conversation
        [{"text": "It is still being written."}],
    ]
    second = events(
        await send(loop_world, account, "How is the book going?", session_id=session_id)
    )
    assert states_of(second, "Checking progress") == ["running", "done", "running", "failed"]
    assert all_ended(second) and names(second)[-1] == "done"


# ------------------------------------------------- narration notes between tool calls


def note_steps(frames: list[Any]) -> list[dict[str, Any]]:
    return [s for s in progress(frames) if s["kind"] == "note"]


async def test_text_before_a_tool_call_becomes_an_ordered_note_not_body_text(
    loop_world: World,
) -> None:
    search = FakeSearchGateway()
    phase2_wire(loop_world, search=search)
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        [
            {"text": "Let me search for that. "},
            {"call": {"name": "web_search", "args": {"query": "solar"}}},
        ],
        [{"text": "Solar output rose."}],
    ]
    frames = events(await send(loop_world, account, "What is the latest news on solar power?"))

    (note,) = note_steps(frames)
    assert note["note"] == "Let me search for that."
    assert note["state"] == "done" and note["text_len"] == len("Let me search for that. ")
    steps = progress(frames)
    labels = [s["label"] for s in steps]
    assert labels.index("Note") < labels.index("Searching the web")  # narration comes first
    # The live stream still carried the text as deltas (the client removes it using text_len).
    assert "Let me search for that." in "".join(
        f.data["text"] for f in frames if f.event == "delta"
    )
    # Stored body is the final text only: no duplication with the note.
    (reply,) = [m for m in loop_world.chat.messages if m.sender == "orchestrator"][-1:]
    # The model text is stored once; after a web search a clean **Sources** list built from the real
    # results is appended (app/services/citations.py), so the body starts with the model text.
    assert reply.content.strip().startswith("Solar output rose.")
    assert "Let me search for that." not in reply.content
    assert "**Sources**" in reply.content and "https://example.org/solar" in reply.content
    stored_notes = [s for s in reply.metadata["progress"] if s["kind"] == "note"]
    assert [s["note"] for s in stored_notes] == ["Let me search for that."]
    assert all("text_len" not in s for s in reply.metadata["progress"])


async def test_notes_are_replayed_in_order_on_a_repeated_key(loop_world: World) -> None:
    phase2_wire(loop_world, search=FakeSearchGateway())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        [{"text": "First I search."}, {"call": {"name": "web_search", "args": {"query": "a"}}}],
        [
            {"text": "Then I search again."},
            {"call": {"name": "web_search", "args": {"query": "b"}}},
        ],
        [{"text": "Done."}],
    ]
    key = new_key()
    first = events(
        await send(loop_world, account, "What is the latest news on solar power?", key=key)
    )
    replay = events(
        await send(loop_world, account, "What is the latest news on solar power?", key=key)
    )

    def order(frames: list[Any]) -> list[str]:
        return [
            s["note"] if s["kind"] == "note" else s["label"]
            for s in progress(frames)
            if s["state"] != "running" or s["kind"] == "note"
        ]

    assert [n["note"] for n in note_steps(first)] == ["First I search.", "Then I search again."]
    assert order(first) == order(replay)
    assert all(n.get("text_len") is None for n in note_steps(replay))
    # A search turn ends with the appended **Sources** list (app/services/citations.py).
    assert "".join(f.data["text"] for f in replay if f.event == "delta").strip().startswith("Done.")


async def test_blank_or_overlong_narration_is_not_a_note(loop_world: World) -> None:
    phase2_wire(loop_world, search=FakeSearchGateway())
    account = await sign_in(loop_world)
    long_text = "word " * 400  # well over the 1200 character limit
    loop_world.models.queued_stream_scripts = [
        [{"text": "   "}, {"call": {"name": "web_search", "args": {"query": "a"}}}],
        [{"text": long_text}, {"call": {"name": "web_search", "args": {"query": "b"}}}],
        [{"text": "Done."}],
    ]
    frames = events(await send(loop_world, account, "What is the latest news on solar power?"))
    assert note_steps(frames) == []
    body = "".join(f.data["text"] for f in frames if f.event == "delta")
    assert "word word" in body  # long text stays in the reply rather than being cut


async def test_a_secret_in_narration_is_scrubbed(loop_world: World) -> None:
    phase2_wire(loop_world, search=FakeSearchGateway())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        [
            {"text": "Using key sk-abcdefghijklmnop123 now."},
            {"call": {"name": "web_search", "args": {"query": "a"}}},
        ],
        [{"text": "Done."}],
    ]
    frames = events(await send(loop_world, account, "What is the latest news on solar power?"))
    (note,) = note_steps(frames)
    assert "sk-abcdefghijklmnop123" not in note["note"]


async def test_done_frame_carries_turns_elapsed_and_only_real_usage(loop_world: World) -> None:
    phase2_wire(loop_world, search=FakeSearchGateway())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("web_search", {"query": "a"}),
        [{"text": "Done."}],
    ]
    frames = events(await send(loop_world, account, "What is the latest news on solar power?"))
    usage = [f for f in frames if f.event == "done"][0].data["usage"]
    assert usage["turns"] == 2 and usage["elapsed_ms"] >= 0
    # The fake provider reports no cached tokens, so none is claimed.
    assert usage["cached_tokens"] == 0
    (reply,) = [m for m in loop_world.chat.messages if m.sender == "orchestrator"][-1:]
    assert reply.metadata["usage"]["turns"] == 2
