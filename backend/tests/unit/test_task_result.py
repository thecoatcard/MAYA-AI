"""The "what the task got" message: built only from real outputs, honest about partial results,
with a deterministic fallback, stored once per task and state. Fakes only; no model, no network."""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.services import task_result as tr
from app.services.task_result import (
    ArtifactView,
    ResultText,
    StepView,
    StoredResult,
    TaskResultService,
    TaskView,
    build_result,
    deterministic_result,
    status_of,
)

DEEP_REPORT = (
    "Read 12 sources from 9 sites (3 skipped).\n\n"
    "- Atomic Habits is the most recommended book [1].\n"
    "- The 7 Habits of Highly Effective People ranks second [2].\n\n"
    "## Sources\n1. Best books - example.org - 2026-01-01 - https://example.org/books"
)


def deep_step(**over: Any) -> StepView:
    base: dict[str, Any] = {
        "description": "Deep research: best book",
        "agent": "research",
        "status": "completed",
        "text": DEEP_REPORT,
        "proof": {"search_engine": "deep_research", "sources": ["https://example.org/books"]},
    }
    return StepView(**{**base, **over})


def view(status: str = "done", **over: Any) -> TaskView:
    base: dict[str, Any] = {
        "task_id": "t1",
        "title": "Find a book",
        "instruction": "find best book for self improvement",
        "status": status,
        "steps": (deep_step(),),
    }
    return TaskView(**{**base, **over})


class Gateway:
    def __init__(self, reply: Any = None, error: Exception | None = None) -> None:
        self.reply = reply
        self.error = error
        self.calls: list[dict[str, Any]] = []

    async def generate_structured(self, **kwargs: Any) -> Any:
        self.calls.append(kwargs)
        if self.error is not None:
            raise self.error
        return self.reply


def test_status_mapping() -> None:
    assert status_of("completed", {}) == "done"
    assert status_of("completed", {"partial": True}) == "partial"
    assert status_of("failed", {}) == "failed"
    assert status_of("cancelled", {}) == "cancelled"
    assert status_of("executing", {}) is None


def test_deep_research_uses_the_engine_report_as_the_body() -> None:
    result = deterministic_result(view())
    assert "Read 12 sources from 9 sites (3 skipped)." in result.markdown
    assert "Atomic Habits" in result.markdown
    assert "https://example.org/books" in result.markdown
    assert "Atomic Habits" in result.spoken and "http" not in result.spoken
    assert "full details are in the chat" in result.spoken


def test_deep_research_without_sources_says_so_and_offers_retry_never_done() -> None:
    step = deep_step(
        status="failed",
        text="",
        error="Deep research found no pages to read: 6 searches returned no usable results.",
        proof={"search_engine": "deep_research", "error": "no_results", "sources": []},
    )
    result = deterministic_result(view("failed", steps=(step,)))
    low = result.markdown.lower()
    assert "did not finish" in low and "no usable pages" in low
    assert "retry" in low and "general knowledge" in low
    assert "completed successfully" not in low and "is done" not in result.spoken.lower()


def test_rate_limit_reason_is_plain() -> None:
    step = StepView("search", "research", "failed", error="x", proof={"error": "rate_limited"})
    result = deterministic_result(view("failed", steps=(step,)))
    assert "rate limit" in result.markdown


def test_partial_is_never_called_done() -> None:
    result = deterministic_result(
        view(
            "partial",
            failed_steps=({"step": "File Step", "error": "Result may be incomplete: no sources"},),
        )
    )
    assert "finished with problems" in result.markdown
    assert "File Step" in result.markdown
    assert "is done" not in result.spoken and "not completely" in result.spoken


def test_document_result_lists_file_counts_outline_and_artifacts_hint() -> None:
    step = StepView(
        "Write report",
        "document",
        "completed",
        text="# Intro\ntext\n## Findings\nmore\n## Next steps\nx",
        proof={"report": {"pages": 12}, "file_name": "plan.pdf"},
    )
    result = deterministic_result(
        view("done", steps=(step,), artifacts=(ArtifactView("a1", "plan.pdf", "pdf"),))
    )
    assert "plan.pdf" in result.markdown and "12 pages" in result.markdown
    assert "Findings" in result.markdown and "The full file is in Artifacts." in result.markdown
    assert result.artifact_ids == ("a1",)
    assert "Artifacts" in result.spoken


