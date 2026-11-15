"""A learning-book Notion step end to end, and a wedged task recovering, on real PostgreSQL.

Owner bug: the Notion step of a book task (no parent page named) paused to ask for the page and
wrote ``tasks.status = 'awaiting_input'``, a value the ``tasks.status`` CHECK constraint refuses.
The IntegrityError rolled the step back (still ``pending``), nothing was queued or failed, and the
task stayed ``executing`` forever with no card. These tests run the real worker and runtime as the
restricted ``maya_app`` role; only the Notion tool server is a recorder.
"""

from __future__ import annotations

import logging
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from uuid import UUID, uuid4

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.models import (
    ChatMessage,
    PlanStatus,
    Subtask,
    SubtaskAgent,
    SubtaskStatus,
    Task,
)
from app.db.tenant import tenant_session
from app.mcp.permissions import ToolPermissionRegistry
from app.services.task_submission import TaskSubmissionService
from app.tasks import needs_input as ni
from app.tasks.idempotency import TaskIdempotencyService
from app.tasks.queue import SubtaskJob, SubtaskQueue
from app.tasks.runtime import TaskRuntime
from app.tasks.worker import SubtaskWorker, resolve_subtask_uuid
from tests.postgres.test_approval_resume_postgres import _approve_over_http
from tests.support.postgres import SeededTenant
from tests.support.settings import JWT_KEY

pytestmark = pytest.mark.postgres

NOTION_TOOL = "notion_create_page_from_job"
PARENT_URL = "https://www.notion.so/Ai-NOTES-" + "ab12" * 8
BOOK = "# Java Streams\n\n## Chapter 1\n\nStreams are lazy.\n\n```java\nlist.stream();\n```\n"


class _Publisher:
    async def publish(self, event: Any) -> bool:
        return True


class _FakeNotion:
    """The tool server: records every call and pretends to create the page."""

    def __init__(self) -> None:
        self.calls: list[tuple[str, dict[str, Any], bool]] = []

    def has_tool(self, name: str) -> bool:
        return True

    @property
    def writes(self) -> list[tuple[str, dict[str, Any], bool]]:
        """The page creations (the read-only page search that fills the card is not one)."""
        return [call for call in self.calls if call[0] == NOTION_TOOL]

    async def invoke(self, name: str, args: dict[str, Any], **kwargs: Any) -> Any:
        self.calls.append((name, dict(args), bool(kwargs.get("has_valid_approval_token"))))
        return SimpleNamespace(
            clean_text="Created page https://notion.so/Java. VERIFIED by reading it back.",
            has_injection_suspicions=False,
        )


async def _no_agent(job: SubtaskJob, input_data: dict[str, Any]) -> dict[str, Any]:
    raise AssertionError("a tool step must not reach the agent handler")


def _runtime(engine: AsyncEngine, tmp_path: Path, notion: _FakeNotion) -> TaskRuntime:
    queue = SubtaskQueue()
    return TaskRuntime(
        engine=engine,
        queue=queue,
        publisher=_Publisher(),  # type: ignore[arg-type]
        worker=SubtaskWorker(
            queue=queue,
            permission_registry=ToolPermissionRegistry(),
            jwt_signing_key=JWT_KEY,
            tool_invoker=notion,
            fleet_support=SimpleNamespace(artifacts_dir=tmp_path),
        ),
        agent_handler=_no_agent,
    )


async def _seed_book_task(
    engine: AsyncEngine,
    tenant: SeededTenant,
    tmp_path: Path,
    *,
    parent: str = "",
    parent_title: str = "",
) -> tuple[UUID, UUID, UUID]:
    """The owner's shape: the Assemble step completed, the Notion step pending on it, task
    executing, the finished book on disk. Returns ``(task, assemble step, notion step)``."""
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        task, _ = await TaskIdempotencyService.get_or_create_task(
            session=session,
            workspace_id=tenant.workspace_id,
            user_id=tenant.user_id,
            idempotency_key=f"book-{uuid4().hex}",
            user_instruction="Write Java Streams study notes and save them to the Notion page",
        )
        task.status = "executing"
        task.plan_status = PlanStatus.EXECUTING
        assemble = Subtask(
            id=resolve_subtask_uuid(task.id, "assemble"),
            task_id=task.id,
            workspace_id=tenant.workspace_id,
            assigned_agent=SubtaskAgent.DOCUMENT,
            description="Assemble the Markdown book",
            dependencies=[],
            status=SubtaskStatus.COMPLETED,
            input_data={},
        )
        notion = Subtask(
            id=resolve_subtask_uuid(task.id, "notion_page"),
            task_id=task.id,
            workspace_id=tenant.workspace_id,
            assigned_agent=SubtaskAgent.PM,
            description="Create the Notion page from the finished book (needs your approval)",
            dependencies=[str(assemble.id)],
            status=SubtaskStatus.PENDING,
            input_data={
                "critical": True,
                "step_label": "Create the Notion page",
                "notion_book": {
                    "parent_page_id": parent,
                    "parent_title": parent_title,
                    "title": "Java Streams",
                },
                "tool": NOTION_TOOL,
                "tool_args": {},
            },
        )
        session.add_all([assemble, notion])
        await session.flush()
    book_dir = tmp_path / str(tenant.workspace_id) / str(task.id)
    book_dir.mkdir(parents=True)
    (book_dir / "java-streams.md").write_text(BOOK, encoding="utf-8")
    return task.id, assemble.id, notion.id


