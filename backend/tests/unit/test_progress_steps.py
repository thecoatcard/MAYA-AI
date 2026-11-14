"""Progress steps: the event model, the recorder, the task step writer and the worker wrapper.

No database and no model: the outbox session is a fake that records what would be written.
"""

from __future__ import annotations

import json
from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.db.models import SubtaskStatus
from app.schemas.chat import ProgressEvent, encode_sse
from app.services.progress import (
    DETAIL_MAX,
    ID_MAX,
    LABEL_MAX,
    MAX_PENDING,
    MAX_STEPS,
    THOUGHT_MAX,
    ProgressRecorder,
    ProgressStep,
    can_transition,
    clean_text,
    parse_steps,
    tool_step,
)
from app.tasks.events import TaskEventType
from app.tasks.progress_steps import agent_label, emit_task_step, subtask_step_id
from app.tasks.queue import SubtaskJob
from app.tasks.worker import SubtaskWorker
from app.voice.event_bridge import _should_speak, format_spoken_task_update

RUN = "run-1"


def step(**overrides: Any) -> dict[str, Any]:
    base: dict[str, Any] = {
        "run_id": RUN,
        "step_id": "s1",
        "kind": "tool",
        "label": "Searching the web",
        "state": "running",
    }
    base.update(overrides)
    return base


# ----------------------------------------------------------------------------- the model


def test_a_valid_step_keeps_its_fields() -> None:
    model = ProgressStep(**step(detail="2 sources", index=4, total=10, elapsed_ms=12))
    assert model.detail == "2 sources" and model.index == 4 and model.total == 10
    assert model.parent_id is None and model.thought is None


def test_labels_and_details_are_capped_and_single_line() -> None:
    model = ProgressStep(**step(label="x" * 500 + "\n\nmore", detail="y" * 900))
    assert len(model.label) <= LABEL_MAX and "\n" not in model.label
    assert len(model.detail or "") <= DETAIL_MAX
    assert model.label.endswith("…") and (model.detail or "").endswith("…")


def test_an_empty_label_is_refused() -> None:
    with pytest.raises(ValidationError):
        ProgressStep(**step(label="   \n\t "))


@pytest.mark.parametrize("kind", ["plan", "think", "tool", "step", "render", "check", "repair"])
def test_every_documented_kind_is_accepted(kind: str) -> None:
    thought = "A short summary." if kind == "think" else None
    assert ProgressStep(**step(kind=kind, thought=thought)).kind == kind


@pytest.mark.parametrize("kind", ["save", "search", "email", "image", "voice"])
def test_the_remaining_documented_kinds_are_accepted(kind: str) -> None:
    assert ProgressStep(**step(kind=kind)).kind == kind


def test_unknown_kind_or_state_is_refused() -> None:
    with pytest.raises(ValidationError):
        ProgressStep(**step(kind="reasoning"))
    with pytest.raises(ValidationError):
        ProgressStep(**step(state="pending"))


def test_thought_is_only_allowed_on_a_think_step() -> None:
    with pytest.raises(ValidationError):
        ProgressStep(**step(kind="tool", thought="not allowed here"))


def test_thought_is_capped_at_its_own_limit() -> None:
    model = ProgressStep(**step(kind="think", label="Reasoning", thought="w " * 2000))
    assert model.thought is not None and len(model.thought) <= THOUGHT_MAX


def test_index_must_not_exceed_total_and_must_be_positive() -> None:
    with pytest.raises(ValidationError):
        ProgressStep(**step(index=5, total=4))
    with pytest.raises(ValidationError):
        ProgressStep(**step(index=0))


def test_unknown_fields_are_refused() -> None:
    with pytest.raises(ValidationError):
        ProgressStep(**step(raw_prompt="never allowed"))


def test_secret_shaped_text_is_redacted() -> None:
    text = (
        "key sk-abcdef1234567890XYZ and AIzaSyD-1234567890abcdefghijklmnop "
        "Bearer abc.def-123 token=hunter2 password: swordfish "
        "0123456789abcdef0123456789abcdef"
    )
    cleaned = clean_text(text, 500)
    for secret in (
        "sk-abcdef",
        "AIzaSy",
        "abc.def",
        "hunter2",
        "swordfish",
        "0123456789abcdef0123",
    ):
        assert secret not in cleaned
    assert "[redacted]" in cleaned


