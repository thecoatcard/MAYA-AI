"""Chat tool loop through the chat endpoint (ADR-0026 Phase 1), flag MAYA_CHAT_TOOL_LOOP on.

Fakes only: the model is scripted, the task submitter and the job reader are fakes, and files go to
a temporary folder. The clock is the test ticker, so nothing sleeps.

Covered: the four tools are offered for a document request and the planner task is not used; a bad
CSV is returned as readable text, the model fixes it and the file card is stored with the reply;
start_job returns a job id and check_job reads its status in a later turn (the working state rides
in the assistant message metadata); the round and document-attempt caps; and the default (flag off)
path, which still submits a deck request directly.
"""

from collections.abc import AsyncIterator
from pathlib import Path
from typing import Annotated, Any
from uuid import UUID, uuid4

import pytest
from fastapi import Depends

from app.api.v1.chat import get_chat_service
from app.auth.dependencies import get_principal
from app.auth.models import Principal
from app.services import chat_service
from app.services.chat_service import MAX_DOCUMENT_ATTEMPTS, MAX_LOOP_ROUNDS, ChatService
from app.services.document_tools import (
    FORCED_FILE_FAILED_TEXT,
    FORCED_RETRY_INSTRUCTION,
    TOOL_LOOP_ADDENDUM,
    JobView,
)
from app.services.task_submission import TaskSubmission
from tests.integration.test_chat_inline_files import IdempotentSubmitter
from tests.integration.test_chat_memory import Prefs, tool_names
from tests.integration.test_chat_streaming import (
    World,
    events,
    make_world,
    names,
    send,
    sign_in,
    stream_calls,
)

CSV_BAD = "name,value\nrent,1\nfood\n"
CSV_GOOD = "name,value\nrent,1\nfood,2\n"
LOOP_TOOLS = ["create_document", "preview_document", "start_job", "check_job"]
# Phase 2 (ADR-0026) also offers generate_image on a loop turn when images are on, which the
# test world has by default. Web, Notion, GitHub, email and memory tools stay off here.
LOOP_TOOLS_WITH_IMAGE = [*LOOP_TOOLS, "generate_image"]


class FixedJobSubmitter(IdempotentSubmitter):
    """Like the real submitter, but the task it creates has the id the test expects."""

    def __init__(self, job_id: UUID) -> None:
        super().__init__()
        self.job_id = job_id

    async def submit(self, **kwargs: Any) -> TaskSubmission:
        self.submitted.append(kwargs)
        return TaskSubmission(self.job_id, kwargs["title"] or "Task", "queued", True)


class FakeJobs:
    """A job reader that returns one fixed view and records the job ids it was asked for."""

    def __init__(self, view: JobView) -> None:
        self.view = view
        self.asked: list[UUID] = []

    async def read(self, *, workspace_id: UUID, user_id: UUID, job_id: UUID) -> JobView | None:
        self.asked.append(job_id)
        return self.view if job_id == self.view.job_id else None


def wire(world: World, *, submitter: Any, job_reader: Any = None) -> None:
    """The chat endpoint's service with a task submitter and a job reader (the test world's)."""

    async def build(principal: Annotated[Principal, Depends(get_principal)]) -> ChatService:
        return ChatService(
            principal=principal,
            database=world.chat.database(),
            gateway=world.gateway,
            registry=world.registry,
            settings=world.settings,
            limiter=world.limiter,
            idempotency=world.store,
            stream_limiter=world.stream_limiter,
            clock=world.clock,
            monotonic=world.ticker,
            repositories=world.chat.repository,
            task_submitter=submitter,
            preferences=Prefs(memory_on=False),
            memory=None,
            job_reader=job_reader,
        )

    world.app.dependency_overrides[get_chat_service] = build


def call(name: str, args: dict[str, Any]) -> list[dict[str, Any]]:
    return [{"call": {"name": name, "args": args}}]


def frames_of(frames: list[Any], event: str) -> list[dict[str, Any]]:
    return [f.data for f in frames if f.event == event]


def system_text(record: dict[str, Any]) -> str:
    return str(record["config"].system_instruction)