def _job(tenant: SeededTenant, task_id: UUID, subtask_id: UUID) -> SubtaskJob:
    return SubtaskJob(
        workspace_id=tenant.workspace_id,
        user_id=tenant.user_id,
        task_id=task_id,
        subtask_id=subtask_id,
        assigned_agent="pm",
    )


async def _state(
    engine: AsyncEngine, tenant: SeededTenant, task_id: UUID, subtask_id: UUID
) -> tuple[Task, Subtask, list[Any]]:
    async with tenant_session(engine, tenant.workspace_id, tenant.user_id) as session:
        task = (await session.execute(select(Task).where(Task.id == task_id))).scalar_one()
        sub = (await session.execute(select(Subtask).where(Subtask.id == subtask_id))).scalar_one()
        approvals = (
            await session.execute(
                text("SELECT id, status, tool_name FROM approval_requests WHERE task_id = :t"),
                {"t": task_id},
            )
        ).all()
        return task, sub, list(approvals)


async def _drain_delayed(runtime: TaskRuntime) -> None:
    for pending in list(runtime._delayed):
        await pending


@pytest.mark.asyncio
async def test_the_tasks_status_check_refuses_awaiting_input(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant], tmp_path: Path
) -> None:
    """The cause of the wedge: not a value the schema accepts (no migration may add it)."""
    tenant, _ = fresh_tenants
    task_id, _, _ = await _seed_book_task(app_engine, tenant, tmp_path)
    with pytest.raises(IntegrityError):
        async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
            await session.execute(
                text("UPDATE tasks SET status = 'awaiting_input' WHERE id = :t"), {"t": task_id}
            )
            await session.commit()


@pytest.mark.asyncio
async def test_book_task_without_a_parent_asks_on_a_card_then_creates_the_page_once_approved(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant], tmp_path: Path
) -> None:
    tenant, _ = fresh_tenants
    task_id, _, notion_id = await _seed_book_task(app_engine, tenant, tmp_path)
    notion = _FakeNotion()
    runtime = _runtime(app_engine, tmp_path, notion)

    # 1. the Notion step runs: it pauses on a card (no crash, nothing written yet)
    await runtime.process_job(_job(tenant, task_id, notion_id))
    task, step, approvals = await _state(app_engine, tenant, task_id, notion_id)
    assert step.status == SubtaskStatus.AWAITING_APPROVAL
    assert task.status == "awaiting_approval" and task.plan_status == PlanStatus.PAUSED
    assert ni.NEEDS_INPUT_KEY in step.input_data and approvals == []
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        cards = [
            m
            for m in (
                await session.execute(
                    select(ChatMessage).where(ChatMessage.session_id == task.session_id)
                )
            ).scalars()
            if (m.metadata_json or {}).get("input_request")
        ]
    assert len(cards) == 1 and cards[0].metadata_json["input_request"]["source"] == "task"

    # 2. the user pastes the page link on the card: the same step resumes
    service = TaskSubmissionService(engine=app_engine, gateway=None, queue=runtime.queue)
    reply = await service.resume_with_input(
        workspace_id=tenant.workspace_id,
        user_id=tenant.user_id,
        task_id=task_id,
        subtask_id=notion_id,
        answers=[{"selected": [], "text": PARENT_URL}],
    )
    assert "Continuing the task" in reply and runtime.queue.qsize() == 1
    resumed = await runtime.queue.dequeue(timeout_seconds=1.0)
    assert resumed is not None
    await runtime.process_job(resumed)

    # 3. exactly ONE approval request bound to the book; the step waits on the approval card
    task, step, approvals = await _state(app_engine, tenant, task_id, notion_id)
    assert [(a.status, a.tool_name) for a in approvals] == [("pending", NOTION_TOOL)]
    assert step.status == SubtaskStatus.AWAITING_APPROVAL and task.status == "awaiting_approval"
    assert step.input_data["approval_args"]["content_sha256"] and notion.writes == []

    # 4. approve over the real route: the step runs once and creates the page
    assert await _approve_over_http(app_engine, tenant, runtime.queue, approvals[0].id) == 200
    resumed = await runtime.queue.dequeue(timeout_seconds=1.0)
    assert resumed is not None
    await runtime.process_job(resumed)
    task, step, approvals = await _state(app_engine, tenant, task_id, notion_id)
    assert [(name, token) for name, _args, token in notion.writes] == [(NOTION_TOOL, True)]
    assert notion.writes[0][1]["parent_page_id"] and notion.writes[0][1]["content_sha256"]
    assert step.status == SubtaskStatus.COMPLETED and task.status == "completed"