def test_control_characters_never_reach_the_label() -> None:
    model = ProgressStep(**step(label="Saved\x00\x1b[31m report\x7f.pdf"))
    assert "\x00" not in model.label and "\x1b" not in model.label and "\x7f" not in model.label


def test_identifiers_are_capped() -> None:
    with pytest.raises(ValidationError):
        ProgressStep(**step(step_id="x" * (ID_MAX + 1)))


def test_the_sse_event_carries_exactly_the_step_fields() -> None:
    event = ProgressEvent.model_validate(ProgressStep(**step(detail="ok")).model_dump())
    frame = encode_sse(3, "progress", event)
    assert frame.startswith("id: 3\nevent: progress\ndata: ")
    data = json.loads(frame.split("data: ", 1)[1].strip())
    assert set(data) == set(ProgressStep.model_fields)
    assert data["state"] == "running" and data["detail"] == "ok"


def test_parse_steps_skips_malformed_entries_and_never_raises() -> None:
    raw = [step(), {"kind": "nope"}, "not a dict", step(step_id="s2")]
    parsed = parse_steps(raw)
    assert [s.step_id for s in parsed] == ["s1", "s2"]
    assert parse_steps("garbage") == [] and parse_steps(None) == []


# ----------------------------------------------------------------------- transitions


def test_running_moves_once_to_a_final_state() -> None:
    assert can_transition("running", "done")
    assert can_transition("running", "failed")
    assert can_transition("running", "skipped")
    assert not can_transition("done", "failed")
    assert not can_transition("failed", "running")
    assert not can_transition("skipped", "done")
    assert not can_transition("running", "pending")


def test_the_recorder_emits_running_then_done_and_refuses_a_second_finish() -> None:
    now = [100.0]
    recorder = ProgressRecorder("run-x", clock=lambda: now[0])
    sid = recorder.start("tool", "Searching the web")
    assert sid is not None
    now[0] = 100.25
    assert recorder.finish(sid, "done", detail="3 sources") is True
    assert recorder.finish(sid, "failed") is False  # terminal states never change

    events = recorder.drain()
    assert [e.state for e in events] == ["running", "done"]
    assert events[1].elapsed_ms == 250 and events[1].detail == "3 sources"
    assert recorder.drain() == []  # draining empties the queue


def test_a_failed_step_is_reported_as_failed() -> None:
    recorder = ProgressRecorder("run-x")
    sid = recorder.start("render", "Rendering PDF")
    assert sid is not None
    recorder.finish(sid, "failed", detail="layout error")
    assert [e.state for e in recorder.drain()] == ["running", "failed"]


def test_close_open_finishes_only_steps_still_running() -> None:
    recorder = ProgressRecorder("run-x")
    done = recorder.start("tool", "Done already", state="done")
    open_id = recorder.start("tool", "Still going")
    assert open_id is not None
    closed = recorder.close_open("failed", detail="stopped")
    assert closed == [open_id] and done is not None
    states = {s.step_id: s.state for s in recorder.snapshot()}
    assert states[open_id] == "failed" and states[done] == "done"


def test_a_one_shot_note_starts_in_its_final_state() -> None:
    recorder = ProgressRecorder("run-x")
    recorder.start("think", "Reasoning", state="skipped", detail="No summary came back.")
    (event,) = recorder.drain()
    assert event.state == "skipped" and event.detail == "No summary came back."


def test_the_queue_is_bounded_and_counts_what_it_drops() -> None:
    recorder = ProgressRecorder("run-x", max_pending=3)
    for n in range(10):
        recorder.start("step", f"Step {n}", state="done")
    events = recorder.drain()
    assert len(events) == 3 and recorder.dropped == 7
    assert [e.label for e in events] == ["Step 7", "Step 8", "Step 9"]