def test_notion_write_shows_the_step_output_with_verification() -> None:
    step = StepView(
        "Save to Notion",
        "action",
        "completed",
        text="Created the Notion page My Plan. VERIFIED by reading it back.\nhttps://notion.so/p1",
        tool="notion_create_page",
    )
    result = deterministic_result(view("done", steps=(step,)))
    assert "VERIFIED" in result.markdown and "https://notion.so/p1" in result.markdown


def test_mail_is_sent_only_when_the_send_step_completed() -> None:
    ok = StepView("Mail", "action", "completed", tool="gmail_send", args={"to": "a@b.co"})
    bad = StepView(
        "Mail", "action", "failed", error="denied", tool="gmail_send", args={"to": "a@b.co"}
    )
    assert "Sent to a@b.co" in deterministic_result(view("done", steps=(ok,))).markdown
    failed = deterministic_result(view("failed", steps=(bad,))).markdown
    assert "NOT sent" in failed and "Sent to" not in failed


def test_chat_only_answer_is_the_message() -> None:
    step = StepView("Answer", "research", "completed", text="Short answer: use PostgreSQL.")
    result = deterministic_result(view("done", steps=(step,)))
    assert "Short answer: use PostgreSQL." in result.markdown


def test_cancelled() -> None:
    result = deterministic_result(view("cancelled", steps=()))
    assert "cancelled" in result.markdown.lower() and "cancelled" in result.spoken


async def test_model_text_is_used_but_scrubbed_of_invented_links_and_done_claims() -> None:
    reply = tr._ModelReply(
        message="The task is done. I have completed it.\n- Book: Atomic Habits\n"
        "See https://evil.example/x and https://example.org/books",
        spoken="All done. Atomic Habits leads. Read https://evil.example/x",
    )
    gateway = Gateway(reply)
    result = await build_result(
        view("partial", steps=(deep_step(text="Atomic Habits leads", proof={}),)), gateway
    )
    assert result.source == "model"
    assert "evil.example" not in result.markdown and "evil.example" not in result.spoken
    assert "I have completed it" not in result.markdown
    assert "Retry" in result.markdown
    call = gateway.calls[0]
    assert call["alias"] == "lite" and "Atomic Habits" in call["contents"][0]


async def test_deep_research_keeps_the_engine_report_even_with_a_model() -> None:
    reply = tr._ModelReply(message="invented table", spoken="Atomic Habits leads the list.")
    result = await build_result(view(), Gateway(reply))
    assert "invented table" not in result.markdown and "Read 12 sources" in result.markdown
    assert result.spoken == "Atomic Habits leads the list."


@pytest.mark.parametrize("error", [RuntimeError("budget"), TimeoutError()])
async def test_a_model_failure_falls_back_to_the_template(error: Exception) -> None:
    result = await build_result(view(), Gateway(error=error))
    assert result.source == "template" and "Atomic Habits" in result.markdown


async def test_no_gateway_is_the_template() -> None:
    assert (await build_result(view(), None)).source == "template"


def test_grounding_input_contains_only_task_outputs() -> None:
    facts = tr.facts_for_model(view())
    assert "STATUS: done" in facts and "Atomic Habits" in facts
    assert "READ: https://example.org/books" in facts


class Store:
    """In-memory loader and poster; the poster is idempotent per task and status."""

    def __init__(self, task_view: TaskView | None) -> None:
        self.view = task_view
        self.sid = uuid4()
        self.messages: dict[tuple[str, str], StoredResult] = {}
        self.posts = 0

    async def load(self, workspace_id: Any, task_id: Any) -> Any:
        return None if self.view is None else (self.view, uuid4(), self.sid)

    async def post(
        self, ws: Any, user: Any, session: Any, task: Any, status: str, result: Any
    ) -> Any:
        key = (str(task), status)
        if key in self.messages or result is None:
            return self.messages.get(key)
        self.posts += 1
        self.messages[key] = StoredResult(
            result.markdown, result.spoken, status, result.artifact_ids, "m1", True
        )
        return self.messages[key]


