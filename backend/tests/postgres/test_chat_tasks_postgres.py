"""Chat that starts tasks (ADR-0016): function calling and the optional intent classifier.

Real PostgreSQL with row-level security (the ``maya_app`` role), real Redis, the real application
wiring; only the Gemini client is a fake that streams real SDK types. The subtask queue is the
in-memory one so the tests can see what is enqueued and when.
"""

from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text

from app.api.v1.chat import get_chat_preferences, get_task_submitter
from app.tasks.queue import SubtaskJob, SubtaskQueue
from tests.postgres import test_chat_api_postgres as base
from tests.postgres.test_chat_api_postgres import (
    Stack,
    events,
    model_calls,
    names,
    new_key,
    onboard,
    post,
    redis_url,
    rows,
    session_rows,
)
from tests.support.models import LITE
from tests.support.settings import make_settings

stack = base.stack  # the shared fixture: real PostgreSQL, real Redis, fake Gemini

pytestmark = [pytest.mark.postgres, pytest.mark.redis]


@pytest.fixture(autouse=True)
def tool_path_without_inline_router(stack: Stack) -> None:
    """These tests cover the function-calling tool and the optional classifier. The deterministic
    inline router (MAYA_CHAT_INLINE_FILES, on by default) would route prompts such as "research"
    to a task before the model is asked, so it is switched off here. Its own coverage lives in
    tests/integration/test_chat_inline_files.py."""
    stack.app.state.settings = make_settings(redis_url=redis_url(), chat_inline_files=False)


CALL = {"name": "submit_task", "args": {"instruction": "Research competitors", "title": "Rivals"}}


class WatchingQueue(SubtaskQueue):
    """Records, for every enqueue, whether the task row was already visible to other sessions."""

    def __init__(self, admin: Any) -> None:
        super().__init__()
        self._admin = admin
        self.visible_at_enqueue: list[bool] = []

    async def enqueue(self, job: SubtaskJob) -> None:
        async with self._admin.connect() as connection:
            found = (
                await connection.execute(
                    text("SELECT count(*) FROM subtasks WHERE id = :id"), {"id": job.subtask_id}
                )
            ).scalar_one()
        self.visible_at_enqueue.append(found == 1)
        await super().enqueue(job)


@pytest.fixture
async def queue(stack: Stack) -> AsyncIterator[WatchingQueue]:
    watching = WatchingQueue(stack.admin)
    stack.app.state.resources.subtask_queue = watching
    yield watching


class Prefs:
    def __init__(self, enabled: bool) -> None:
        self.enabled = enabled
        self.asked = 0

    async def intent_classifier_enabled(self, workspace_id: UUID, user_id: UUID) -> bool:
        self.asked += 1
        return self.enabled


def turn_on_classifier(stack: Stack, *, master: bool, user: bool) -> Prefs:
    stack.app.state.settings = make_settings(
        redis_url=redis_url(), chat_intent_classifier=master, chat_inline_files=False
    )
    prefs = Prefs(user)
    stack.app.dependency_overrides[get_chat_preferences] = lambda: prefs
    return prefs


async def count(stack: Stack, table: str, workspace_id: UUID, where: str = "TRUE") -> int:
    async with stack.admin.connect() as connection:
        result = await connection.execute(
            text(f"SELECT count(*) FROM {table} WHERE workspace_id = :w AND {where}"),  # noqa: S608
            {"w": workspace_id},
        )
        return int(result.scalar_one())


def completion_calls(stack: Stack) -> list[dict[str, Any]]:
    return [c for c in stack.fake.aio.models.calls if c["method"] == "generate_content"]


def stream_calls(stack: Stack) -> list[dict[str, Any]]:
    return [c for c in stack.fake.aio.models.calls if c["method"] == "generate_content_stream"]


# ------------------------------------------------------------------------------- the tool