def forced_mode(record: dict[str, Any]) -> tuple[str, list[str]] | None:
    """(mode, allowed names) of the function calling config of one streamed call, or None."""
    config = record["config"].tool_config
    if config is None:
        return None
    calling = config.function_calling_config
    return str(getattr(calling.mode, "value", calling.mode)), list(calling.allowed_function_names)


def deltas(frames: list[Any]) -> str:
    """The text the user would see: all delta events joined."""
    return "".join(f.data["text"] for f in frames if f.event == "delta")


@pytest.fixture
async def loop_world(tmp_path: Path) -> AsyncIterator[World]:
    async with make_world(artifacts_dir=tmp_path / "artifacts", chat_tool_loop=True) as value:
        yield value


async def test_a_deck_request_offers_the_four_tools_and_no_planner_task(
    loop_world: World,
) -> None:
    submitter = IdempotentSubmitter()
    wire(loop_world, submitter=submitter)
    account = await sign_in(loop_world)
    # A text-only first round is never shown: it is retried once, and then answered honestly.
    loop_world.models.queued_stream_scripts = [
        [{"text": "I can make that deck."}],
        [{"text": "Here is the deck text."}],
    ]
    frames = events(await send(loop_world, account, "Make a 20-slide deck on solar energy"))

    first, retry = stream_calls(loop_world)
    assert tool_names(first) == LOOP_TOOLS_WITH_IMAGE
    assert TOOL_LOOP_ADDENDUM in system_text(first)
    assert forced_mode(first) == ("ANY", ["create_document", "start_job"])
    assert submitter.submitted == [] and "task.created" not in names(frames)
    assert deltas(frames) == FORCED_FILE_FAILED_TEXT
    assert names(frames)[-1] == "done"


async def test_a_bad_csv_is_fixed_by_the_model_and_its_card_is_stored(
    loop_world: World, tmp_path: Path
) -> None:
    wire(loop_world, submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("create_document", {"format": "csv", "title": "Budget", "spec": {"text": CSV_BAD}}),
        call("create_document", {"format": "csv", "title": "Budget", "spec": {"text": CSV_GOOD}}),
        [{"text": "I made Budget.csv with two rows."}],
    ]
    frames = events(await send(loop_world, account, "Make a budget CSV with two rows"))

    statuses = [f.data["status"] for f in frames if f.event == "tool.finished"]
    assert statuses == ["error", "ok"]
    (card,) = frames_of(frames, "file.created")
    assert card["name"] == "Budget.csv" and card["file_type"] == "csv"
    assert card["action"] == "created"
    saved = [p for p in (tmp_path / "artifacts").rglob("Budget.csv")]
    assert len(saved) == 1 and saved[0].read_text(encoding="utf-8") == CSV_GOOD
    # The readable error reached the model in the next round, so it could repair the spec.
    assert "CSV row 3" in str(stream_calls(loop_world)[1]["contents"])
    assert names(frames)[-1] == "done"


async def test_start_job_returns_the_job_id_and_check_job_reads_status_next_turn(
    loop_world: World,
) -> None:
    job_id = uuid4()
    submitter = FixedJobSubmitter(job_id)
    jobs = FakeJobs(
        JobView(
            job_id=job_id,
            title="Solar book",
            status="in_progress",
            current_step="Writing chapter 2",
        )
    )
    wire(loop_world, submitter=submitter, job_reader=jobs)
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("start_job", {"instruction": "Write a 300-page book on solar energy"}),
        [{"text": "The book is running in the background."}],
    ]
    first = events(await send(loop_world, account, "Write a 300-page book on solar energy"))
    (task,) = frames_of(first, "task.created")
    assert task["task_id"] == str(job_id) and task["promoted"] is False
    session_id = frames_of(first, "message.accepted")[0]["session_id"]

    loop_world.models.queued_stream_scripts = [
        call("check_job", {"job_id": str(job_id)}),
        [{"text": "It is still being written."}],
    ]
    second = events(
        await send(loop_world, account, "How is the book going?", session_id=session_id)
    )
    assert jobs.asked == [job_id]
    statuses = [f.data for f in second if f.event == "tool.finished"]
    assert [s["name"] for s in statuses] == ["check_job"] and statuses[0]["status"] == "ok"
    # The job state was read back from the first reply's metadata and the status reached the model.
    turn_two = stream_calls(loop_world)[-2]
    assert "Working state" in system_text(turn_two) and str(job_id) in system_text(turn_two)
    assert "in_progress" in str(stream_calls(loop_world)[-1]["contents"])
    assert names(second)[-1] == "done"


