"""Inline chat files (ADR-0025): the router decides the route, the model can make one small text
file inline (create_file_inline) or a new version of an attached text file (edit_attached_file),
a file is a card (file.created) stored with the reply, a long inline reply or a fifth file call is
moved to a background task (promoted), and the kill switch restores the previous behaviour.

Fakes only: no real Gemini, no network. The clock is the test ticker (no sleeping). Files go to a
temporary artifact folder, never to the repository's storage.
"""

from collections.abc import AsyncIterator, Iterator
from pathlib import Path
from typing import Any
from uuid import uuid4

import pytest

from app.files import services as file_services
from app.services.chat_service import PROMOTED_NOTICE, TASK_TOOL_INLINE
from app.services.context_manager import (
    CHAT_AGENT_ADDENDUM,
    CHAT_ANSWER_ADDENDUM,
    CHAT_APPROVAL_ADDENDUM,
    chat_system_instruction,
)
from app.services.task_submission import TaskSubmission
from tests.integration.test_chat_latency import FakeSubmitter, wire
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
from tests.support.files import RecordingServices, make_services

NOTES_BODY = "# SQL tips\n\n1. Use indexes on join keys.\n2. Avoid SELECT *.\n"


class IdempotentSubmitter(FakeSubmitter):
    """Like the real submitter: one task per idempotency key, whatever is submitted again."""

    def __init__(self) -> None:
        super().__init__()
        self.by_key: dict[str, TaskSubmission] = {}

    async def submit(self, **kwargs: Any) -> TaskSubmission:
        self.submitted.append(kwargs)
        key = kwargs["idempotency_key"]
        if key not in self.by_key:
            self.by_key[key] = TaskSubmission(uuid4(), kwargs["title"] or "Task", "queued", True)
        return self.by_key[key]


class RefusingSubmitter(FakeSubmitter):
    async def submit(self, **kwargs: Any) -> Any:
        self.submitted.append(kwargs)
        return {"ok": False, "reason": "No GitHub access.", "hint": "Connect GitHub first."}


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    async with make_world(artifacts_dir=tmp_path / "artifacts") as value:
        yield value


@pytest.fixture
def services(tmp_path: Path) -> Iterator[RecordingServices]:
    built, _settings, _folder = make_services(tmp_path)
    file_services.configure(built)
    yield built
    file_services.reset()


def call(name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"call": {"name": name, "args": args}}]


def card_frames(frames: list[Any]) -> list[dict[str, Any]]:
    return [f.data for f in frames if f.event == "file.created"]


def stored_files(world: World, tmp_path: Path) -> list[Path]:
    root = tmp_path / "artifacts"
    return sorted(p for p in root.rglob("*") if p.is_file()) if root.exists() else []


def system_text(call_record: dict[str, Any]) -> str:
    return str(call_record["config"].system_instruction)


# ---------------------------------------------------------------------- routing per route