def test_the_number_of_steps_per_run_is_capped() -> None:
    recorder = ProgressRecorder("run-x", max_steps=2)
    assert recorder.start("step", "one", state="done") is not None
    assert recorder.start("step", "two", state="done") is not None
    assert recorder.start("step", "three", state="done") is None
    assert len(recorder.snapshot()) == 2 and recorder.dropped == 1


def test_an_invalid_step_is_refused_without_raising() -> None:
    recorder = ProgressRecorder("run-x")
    assert recorder.start("not-a-kind", "x") is None
    assert recorder.start("step", "   ") is None
    assert recorder.rejected == 2 and recorder.drain() == []


def test_thought_fragments_are_coalesced_and_capped() -> None:
    now = [0.0]
    recorder = ProgressRecorder("run-x", clock=lambda: now[0])
    sid = recorder.start("think", "Reasoning")
    assert sid is not None
    for piece in ["I will check ", "the file ", "first."]:
        recorder.append_thought(sid, piece)  # within the interval: no event per fragment
    now[0] = 1.0
    recorder.append_thought(sid, " Then answer.")  # past the interval: one more event
    events = recorder.drain()
    assert [e.state for e in events] == ["running", "running"]
    assert events[-1].thought == "I will check the file first. Then answer."
    for _ in range(200):
        recorder.append_thought(sid, "word " * 20)
    final = recorder.snapshot()[0]
    assert final.thought is not None and len(final.thought) <= THOUGHT_MAX


def test_append_thought_is_ignored_on_a_non_think_step() -> None:
    recorder = ProgressRecorder("run-x")
    sid = recorder.start("tool", "Searching")
    assert sid is not None
    recorder.append_thought(sid, "should not appear")
    assert recorder.snapshot()[0].thought is None


def test_snapshot_dicts_are_json_ready_and_omit_empty_fields() -> None:
    recorder = ProgressRecorder("run-x")
    recorder.start("plan", "Planning the reply", state="done", detail="document")
    (item,) = recorder.snapshot_dicts()
    assert "thought" not in item and "parent_id" not in item and "index" not in item
    assert json.dumps(item)  # serialisable


def test_tool_names_map_to_kinds_and_unknown_tools_are_generic() -> None:
    assert tool_step("web_search") == ("search", "Searching the web")
    assert tool_step("create_document")[0] == "render"
    assert tool_step("send_email")[0] == "email"
    assert tool_step("generate_image")[0] == "image"
    assert tool_step("something_new") == ("tool", "Using a tool")


def test_the_module_constants_match_the_documented_contract() -> None:
    assert (LABEL_MAX, DETAIL_MAX, THOUGHT_MAX) == (120, 280, 6000)
    assert MAX_STEPS == 200 and MAX_PENDING == 200


# ---------------------------------------------------------- task steps (fake outbox)


class _FakeSession:
    """Records what the outbox would write. ``fail_flush`` simulates a failing write."""

    def __init__(self, *, fail_flush: bool = False) -> None:
        self.added: list[Any] = []
        self.fail_flush = fail_flush
        self.rolled_back = False

    async def rollback(self) -> None:
        self.rolled_back = True

    def add(self, row: Any) -> None:
        self.added.append(row)

    async def flush(self) -> None:
        if self.fail_flush:
            raise RuntimeError("database went away")

    @asynccontextmanager
    async def begin_nested(self):  # type: ignore[no-untyped-def]
        yield self


def _payload_steps(session: _FakeSession) -> list[dict[str, Any]]:
    return [row.payload["step"] for row in session.added]


async def test_a_task_step_is_queued_as_a_task_progress_outbox_event() -> None:
    session = _FakeSession()
    task_id, workspace_id = uuid4(), uuid4()
    await emit_task_step(
        session,  # type: ignore[arg-type]
        workspace_id=workspace_id,
        task_id=task_id,
        subtask_id=None,
        step_id="plan-1",
        kind="plan",
        label="Planned the work",
        state="done",
        detail="3 steps",
    )
    (row,) = session.added
    assert row.event_type == TaskEventType.TASK_PROGRESS.value
    assert row.payload["task_id"] == str(task_id) and row.payload["subtask_id"] is None
    assert row.payload["step"]["run_id"] == str(task_id)
    assert row.payload["step"]["detail"] == "3 steps"