async def test_ensure_posts_once_per_task_and_state() -> None:
    store = Store(view())
    service = TaskResultService(loader=store.load, poster=store.post)
    ws, task = uuid4(), uuid4()
    first = await service.ensure(ws, task)
    second = await service.ensure(ws, task)
    assert first is not None and second is not None
    assert store.posts == 1 and first.spoken == second.spoken


async def test_a_new_terminal_state_gets_its_own_message() -> None:
    store = Store(view("failed", steps=()))
    service = TaskResultService(loader=store.load, poster=store.post)
    ws, task = uuid4(), uuid4()
    await service.ensure(ws, task)
    store.view = view("done")
    await service.ensure(ws, task)
    assert store.posts == 2


async def test_needs_attention_and_unknown_tasks_post_nothing() -> None:
    store = Store(view("needs_attention"))
    service = TaskResultService(loader=store.load, poster=store.post)
    assert await service.ensure(uuid4(), uuid4()) is None
    store.view = None
    assert await service.ensure(uuid4(), uuid4()) is None
    assert store.posts == 0


async def test_ensure_never_raises() -> None:
    async def boom(*_a: Any) -> Any:
        raise RuntimeError("db down")

    assert await TaskResultService(loader=boom, poster=boom).ensure(uuid4(), uuid4()) is None


def test_view_from_rows_maps_a_flagged_task_to_partial_and_missing_detail_to_attention() -> None:
    task = SimpleNamespace(
        id=uuid4(),
        title="t",
        user_instruction="i",
        instruction=None,
        status="completed",
        shared_context={"partial": True, "failed_steps": [{"step": "S", "error": "e"}]},
    )
    step = SimpleNamespace(
        description="d",
        assigned_agent="research",
        status="failed",
        output_result=None,
        error_message="I need a little more detail",
        verification_proof=None,
        input_data={},
        created_at="1",
    )
    built = tr._view_from_rows(task, [step], [])
    assert built is not None and built.status == "partial"
    task.status = "failed"
    built = tr._view_from_rows(task, [step], [])
    assert built is not None and built.status == "needs_attention"


def test_result_text_is_a_plain_dataclass() -> None:
    assert ResultText("m", "s", "done").artifact_ids == ()


async def test_the_runtime_posts_a_result_for_each_ended_task_once() -> None:
    import asyncio

    from app.tasks.runtime import TaskRuntime

    calls: list[Any] = []

    class Service:
        async def ensure(self, ws: Any, task: Any) -> None:
            calls.append(task)

    runtime = TaskRuntime.__new__(TaskRuntime)
    runtime._delayed = set()
    tr.set_current(Service())  # type: ignore[arg-type]
    try:
        task = uuid4()
        runtime._post_results(uuid4(), [task, task])
        await asyncio.sleep(0.01)
    finally:
        tr.set_current(None)
    assert calls == [task]
    runtime._post_results(uuid4(), [uuid4()])  # no service: silently nothing


def test_the_message_metadata_exposes_the_task_result_marker() -> None:
    from app.schemas.chat import MessageMetadataOut

    meta = MessageMetadataOut.model_validate(
        {
            "kind": "task_result",
            "task_id": "t1",
            "status": "partial",
            "artifact_ids": ["a1"],
            "spoken_summary": "never sent to clients",
        }
    )
    assert meta.task_result is not None
    assert (meta.task_result.task_id, meta.task_result.status) == ("t1", "partial")
    assert meta.task_result.artifact_ids == ["a1"]
    assert (
        MessageMetadataOut.model_validate({"kind": "task_result", "status": "x"}).task_result
        is None
    )
    assert MessageMetadataOut.model_validate({}).task_result is None