@pytest.mark.asyncio
async def test_book_task_with_a_parent_goes_straight_to_the_approval_card(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant], tmp_path: Path
) -> None:
    tenant, _ = fresh_tenants
    task_id, _, notion_id = await _seed_book_task(app_engine, tenant, tmp_path, parent=PARENT_URL)
    runtime = _runtime(app_engine, tmp_path, _FakeNotion())
    await runtime.process_job(_job(tenant, task_id, notion_id))
    task, step, approvals = await _state(app_engine, tenant, task_id, notion_id)
    assert len(approvals) == 1 and approvals[0].status == "pending"
    assert step.status == SubtaskStatus.AWAITING_APPROVAL and task.status == "awaiting_approval"


class _CrashOnPause:
    """Re-creates the original defect: the pause path raises an IntegrityError."""

    def __init__(self, monkeypatch: pytest.MonkeyPatch) -> None:
        self.raised = 0

        async def boom(*args: Any, **kwargs: Any) -> Any:
            self.raised += 1
            raise IntegrityError("INSERT secret-statement", {"p": "secret-value"}, Exception("x"))

        monkeypatch.setattr(SubtaskWorker, "_pause_for_input", boom)


@pytest.mark.asyncio
async def test_a_crash_preparing_the_step_is_retried_once_then_fails_plainly_and_ends_the_task(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    tenant, _ = fresh_tenants
    task_id, _, notion_id = await _seed_book_task(app_engine, tenant, tmp_path)
    crash = _CrashOnPause(monkeypatch)
    runtime = _runtime(app_engine, tmp_path, _FakeNotion())
    job = _job(tenant, task_id, notion_id)
    caplog.set_level(logging.ERROR, logger="maya")

    # first crash: rolled back, nothing wedged, the step is queued again in a fresh session
    with pytest.raises(IntegrityError) as first:
        await runtime.process_job(job)
    await runtime._recover_failed_job(job, first.value)
    await _drain_delayed(runtime)
    assert runtime.queue.qsize() == 1
    _, step, _ = await _state(app_engine, tenant, task_id, notion_id)
    assert step.status == SubtaskStatus.PENDING

    # second crash: the step fails with a plain reason and the task ends, never "executing"
    retry = await runtime.queue.dequeue(timeout_seconds=1.0)
    assert retry is not None
    with pytest.raises(IntegrityError) as second:
        await runtime.process_job(retry)
    await runtime._recover_failed_job(retry, second.value)
    task, step, _ = await _state(app_engine, tenant, task_id, notion_id)
    assert crash.raised == 2 and runtime.queue.qsize() == 0
    assert step.status == SubtaskStatus.FAILED
    assert step.error_message is not None
    assert step.error_message.startswith("Could not prepare the Notion page:")
    assert "secret" not in step.error_message
    assert task.status == "completed", "the finished book is kept: completed with errors"
    assert task.shared_context["partial"] is True
    assert task.shared_context["failed_steps"][0]["error"].startswith("Could not prepare")


@pytest.mark.asyncio
async def test_the_worker_loop_logs_the_class_and_where_but_no_values(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    caplog: pytest.LogCaptureFixture,
) -> None:
    import asyncio

    tenant, _ = fresh_tenants
    task_id, _, notion_id = await _seed_book_task(app_engine, tenant, tmp_path)
    _CrashOnPause(monkeypatch)
    runtime = _runtime(app_engine, tmp_path, _FakeNotion())
    caplog.set_level(logging.ERROR, logger="maya")
    await runtime.queue.enqueue(_job(tenant, task_id, notion_id))
    loop = asyncio.create_task(runtime._worker_loop())
    try:
        for _ in range(100):
            if any("subtask_worker_loop_error" in r.getMessage() for r in caplog.records):
                break
            await asyncio.sleep(0.05)
    finally:
        loop.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loop
        await runtime.stop()
    line = next(r for r in caplog.records if "subtask_worker_loop_error" in r.getMessage())
    message = line.getMessage()
    assert "error=IntegrityError" in message and "worker.py:" in message and "boom" in message
    assert "secret" not in message and line.exc_info is None


@pytest.mark.asyncio
async def test_the_sweep_requeues_the_owners_wedged_task_and_stays_idempotent(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant], tmp_path: Path
) -> None:
    tenant, _ = fresh_tenants
    task_id, _, notion_id = await _seed_book_task(app_engine, tenant, tmp_path)
    runtime = _runtime(app_engine, tmp_path, _FakeNotion())

    # the wedge: executing task, the dependency completed, the step pending, nothing queued
    assert runtime.queue.qsize() == 0
    assert await runtime.sweep_stalled(workspace_ids=[tenant.workspace_id]) == 0, (
        "seen stalled once: confirmed on the next pass"
    )
    assert await runtime.sweep_stalled(workspace_ids=[tenant.workspace_id]) == 1
    queued = await runtime.queue.dequeue(timeout_seconds=1.0)
    assert queued is not None and queued.subtask_id == notion_id and queued.assigned_agent == "pm"
    await runtime.queue.enqueue(queued)
    assert await runtime.sweep_stalled(workspace_ids=[tenant.workspace_id]) == 0, (
        "a queued step is never doubled"
    )
    assert await runtime.sweep_stalled(workspace_ids=[tenant.workspace_id]) == 0

    # the recovered step then behaves: it pauses on the card instead of crashing
    again = await runtime.queue.dequeue(timeout_seconds=1.0)
    assert again is not None
    await runtime.process_job(again)
    task, step, _ = await _state(app_engine, tenant, task_id, notion_id)
    assert step.status == SubtaskStatus.AWAITING_APPROVAL and task.status == "awaiting_approval"
    assert await runtime.sweep_stalled(workspace_ids=[tenant.workspace_id]) == 0, (
        "a task waiting on a person is not swept"
    )