async def test_a_function_call_creates_the_task_and_streams_a_confirmation(
    stack: Stack, queue: WatchingQueue
) -> None:
    alice = await onboard(stack)
    stack.models.queued_stream_scripts = [
        [{"text": "On it."}, {"call": CALL}],
        [{"text": "I started the research."}],
    ]

    frames = events(await post(stack, alice, "Research our competitors and write a report"))

    assert names(frames)[0] == "message.accepted"
    assert names(frames)[-1] == "done"
    assert names(frames).count("task.created") == 1
    created = next(f for f in frames if f.event == "task.created")
    # "promoted" is present on every task.created event; false unless an inline answer was moved
    assert set(created.data) == {"task_id", "title", "status", "promoted"}
    assert created.data["promoted"] is False
    assert created.data["title"] == "Rivals"
    assert created.data["status"] == "executing"
    # the task event comes before the confirmation text
    index = names(frames).index("task.created")
    assert names(frames)[index + 1 :].count("delta") >= 1

    # one task, one outbox event, one audit row, linked to the CURRENT chat session
    task_id = UUID(created.data["task_id"])
    assert await count(stack, "tasks", alice.workspace_id) == 1
    # One plan event; the task's progress steps are separate outbox rows (task_progress).
    assert (
        await count(
            stack,
            "task_outbox_events",
            alice.workspace_id,
            "task_id IS NOT NULL AND event_type <> 'task_progress'",
        )
        == 1
    )
    assert await count(stack, "audit_log", alice.workspace_id, "tool_name = 'task.submitted'") == 1
    chat_session = UUID(frames[0].data["session_id"])
    async with stack.admin.connect() as connection:
        task = (
            await connection.execute(
                text("SELECT session_id, user_id, instruction FROM tasks WHERE id = :id"),
                {"id": task_id},
            )
        ).one()
    assert task.session_id == chat_session
    assert task.user_id == alice.user_id
    assert task.instruction == "Research competitors"
    # no hidden, empty session was created for the task
    assert [row.id for row in await session_rows(stack, alice.workspace_id)] == [chat_session]

    # enqueue happened after the commit (the subtask row was visible to another connection)
    assert queue.qsize() >= 1
    assert queue.visible_at_enqueue and all(queue.visible_at_enqueue)

    # the model saw the tool, then the function response on one continuation turn without tools
    first, second = stream_calls(stack)
    # memory is on by default (ADR-0018), so its two tools are declared after submit_task
    assert [d.name for d in first["config"].tools[0].function_declarations] == [
        "submit_task",
        "save_memory",
        "forget_memory",
    ]
    assert second["config"].tools is None
    sent = second["contents"]
    assert sent[-1]["parts"][0]["function_response"]["response"]["status"] == "started"
    assert sent[-2]["parts"][-1]["function_call"]["name"] == "submit_task"

    # The narration before the tool call is stored once, as a note step in the work log (not
    # duplicated in the body); the body holds the confirmation. The usage covers both model calls.
    (assistant,) = await rows(stack, alice.workspace_id, "orchestrator")
    assert "I started the research." in assistant.content
    assert "On it." not in assistant.content
    notes = [s for s in assistant.metadata["progress"] if s.get("kind") == "note"]
    assert [s["note"] for s in notes] == ["On it."]
    assert assistant.metadata["task"] == created.data
    assert frames[-1].data["usage"]["prompt_tokens"] == 200  # two streams of 100 prompt tokens


async def test_a_simple_question_starts_no_task(stack: Stack, queue: WatchingQueue) -> None:
    alice = await onboard(stack)
    frames = events(await post(stack, alice, "What is the capital of France?"))
    assert names(frames) == ["message.accepted", *["delta"] * 4, "done"]
    assert await count(stack, "tasks", alice.workspace_id) == 0
    assert queue.qsize() == 0
    (call,) = stream_calls(stack)
    assert call["config"].tools is not None  # the tool is offered; the model chose not to use it