async def test_a_question_gets_an_inline_answer_with_no_file_or_task_tool(world: World) -> None:
    wire(world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(world)
    world.models.queued_stream_scripts = [[{"text": "A mutex is a lock."}]]
    frames = events(await send(world, account, "What is a mutex?"))
    (only,) = stream_calls(world)
    assert tool_names(only) == [] and tool_names(only, with_ask=True) == ["ask_user"]
    assert CHAT_ANSWER_ADDENDUM in system_text(only)
    assert "task.created" not in names(frames) and "file.created" not in names(frames)


async def test_a_big_deliverable_is_submitted_directly_and_the_model_only_acknowledges(
    world: World,
) -> None:
    submitter = IdempotentSubmitter()
    wire(world, prefs=Prefs(memory_on=False), submitter=submitter)
    account = await sign_in(world)
    world.models.queued_stream_scripts = [[{"text": "Started. Progress is in the Task Ledger."}]]
    request = "Make a 10-slide deck on solar energy, dark theme"
    frames = events(await send(world, account, request))
    assert [s["instruction"] for s in submitter.submitted] == [request]
    assert "task.created" in names(frames) and names(frames)[-1] == "done"
    (task,) = [f.data for f in frames if f.event == "task.created"]
    assert task["promoted"] is False
    (only,) = stream_calls(world)
    assert only["config"].tools is None  # the model is not asked to call anything
    assert "has just been started" in system_text(only)


async def test_an_approval_request_offers_only_the_approval_gated_task(world: World) -> None:
    submitter = IdempotentSubmitter()
    wire(world, prefs=Prefs(memory_on=False), submitter=submitter)
    account = await sign_in(world)
    world.models.queued_stream_scripts = [[{"text": "I can prepare that for your approval."}]]
    frames = events(await send(world, account, "Send an email to rahul@example.com: meeting at 5"))
    assert submitter.submitted == [] and "task.created" not in names(frames)
    (only,) = stream_calls(world)
    # No Gmail on this world: an email request gets no task tool and is told Gmail is not connected.
    assert tool_names(only) == []
    assert CHAT_APPROVAL_ADDENDUM in system_text(only)
    assert "no mailbox is connected" in system_text(only)


async def test_a_refused_direct_task_is_explained_and_no_task_exists(world: World) -> None:
    submitter = RefusingSubmitter()
    wire(world, prefs=Prefs(memory_on=False), submitter=submitter)
    account = await sign_in(world)
    world.models.queued_stream_scripts = [[{"text": "I cannot do that yet."}]]
    frames = events(await send(world, account, "Research my GitHub repo and compile a report"))
    assert "task.created" not in names(frames) and names(frames)[-1] == "done"
    (only,) = stream_calls(world)
    assert "No task was started" in system_text(only) and "No GitHub access." in system_text(only)


async def test_create_an_agent_still_routes_to_the_draft_path(
    world: World, services: RecordingServices
) -> None:
    wire(world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(world)
    world.models.queued_stream_scripts = [[{"text": "Here is the draft."}]]
    frames = events(await send(world, account, "Create an agent that reviews my Python files"))
    (only,) = stream_calls(world)
    assert tool_names(only) == ["create_custom_agent"]
    assert CHAT_AGENT_ADDENDUM in system_text(only)
    assert "task.created" not in names(frames)


async def test_the_kill_switch_restores_the_previous_tools_and_prompt(tmp_path: Path) -> None:
    async with make_world(artifacts_dir=tmp_path / "a", chat_inline_files=False) as legacy:
        wire(legacy, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
        account = await sign_in(legacy)
        legacy.models.queued_stream_scripts = [[{"text": "Ok."}]]
        events(await send(legacy, account, "Create a notes.md with 10 SQL tips"))
        (only,) = stream_calls(legacy)
        assert tool_names(only) == ["submit_task"]
        assert system_text(only) == chat_system_instruction(tasks=True, memory=False)
        assert TASK_TOOL_INLINE.name == "submit_task"


# ------------------------------------------------------------------- inline file creation


async def test_create_file_inline_makes_a_card_stored_with_the_reply(
    world: World, tmp_path: Path
) -> None:
    submitter = IdempotentSubmitter()
    wire(world, prefs=Prefs(memory_on=False), submitter=submitter)
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        call("create_file_inline", {"name": "notes.md", "content": NOTES_BODY}),
        [{"text": "I created notes.md with ten SQL tips."}],
    ]
    frames = events(await send(world, account, "Create a notes.md with 10 key SQL tips"))

    assert submitter.submitted == []  # no task for a small text file
    assert names(frames).count("tool.started") == 1 and names(frames).count("tool.finished") == 1
    (finished,) = [f.data for f in frames if f.event == "tool.finished"]
    assert finished["name"] == "create_file_inline" and finished["status"] == "ok"
    (card,) = card_frames(frames)
    assert card["name"] == "notes.md" and card["action"] == "created"
    assert card["file_type"] == "md" and card["mime_type"].startswith("text/")
    assert card["size_bytes"] == len(NOTES_BODY.encode("utf-8"))
    assert names(frames)[-1] == "done"

    (saved,) = stored_files(world, tmp_path)
    assert saved.name == "notes.md" and saved.read_text(encoding="utf-8") == NOTES_BODY

    (assistant,) = world.chat.rows("orchestrator")
    assert assistant.metadata["files_created"][0]["file_id"] == card["file_id"]
    assert assistant.metadata["chat_files"][0]["storage_key"].endswith("notes.md")
    follow_up = stream_calls(world)[1]
    assert "create_file_inline" in tool_names(follow_up)  # a later round may still create one
    assert "submit_task" not in tool_names(follow_up)


async def test_the_file_card_is_replayed_with_the_same_idempotency_key(world: World) -> None:
    wire(world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        call("create_file_inline", {"name": "todo.txt", "content": "buy milk\n"}),
        [{"text": "Created todo.txt."}],
    ]
    key = uuid4().hex
    first = events(await send(world, account, "Ek todo.txt bana do", key=key))
    again = events(await send(world, account, "Ek todo.txt bana do", key=key))
    assert card_frames(first) == card_frames(again) and len(card_frames(again)) == 1
    assert len(stream_calls(world)) == 2  # the replay calls no model


async def test_a_file_tool_on_a_plain_answer_creates_nothing(world: World, tmp_path: Path) -> None:
    wire(world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        call("create_file_inline", {"name": "x.md", "content": "hi"}),
        [{"text": "Ok."}],
    ]
    frames = events(await send(world, account, "What is a mutex?"))
    # No file tool is offered for a plain answer, so the model's call is not even made.
    assert card_frames(frames) == [] and stored_files(world, tmp_path) == []
    assert all(f.data["status"] != "ok" for f in frames if f.event == "tool.finished")


async def test_an_oversized_file_is_moved_to_a_task_and_nothing_is_stored(
    world: World, tmp_path: Path
) -> None:
    # 200 KB is far above the inline token cap (about 36 KB of text), so the budget moves the
    # request to a background task before the file tool runs. Nothing is written inline.
    submitter = IdempotentSubmitter()
    wire(world, prefs=Prefs(memory_on=False), submitter=submitter)
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        call("create_file_inline", {"name": "big.txt", "content": "x" * (200 * 1024 + 1)}),
        [{"text": "That is being prepared in the background."}],
    ]
    frames = events(await send(world, account, "Make a big.txt with lots of text"))
    assert card_frames(frames) == [] and stored_files(world, tmp_path) == []
    (task,) = [f.data for f in frames if f.event == "task.created"]
    assert task["promoted"] is True and len(submitter.submitted) == 1


# -------------------------------------------------------------------- editing an attachment


async def test_edit_attached_file_saves_a_new_version_and_the_original_is_intact(
    world: World, services: RecordingServices
) -> None:
    wire(world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(world)
    upload = services.uploads.save(account.workspace_id, account.user_id, "notes.txt", b"teh cat")
    world.models.queued_stream_scripts = [
        call("edit_attached_file", {"upload_id": upload.id, "instruction": "fix typos"}),
        [{"text": "I saved a corrected copy next to the original."}],
    ]
    world.models.queued_text_responses = ["the cat"]  # the revised text (one model call)
    frames = events(
        await send(world, account, "In the attached notes.txt fix typos", attachments=[upload.id])
    )
    (card,) = card_frames(frames)
    assert card["action"] == "edited" and card["source_upload_id"] == upload.id
    assert card["name"] != "notes.txt"
    new_version = [p for p in services.artifacts_dir.rglob(card["name"]) if p.is_file()]
    assert [p.read_bytes() for p in new_version] == [b"the cat"]
    original = services.uploads.path_of(upload)
    assert original is not None and original.read_bytes() == b"teh cat"


async def test_an_edit_of_a_file_not_attached_to_this_message_is_refused(
    world: World, services: RecordingServices
) -> None:
    wire(world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(world)
    other = services.uploads.save(account.workspace_id, account.user_id, "a.txt", b"x")
    world.models.queued_stream_scripts = [
        call("edit_attached_file", {"upload_id": other.id, "instruction": "change it"}),
        [{"text": "I cannot edit that file here."}],
    ]
    frames = events(await send(world, account, "In the attached notes.txt fix typos"))
    assert card_frames(frames) == []
    (finished,) = [f.data for f in frames if f.event == "tool.finished"]
    assert finished["status"] == "error"


# ----------------------------------------------------------------- promotion (fake clock)


async def test_over_the_inline_budget_a_file_request_is_promoted_to_a_task(
    world: World, tmp_path: Path
) -> None:
    submitter = IdempotentSubmitter()
    wire(world, prefs=Prefs(memory_on=False), submitter=submitter)
    account = await sign_in(world)
    world.ticker.step = 30.0  # every clock read moves 30 s: the budget (25 s) is gone at once
    world.models.queued_stream_scripts = [
        call("create_file_inline", {"name": "notes.md", "content": NOTES_BODY}),
        [{"text": "ok"}],
    ]
    frames = events(await send(world, account, "Create a notes.md with 10 key SQL tips"))
    assert stored_files(world, tmp_path) == []  # nothing was written inline
    assert card_frames(frames) == []
    (task,) = [f.data for f in frames if f.event == "task.created"]
    assert task["promoted"] is True
    assert len(submitter.submitted) == 1
    assert submitter.submitted[0]["instruction"] == "Create a notes.md with 10 key SQL tips"
    (ack,) = stream_calls(world)[1:]
    assert PROMOTED_NOTICE in system_text(ack) and tool_names(ack) == []


async def test_a_retried_promoted_message_returns_the_same_task_and_calls_no_model(
    world: World,
) -> None:
    submitter = IdempotentSubmitter()
    wire(world, prefs=Prefs(memory_on=False), submitter=submitter)
    account = await sign_in(world)
    world.ticker.step = 30.0
    world.models.queued_stream_scripts = [
        call("create_file_inline", {"name": "notes.md", "content": NOTES_BODY}),
        [{"text": "ok"}],
    ]
    key = uuid4().hex
    first = events(await send(world, account, "Create a notes.md with 10 key SQL tips", key=key))
    calls_before = len(stream_calls(world))
    again = events(await send(world, account, "Create a notes.md with 10 key SQL tips", key=key))
    assert [f.data for f in first if f.event == "task.created"] == [
        f.data for f in again if f.event == "task.created"
    ]
    assert len(submitter.submitted) == 1 and len(stream_calls(world)) == calls_before


async def test_inline_file_rounds_are_capped_at_four(world: World, tmp_path: Path) -> None:
    submitter = IdempotentSubmitter()
    wire(world, prefs=Prefs(memory_on=False), submitter=submitter)
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        call("create_file_inline", {"name": f"part{i}.md", "content": f"part {i}\n"})
        for i in range(5)
    ] + [[{"text": "done"}]]
    frames = events(await send(world, account, "Make notes.md and five more text files of notes"))
    # Four rounds of files, as the limit allows. After the fourth round no tool is offered, so the
    # fifth scripted call is never made and no task is started.
    assert len(card_frames(frames)) == 4
    assert len(stored_files(world, tmp_path)) == 4
    assert submitter.submitted == [] and "task.created" not in names(frames)


async def test_a_long_model_routed_reply_past_the_budget_is_moved_to_the_background(
    world: World,
) -> None:
    submitter = IdempotentSubmitter()
    wire(world, prefs=Prefs(memory_on=False), submitter=submitter)
    account = await sign_in(world)
    world.ticker.step = 30.0
    world.models.queued_stream_scripts = [
        [{"text": "x" * 40_000}],  # about 13,000 estimated tokens: over the 12,000 cap
        [{"text": "never shown"}],
    ]
    # "as a text file" names a file: with no format word the checklist is a chat answer (owner
    # rule: no file unless asked), never model-routed and so never promoted.
    frames = events(
        await send(world, account, "Create a checklist for moving house as a text file")
    )
    (task,) = [f.data for f in frames if f.event == "task.created"]
    assert task["promoted"] is True and len(submitter.submitted) == 1
    (ack,) = stream_calls(world)[1:]  # the stream was cancelled; only the acknowledgement follows
    assert PROMOTED_NOTICE in system_text(ack)


# ----------------------------------------------------------------------- the download route


async def test_a_file_made_in_chat_downloads_as_an_attachment(
    world: World, tmp_path: Path, services: RecordingServices
) -> None:
    wire(world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        call("create_file_inline", {"name": "notes.md", "content": NOTES_BODY}),
        [{"text": "Created."}],
    ]
    frames = events(await send(world, account, "Create a notes.md with 10 key SQL tips"))
    session_id = frames[0].data["session_id"]
    message_id = frames[0].data["assistant_message_id"]
    (card,) = card_frames(frames)
    url = f"{CHAT}/sessions/{session_id}/messages/{message_id}/files/{card['file_id']}"

    response = await world.client.get(url, headers=account.headers)
    assert response.status_code == 200 and response.content == NOTES_BODY.encode("utf-8")
    assert response.headers["content-disposition"].startswith("attachment")
    assert response.headers["x-content-type-options"] == "nosniff"

    other = await world.client.get(
        f"{CHAT}/sessions/{uuid4()}/messages/{message_id}/files/{card['file_id']}",
        headers=account.headers,
    )
    unknown = await world.client.get(
        f"{CHAT}/sessions/{session_id}/messages/{message_id}/files/{uuid4()}",
        headers=account.headers,
    )
    assert other.status_code == 404 and unknown.status_code == 404


async def test_the_download_needs_a_signed_in_user(world: World) -> None:
    response = await world.client.get(
        f"{CHAT}/sessions/{uuid4()}/messages/{uuid4()}/files/{uuid4()}"
    )
    assert response.status_code == 401


async def test_a_chat_file_can_be_deleted_and_then_reads_as_deleted(
    world: World, tmp_path: Path, services: RecordingServices
) -> None:
    wire(world, prefs=Prefs(memory_on=False), submitter=IdempotentSubmitter())
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        call("create_file_inline", {"name": "notes.md", "content": NOTES_BODY}),
        [{"text": "Created."}],
    ]
    frames = events(await send(world, account, "Create a notes.md with 10 key SQL tips"))
    session_id = frames[0].data["session_id"]
    message_id = frames[0].data["assistant_message_id"]
    (card,) = card_frames(frames)
    url = f"{CHAT}/sessions/{session_id}/messages/{message_id}/files/{card['file_id']}"

    foreign = await world.client.delete(
        f"{CHAT}/sessions/{uuid4()}/messages/{message_id}/files/{card['file_id']}",
        headers=account.headers,
    )
    assert foreign.status_code == 404
    assert (await world.client.get(url, headers=account.headers)).status_code == 200

    removed = await world.client.delete(url, headers=account.headers)
    assert removed.status_code == 204
    gone = await world.client.get(url, headers=account.headers)
    assert gone.status_code == 410 and "file_deleted" in gone.text
    # Idempotent: a second delete is a plain 404 the UI treats as done.
    again = await world.client.delete(url, headers=account.headers)
    assert again.status_code == 404