async def test_a_failing_step_write_is_dropped_and_never_raises() -> None:
    session = _FakeSession(fail_flush=True)
    await emit_task_step(
        session,  # type: ignore[arg-type]
        workspace_id=uuid4(),
        task_id=uuid4(),
        subtask_id=None,
        step_id="s",
        kind="step",
        label="Working",
        state="running",
    )  # no exception: the work goes on


async def test_an_invalid_step_is_dropped_and_never_raises() -> None:
    session = _FakeSession()
    await emit_task_step(
        session,  # type: ignore[arg-type]
        workspace_id=uuid4(),
        task_id=uuid4(),
        subtask_id=None,
        step_id="s",
        kind="not-a-kind",
        label="x",
        state="running",
    )
    assert session.added == []


def test_agent_labels_are_short_and_human() -> None:
    assert agent_label("research") == "Research agent"
    assert agent_label("doc_writer") == "Doc writer agent"
    assert agent_label(None) == "Task agent"
    assert agent_label("x" * 300).endswith("agent") and len(agent_label("x" * 300)) <= LABEL_MAX


def _job() -> SubtaskJob:
    return SubtaskJob(
        workspace_id=uuid4(),
        user_id=uuid4(),
        task_id=uuid4(),
        subtask_id=uuid4(),
        assigned_agent="research",
    )


def _worker_with(execute: AsyncMock) -> SubtaskWorker:
    worker = SubtaskWorker.__new__(SubtaskWorker)
    worker._execute_job = execute  # type: ignore[method-assign]
    return worker


async def test_a_worker_step_emits_running_then_done() -> None:
    job = _job()
    session = _FakeSession()
    worker = _worker_with(AsyncMock(return_value=SubtaskStatus.COMPLETED))

    status = await worker.execute_job(session, job)  # type: ignore[arg-type]

    assert status == SubtaskStatus.COMPLETED
    steps = _payload_steps(session)
    assert [s["state"] for s in steps] == ["running", "done"]
    assert {s["step_id"] for s in steps} == {subtask_step_id(job.subtask_id)}
    assert steps[0]["label"] == "Research agent" and steps[0]["kind"] == "step"


async def test_a_failed_subtask_emits_failed_and_keeps_its_status() -> None:
    session = _FakeSession()
    worker = _worker_with(AsyncMock(return_value=SubtaskStatus.FAILED))
    status = await worker.execute_job(session, _job())  # type: ignore[arg-type]
    assert status == SubtaskStatus.FAILED
    assert [s["state"] for s in _payload_steps(session)] == ["running", "failed"]


async def test_an_error_still_reaches_the_caller_after_a_failed_step() -> None:
    session = _FakeSession()
    worker = _worker_with(AsyncMock(side_effect=RuntimeError("provider returned 500")))
    with pytest.raises(RuntimeError, match="provider returned 500"):
        await worker.execute_job(session, _job())  # type: ignore[arg-type]
    # The worker rolls the session back and re-raises; the runtime's recovery writes the
    # failed step in a fresh session (so a poisoned session cannot wedge the task).
    assert session.rolled_back is True
    assert [s["state"] for s in _payload_steps(session)] == ["running"]


async def test_a_step_write_failure_does_not_change_the_subtask_result() -> None:
    session = _FakeSession(fail_flush=True)
    worker = _worker_with(AsyncMock(return_value=SubtaskStatus.COMPLETED))
    status = await worker.execute_job(session, _job())  # type: ignore[arg-type]
    assert status == SubtaskStatus.COMPLETED


async def test_an_approval_pause_is_shown_as_skipped_with_its_reason() -> None:
    session = _FakeSession()
    worker = _worker_with(AsyncMock(return_value=SubtaskStatus.AWAITING_APPROVAL))
    await worker.execute_job(session, _job())  # type: ignore[arg-type]
    last = _payload_steps(session)[-1]
    assert last["state"] == "skipped" and last["detail"] == "Waiting for your approval."


# ------------------------------------------------------------------- voice and events