async def test_the_loop_stops_offering_tools_after_its_round_and_attempt_caps(
    loop_world: World,
) -> None:
    wire(loop_world, submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    rounds = MAX_LOOP_ROUNDS
    bad = {"format": "csv", "title": "Budget", "spec": {"text": CSV_BAD}}
    loop_world.models.queued_stream_scripts = [
        *[call("create_document", bad) for _ in range(rounds)],
        [{"text": "I could not make the file; the CSV keeps failing."}],
    ]
    frames = events(await send(loop_world, account, "Make a budget CSV"))

    statuses = [f.data["status"] for f in frames if f.event == "tool.finished"]
    assert statuses == ["error"] * MAX_DOCUMENT_ATTEMPTS + ["skipped"] * (
        rounds - MAX_DOCUMENT_ATTEMPTS
    )
    calls = stream_calls(loop_world)
    assert len(calls) == rounds + 1
    assert tool_names(calls[0]) == LOOP_TOOLS_WITH_IMAGE  # tools while the budget lasts
    assert tool_names(calls[-1]) == []  # the last turn answers without tools
    assert names(frames)[-1] == "done"


async def test_the_token_budget_withdraws_the_tools_after_the_round_that_used_it(
    loop_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat_service, "LOOP_TOKEN_CAP", 1)  # any tool traffic exceeds it
    wire(loop_world, submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("create_document", {"format": "csv", "title": "Budget", "spec": {"text": CSV_BAD}}),
        [{"text": "The CSV had a problem; I have not saved it."}],
    ]
    frames = events(await send(loop_world, account, "Make a budget CSV"))

    assert [f.data["status"] for f in frames if f.event == "tool.finished"] == ["error"]
    calls = stream_calls(loop_world)
    assert tool_names(calls[0]) == LOOP_TOOLS_WITH_IMAGE and tool_names(calls[-1]) == []
    assert names(frames)[-1] == "done"


async def test_the_time_budget_refuses_tool_calls_once_it_has_passed(
    loop_world: World, monkeypatch: pytest.MonkeyPatch
) -> None:
    monkeypatch.setattr(chat_service, "LOOP_BUDGET_SECONDS", -1.0)  # already spent
    wire(loop_world, submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        call("create_document", {"format": "md", "title": "Notes", "spec": {"text": "# Hi\n"}}),
        [{"text": "I ran out of time to make the file."}],
    ]
    frames = events(await send(loop_world, account, "Make a notes.md file"))

    assert [f.data["status"] for f in frames if f.event == "tool.finished"] == ["skipped"]
    assert "file.created" not in names(frames)  # nothing was written after the budget ran out
    assert tool_names(stream_calls(loop_world)[-1]) == []
    assert names(frames)[-1] == "done"


async def test_with_the_flag_off_a_deck_request_is_submitted_directly_as_before(
    tmp_path: Path,
) -> None:
    async with make_world(artifacts_dir=tmp_path / "artifacts", chat_tool_loop=False) as legacy:
        submitter = IdempotentSubmitter()
        wire(legacy, submitter=submitter)
        account = await sign_in(legacy)
        legacy.models.queued_stream_scripts = [
            [{"text": "Started. Progress is in the Task Ledger."}]
        ]
        frames = events(await send(legacy, account, "Make a 20-slide deck on solar energy"))

    assert [s["instruction"] for s in submitter.submitted] == [
        "Make a 20-slide deck on solar energy"
    ]
    assert "task.created" in names(frames)
    (only,) = stream_calls(legacy)
    assert only["config"].tools is None  # the model is not offered any loop tool
    assert forced_mode(only) is None  # and no call is forced


SQL_DECK = {
    "design": {"base": "midnight"},
    "slides": [
        {"layout": "cover", "title": "SQL joins", "subtitle": "Combining tables"},
        {"layout": "bullets", "title": "What a join does", "bullets": ["Matches rows on a key"]},
        {"layout": "bullets", "title": "INNER JOIN", "bullets": ["Keeps matching rows only"]},
        {"layout": "bullets", "title": "LEFT JOIN", "bullets": ["Keeps every left row"]},
        {"layout": "bullets", "title": "RIGHT JOIN", "bullets": ["Keeps every right row"]},
        {"layout": "closing", "title": "Thank you"},
    ],
}


async def test_a_text_only_file_reply_is_retried_with_a_forced_call_and_never_shown(
    loop_world: World, tmp_path: Path
) -> None:
    wire(loop_world, submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        [{"text": "Slide 1: SQL joins. Slide 2: INNER JOIN keeps matches."}],
        call("create_document", {"format": "pptx", "title": "SQL Joins", "spec": SQL_DECK}),
        [{"text": "Your SQL Joins deck is ready."}],
    ]
    frames = events(await send(loop_world, account, "Make a 6-slide PPT on SQL joins, dark theme"))

    first, retry, follow_up = stream_calls(loop_world)
    assert forced_mode(first) == ("ANY", ["create_document", "start_job"])
    assert forced_mode(retry) == ("ANY", ["create_document", "start_job"])
    assert FORCED_RETRY_INSTRUCTION in system_text(retry)
    assert forced_mode(follow_up) is None  # only the first round of a file request is forced
    # The text-only round was dropped: the user sees the final line and the card, never the text.
    assert "Slide 1: SQL joins" not in deltas(frames)
    assert deltas(frames) == "Your SQL Joins deck is ready."
    (card,) = frames_of(frames, "file.created")
    assert card["name"].endswith(".pptx") and card["file_type"] == "pptx"
    assert names(frames)[-1] == "done"


async def test_a_plan_line_before_the_call_is_shown_and_the_file_is_made(
    loop_world: World,
) -> None:
    wire(loop_world, submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [
        [
            {"text": "Plan: a 6-slide dark deck. "},
            {
                "call": {
                    "name": "create_document",
                    "args": {"format": "pptx", "title": "SQL Joins", "spec": SQL_DECK},
                }
            },
        ],
        [{"text": "Your SQL Joins deck is ready."}],
    ]
    frames = events(await send(loop_world, account, "Make a 6-slide PPT on SQL joins, dark theme"))

    assert len(stream_calls(loop_world)) == 2  # no retry when the first round made the call
    assert deltas(frames).startswith("Plan: a 6-slide dark deck. ")
    assert deltas(frames).endswith("Your SQL Joins deck is ready.")
    assert len(frames_of(frames, "file.created")) == 1


async def test_the_file_turn_prompt_lists_only_real_controls_and_no_new_task_button(
    loop_world: World,
) -> None:
    wire(loop_world, submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [[{"text": "Plan."}], [{"text": "Plan again."}]]
    await send(loop_world, account, "Make a 6-slide PPT on SQL joins, dark theme")

    system = system_text(stream_calls(loop_world)[0])
    for control in (
        "the Download button on the file card",
        "the Artifacts page in the sidebar",
        "Settings > Connections",
        "Settings > Files (the working folder)",
        "the Tasks page",
    ):
        assert control in system
    assert "+ New Task" not in system and "Task Ledger" not in system
    assert "ONE short line" in system and "midnight" in system and "cover" in system


async def test_a_plain_question_gets_no_forced_call_and_no_file_tool(loop_world: World) -> None:
    wire(loop_world, submitter=IdempotentSubmitter())
    account = await sign_in(loop_world)
    loop_world.models.queued_stream_scripts = [[{"text": "A join combines rows from two tables."}]]
    frames = events(await send(loop_world, account, "What is a SQL join?"))

    (only,) = stream_calls(loop_world)
    assert forced_mode(only) is None
    assert "create_document" not in tool_names(only)
    assert deltas(frames) == "A join combines rows from two tables."
    assert "file.created" not in names(frames)