@pytest.mark.asyncio
async def test_a_restart_reconcile_also_picks_up_the_wedged_step(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant], tmp_path: Path
) -> None:
    tenant, _ = fresh_tenants
    _, _, notion_id = await _seed_book_task(app_engine, tenant, tmp_path)
    runtime = _runtime(app_engine, tmp_path, _FakeNotion())
    assert await runtime.reconcile() >= 1  # other tests' open tasks may share this database
    queued = []
    while runtime.queue.qsize():
        queued.append(await runtime.queue.dequeue(timeout_seconds=1.0))
    assert notion_id in {job.subtask_id for job in queued if job is not None}


@pytest.mark.asyncio
async def test_the_sweep_fails_an_orphaned_in_progress_step_into_the_retry_path(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant], tmp_path: Path
) -> None:
    tenant, _ = fresh_tenants
    task_id, _, notion_id = await _seed_book_task(app_engine, tenant, tmp_path)
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        step = (await session.execute(select(Subtask).where(Subtask.id == notion_id))).scalar_one()
        step.status = SubtaskStatus.IN_PROGRESS
        await session.commit()
    runtime = _runtime(app_engine, tmp_path, _FakeNotion())
    assert await runtime.sweep_stalled(workspace_ids=[tenant.workspace_id]) == 0
    assert await runtime.sweep_stalled(workspace_ids=[tenant.workspace_id]) == 1
    _, step, _ = await _state(app_engine, tenant, task_id, notion_id)
    assert step.status == SubtaskStatus.PENDING and step.retry_count == 1
    await _drain_delayed(runtime)
    assert runtime.queue.qsize() == 1


# ---------------------------------------------------------------- the owner's second bug
# A destination the user ALREADY chose on the gate card ("Destination: Ai NOTES") reached the Notion
# step only as text, so the step asked again; the second card was then buried by a later chat
# message and the task card could only look for an approval that did not exist.

AI_NOTES_ID = "40958955-61ca-80bf-98b4-d3110b2bdb64"
OTHER_ID = "11111111-2222-3333-4444-555555555555"


class _SearchNotion(_FakeNotion):
    """Also answers ``notion_search`` with the given page rows."""

    def __init__(self, rows: list[tuple[str, str]]) -> None:
        super().__init__()
        self.rows = rows

    async def invoke(self, name: str, args: dict[str, Any], **kwargs: Any) -> Any:
        if name == "notion_search":
            self.calls.append((name, dict(args), False))
            lines = [f"- {title} | id {page_id} | in a workspace" for title, page_id in self.rows]
            return SimpleNamespace(clean_text="\n".join(lines), has_injection_suspicions=False)
        return await super().invoke(name, args, **kwargs)