def test_task_progress_events_are_never_spoken() -> None:
    assert TaskEventType.TASK_PROGRESS.value == "task_progress"
    assert _should_speak(TaskEventType.TASK_PROGRESS.value, {"state": "done"}) is False


def test_only_a_finished_milestone_step_is_spoken_in_one_sentence() -> None:
    done = {"step": {"state": "done", "kind": "render", "label": "Rendering PDF"}}
    assert _should_speak("task_progress", done) is True
    assert format_spoken_task_update("task_progress", done) == "Finished: Rendering PDF."

    running = {"step": {"state": "running", "kind": "render", "label": "Rendering PDF"}}
    other_kind = {"step": {"state": "done", "kind": "step", "label": "Planned the work"}}
    failed = {"step": {"state": "failed", "kind": "email", "label": "Drafting email"}}
    for payload in (running, other_kind, failed, {}):
        assert _should_speak("task_progress", payload) is False


@pytest.mark.asyncio
async def test_a_task_step_reaches_the_voice_client_as_a_silent_task_event_frame() -> None:
    from app.voice import protocol
    from app.voice.event_bridge import VoiceEventBridge

    task_id = str(uuid4())
    step_payload = {
        "run_id": task_id,
        "step_id": "sub-1",
        "kind": "step",
        "label": "Research agent",
        "state": "running",
    }
    bridge = VoiceEventBridge(uuid4())
    bridge.track_task(task_id)
    await bridge.handle_event(
        {"event_type": "task_progress", "payload": {"task_id": task_id, "step": step_payload}}
    )
    announcement = await bridge.get_next_announcement()
    assert announcement.speak is False
    frame = protocol.task_event(announcement.task_id, announcement.event_type, announcement.payload)
    assert frame["type"] == "TASK_EVENT"
    assert frame["event_type"] == "task_progress"
    assert frame["step"] == step_payload


def test_a_task_event_frame_without_a_step_has_no_step_key() -> None:
    from app.voice import protocol

    frame = protocol.task_event("t", "task_completed", {"status": "completed"})
    assert "step" not in frame


def test_note_step_needs_text_only_on_kind_note_and_is_capped_and_scrubbed() -> None:
    from app.services.progress import NOTE_MAX, ProgressRecorder

    rec = ProgressRecorder("r")
    assert rec.start("note", "Note") is None  # a note without text is refused
    assert rec.start("tool", "Using a tool", note="x") is None  # text on another kind is refused
    sid = rec.start("note", "Note", note="a  b\nsk-abcdefghijklmnop123 " + "z" * 2000, state="done")
    assert sid is not None
    (step,) = rec.snapshot()
    assert step.note is not None and len(step.note) <= NOTE_MAX
    assert "sk-abcdefghijklmnop123" not in step.note and "\n" not in step.note


def test_text_len_is_live_only_and_never_persisted() -> None:
    from app.services.progress import ProgressRecorder

    rec = ProgressRecorder("r")
    rec.start("note", "Note", note="hi", text_len=3, state="done")
    assert rec.drain()[0].text_len == 3
    assert "text_len" not in rec.snapshot_dicts()[0]


def test_a_thinking_summary_keeps_its_paragraphs_and_still_scrubs() -> None:
    """The reasoning summary is Markdown: line breaks survive (at most one blank line) so
    headings and paragraphs render; other text fields stay single-line."""
    from app.services.progress import ProgressStep, clean_text

    raw = "**Plan**\r\n\r\n\r\n\r\nDo the thing.   \nSecond   line\x07 here."
    kept = clean_text(raw, 500, keep_newlines=True)
    assert kept == "**Plan**\n\nDo the thing.\nSecond line here."
    assert "\n" not in clean_text(raw, 500)
    step = ProgressStep(
        run_id="r1", step_id="t1", kind="think", state="running", label="Thinking", thought=raw
    )
    assert step.thought == "**Plan**\n\nDo the thing.\nSecond line here."
    note = ProgressStep(run_id="r1", step_id="n1", kind="step", state="done", label="x", detail=raw)
    assert note.detail is not None and "\n" not in note.detail