async def test_only_one_task_is_started_per_message(stack: Stack, queue: WatchingQueue) -> None:
    alice = await onboard(stack)
    other = {"name": "submit_task", "args": {"instruction": "A second thing"}}
    stack.models.queued_stream_scripts = [
        [{"call": CALL}, {"call": other}],
        [{"text": "Started one."}],
    ]
    frames = events(await post(stack, alice, "Do two big things"))
    assert names(frames).count("task.created") == 1
    assert await count(stack, "tasks", alice.workspace_id) == 1
    responses = stream_calls(stack)[1]["contents"][-1]["parts"]
    assert [p["function_response"]["response"]["status"] for p in responses] == [
        "started",
        "skipped",
    ]


async def test_a_failed_submission_tells_the_model_nothing_was_started(
    stack: Stack, queue: WatchingQueue
) -> None:
    class Failing:
        async def submit(self, **_: Any) -> Any:
            raise RuntimeError("database exploded")

    alice = await onboard(stack)
    stack.app.dependency_overrides[get_task_submitter] = lambda: Failing()
    stack.models.queued_stream_scripts = [[{"call": CALL}], [{"text": "Sorry, nothing started."}]]
    frames = events(await post(stack, alice, "Research competitors"))
    assert "task.created" not in names(frames)
    assert names(frames)[-1] == "done"
    response = stream_calls(stack)[1]["contents"][-1]["parts"][0]["function_response"]["response"]
    assert response["status"] == "error"
    assert await count(stack, "tasks", alice.workspace_id) == 0


async def test_a_retried_message_does_not_create_a_second_task(
    stack: Stack, queue: WatchingQueue
) -> None:
    alice = await onboard(stack)
    stack.models.queued_stream_scripts = [[{"call": CALL}], [{"text": "Started."}]]
    key = new_key()
    first = events(await post(stack, alice, "Research competitors", key=key))
    second = events(await post(stack, alice, "Research competitors", key=key))

    assert second[0].data["replayed"] is True
    assert model_calls(stack) == 2  # the replay called no model
    assert await count(stack, "tasks", alice.workspace_id) == 1
    replayed = next(f for f in second if f.event == "task.created")
    assert replayed.data == next(f for f in first if f.event == "task.created").data
    assert names(second) == ["message.accepted", "task.created", "delta", "done"]
    assert replayed.data["promoted"] is False


async def test_the_submission_service_is_idempotent_per_key(stack: Stack) -> None:
    from app.services.task_submission import TaskSubmissionService, chat_task_idempotency_key

    alice = await onboard(stack)
    service = TaskSubmissionService(
        engine=stack.app.state.resources.engine, gateway=None, queue=None
    )
    key = chat_task_idempotency_key(alice.workspace_id, alice.user_id, UUID(int=7))
    args: dict[str, Any] = {
        "workspace_id": alice.workspace_id,
        "user_id": alice.user_id,
        "idempotency_key": key,
        "instruction": "Do it",
        "title": None,
        "session_id": None,
    }
    one = await service.submit(**args)
    two = await service.submit(**args)
    assert (one.is_new, two.is_new) == (True, False)
    assert one.task_id == two.task_id
    assert await count(stack, "tasks", alice.workspace_id) == 1


async def test_tasks_started_in_chat_are_invisible_to_another_workspace(
    stack: Stack, queue: WatchingQueue
) -> None:
    alice = await onboard(stack)
    bob = await onboard(stack)
    stack.models.queued_stream_scripts = [[{"call": CALL}], [{"text": "Started."}]]
    created = next(
        f
        for f in events(await post(stack, alice, "Research competitors"))
        if f.event == "task.created"
    )
    mine = await stack.client.get(f"/api/v1/tasks/{created.data['task_id']}", headers=alice.headers)
    theirs = await stack.client.get(f"/api/v1/tasks/{created.data['task_id']}", headers=bob.headers)
    assert mine.status_code == 200
    assert theirs.status_code == 404


# ------------------------------------------------------------------------ the classifier