@pytest.mark.asyncio
async def test_a_chosen_destination_title_is_resolved_and_never_asked_again(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant], tmp_path: Path
) -> None:
    tenant, _ = fresh_tenants
    task_id, _, notion_id = await _seed_book_task(
        app_engine, tenant, tmp_path, parent_title="ai notes"
    )
    notion = _SearchNotion([("Ai NOTES", AI_NOTES_ID), ("Other", OTHER_ID)])
    runtime = _runtime(app_engine, tmp_path, notion)
    await runtime.process_job(_job(tenant, task_id, notion_id))
    _task, step, approvals = await _state(app_engine, tenant, task_id, notion_id)
    assert ni.NEEDS_INPUT_KEY not in step.input_data
    assert len(approvals) == 1 and approvals[0].status == "pending"
    assert step.input_data["approval_args"]["parent_page_id"].replace(
        "-", ""
    ) == AI_NOTES_ID.replace("-", "")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "rows",
    [
        [("Ai NOTES", AI_NOTES_ID), ("Ai NOTES", OTHER_ID)],  # several equal titles
        [("Something else", OTHER_ID)],  # no match
    ],
)
async def test_an_ambiguous_or_unknown_title_still_asks_once(
    app_engine: AsyncEngine,
    fresh_tenants: tuple[SeededTenant, SeededTenant],
    tmp_path: Path,
    rows: list[tuple[str, str]],
) -> None:
    tenant, _ = fresh_tenants
    task_id, _, notion_id = await _seed_book_task(
        app_engine, tenant, tmp_path, parent_title="Ai NOTES"
    )
    runtime = _runtime(app_engine, tmp_path, _SearchNotion(rows))
    await runtime.process_job(_job(tenant, task_id, notion_id))
    task, step, approvals = await _state(app_engine, tenant, task_id, notion_id)
    assert ni.NEEDS_INPUT_KEY in step.input_data and approvals == []
    assert task.status == "awaiting_approval"


@pytest.mark.asyncio
async def test_a_buried_question_card_is_posted_again_once_and_the_answer_resumes(
    app_engine: AsyncEngine, fresh_tenants: tuple[SeededTenant, SeededTenant], tmp_path: Path
) -> None:
    from app.tasks import input_cards

    tenant, _ = fresh_tenants
    task_id, _, notion_id = await _seed_book_task(app_engine, tenant, tmp_path)
    runtime = _runtime(app_engine, tmp_path, _SearchNotion([("Ai NOTES", AI_NOTES_ID)]))
    await runtime.process_job(_job(tenant, task_id, notion_id))
    task, step, _ = await _state(app_engine, tenant, task_id, notion_id)
    ask_id = step.input_data[ni.NEEDS_INPUT_KEY]["ask_id"]

    async def cards() -> list[Any]:
        async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
            rows = (
                await session.execute(
                    select(ChatMessage).where(ChatMessage.session_id == task.session_id)
                )
            ).scalars()
            return [m for m in rows if (m.metadata_json or {}).get("input_request")]

    # The card is still the open one: nothing is posted again.
    input_cards._REPOSTED.discard(ask_id)
    assert await runtime.repost_input_cards() == 0 and len(await cards()) == 1

    # A later user message buries it (the owner's wedged shape): it comes back once.
    async with tenant_session(app_engine, tenant.workspace_id, tenant.user_id) as session:
        await session.execute(
            text(
                "INSERT INTO messages (id, workspace_id, session_id, sender, content, modality, "
                "metadata) VALUES (:id, :w, :s, 'user', 'status?', 'text', '{}'::jsonb)"
            ),
            {"id": uuid4(), "w": tenant.workspace_id, "s": task.session_id},
        )
        await session.commit()
    assert await runtime.repost_input_cards() == 1
    assert await runtime.repost_input_cards() == 0
    again = await cards()
    assert len(again) == 2
    assert {c.metadata_json["input_request"]["ask_id"] for c in again} == {ask_id}

    # Answering from the task card (the API path) resumes the same step.
    service = TaskSubmissionService(engine=app_engine, gateway=None, queue=runtime.queue)
    reply = await service.resume_with_input(
        workspace_id=tenant.workspace_id,
        user_id=tenant.user_id,
        task_id=task_id,
        subtask_id=notion_id,
        answers=[{"selected": [], "text": PARENT_URL}],
    )
    assert "Continuing the task" in reply