@pytest.mark.parametrize(
    ("master", "user"),
    [(False, False), (False, True), (True, False)],
    ids=["off", "master-off", "user-off"],
)
async def test_the_classifier_makes_no_model_call_unless_both_allow_it(
    stack: Stack, queue: WatchingQueue, master: bool, user: bool
) -> None:
    alice = await onboard(stack)
    turn_on_classifier(stack, master=master, user=user)
    frames = events(await post(stack, alice, "Research our competitors"))
    assert names(frames) == ["message.accepted", *["delta"] * 4, "done"]
    assert completion_calls(stack) == []  # no classifier call, no planner call
    assert model_calls(stack) == 1
    assert await count(stack, "tasks", alice.workspace_id) == 0


async def test_the_classifier_is_off_by_default_and_never_reads_the_preference(
    stack: Stack, queue: WatchingQueue
) -> None:
    alice = await onboard(stack)
    prefs = Prefs(True)
    stack.app.dependency_overrides[get_chat_preferences] = lambda: prefs  # master switch stays off
    events(await post(stack, alice, "Research our competitors"))
    assert prefs.asked == 0
    assert completion_calls(stack) == []


async def test_with_both_switches_on_a_work_request_becomes_a_task_without_the_tool(
    stack: Stack, queue: WatchingQueue
) -> None:
    alice = await onboard(stack)
    turn_on_classifier(stack, master=True, user=True)
    stack.models.queued_text_responses = ['{"work_request": true, "title": "Competitor report"}']
    stack.models.queued_stream_scripts = [[{"text": "I started the competitor report."}]]

    frames = events(await post(stack, alice, "Research our competitors and write a report"))

    assert names(frames)[:2] == ["message.accepted", "task.created"]
    assert names(frames)[-1] == "done"
    assert frames[1].data["title"] == "Competitor report"
    assert await count(stack, "tasks", alice.workspace_id) == 1
    assert [row.id for row in await session_rows(stack, alice.workspace_id)] == [
        UUID(frames[0].data["session_id"])
    ]
    classifier = completion_calls(stack)[0]
    assert classifier["model"] == LITE  # the lite alias
    (stream,) = stream_calls(stack)
    assert stream["config"].tools is None  # the acknowledgement does not offer the tool
    assert "Competitor report" in stream["config"].system_instruction
    assert queue.qsize() >= 1


async def test_with_both_switches_on_a_plain_question_stays_a_plain_answer(
    stack: Stack, queue: WatchingQueue
) -> None:
    alice = await onboard(stack)
    turn_on_classifier(stack, master=True, user=True)
    stack.models.queued_text_responses = ['{"work_request": false, "title": ""}']
    frames = events(await post(stack, alice, "What is a mutex?"))
    assert names(frames) == ["message.accepted", *["delta"] * 4, "done"]
    assert len(completion_calls(stack)) == 1  # only the classifier
    assert await count(stack, "tasks", alice.workspace_id) == 0


async def test_a_failing_classifier_never_fails_the_message(
    stack: Stack, queue: WatchingQueue
) -> None:
    alice = await onboard(stack)
    turn_on_classifier(stack, master=True, user=True)
    stack.models.queued_text_responses = ["not json", "still not json"]
    frames = events(await post(stack, alice, "Research competitors"))
    assert names(frames)[-1] == "done"
    assert await count(stack, "tasks", alice.workspace_id) == 0


# --------------------------------------------------------------------------- compatibility


async def test_a_reply_without_a_task_keeps_the_old_event_contract(stack: Stack) -> None:
    alice = await onboard(stack)
    frames = events(await post(stack, alice, "Hello MAYA"))
    assert names(frames) == ["message.accepted", *["delta"] * 4, "done"]
    assert set(frames[0].data) == {
        "session_id",
        "user_message_id",
        "assistant_message_id",
        "replayed",
    }
    (assistant,) = await rows(stack, alice.workspace_id, "orchestrator")
    assert "task" not in assistant.metadata
