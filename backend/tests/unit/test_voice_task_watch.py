"""Voice task watch: milestones are spoken once, queued behind speech, never for progress."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.tasks.events import TaskEventType
from app.voice.event_bridge import VoiceEventBridge
from app.voice.task_watch import (
    AnnouncedStore,
    Milestone,
    TaskAnnouncer,
    TaskFacts,
    catch_up_text,
    milestone_for_event,
    milestone_for_status,
)
from app.voice.tools import VoiceApprovalProhibitedError, VoiceExecutionContext, VoiceToolRegistry

WS = uuid4()


class Rig:
    def __init__(self, files: tuple[str, ...] = ()) -> None:
        self.user = uuid4()
        self.owner = self.user
        self.files = files
        self.busy = False
        self.spoken: list[str] = []
        self.frames: list[dict[str, Any]] = []
        self.store = AnnouncedStore()
        self.announcer = self.make_announcer(lambda: self.busy)

    def make_announcer(self, is_busy: Any) -> TaskAnnouncer:
        return TaskAnnouncer(
            workspace_id=WS,
            user_id=self.user,
            resolver=self.resolve,
            store=self.store,
            speak=self.speak,
            is_busy=is_busy,
            notify=self.notify,
            poll_seconds=0.002,
            gap_seconds=0.0,
            settle_seconds=0.01,
        )

    async def resolve(self, ws: UUID, user: UUID, task_id: str) -> TaskFacts | None:
        return TaskFacts(task_id, self.owner, "Solar deck", "completed", self.files)

    async def speak(self, text: str) -> None:
        self.spoken.append(text)

    async def notify(self, frame: dict[str, Any]) -> None:
        self.frames.append(frame)

    async def wait_spoken(self, count: int) -> None:
        for _ in range(400):
            if len(self.spoken) >= count:
                return
            await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_completed_names_the_file_and_hides_ids() -> None:
    rig = Rig(files=("solar.pptx",))
    rig.announcer.start()
    await rig.announcer.on_event("task_completed", {"task_id": "t1"})
    await rig.wait_spoken(1)
    await rig.announcer.close()
    assert "Solar deck" in rig.spoken[0] and "solar.pptx" in rig.spoken[0]
    assert "t1" not in rig.spoken[0]
    assert rig.frames[0]["type"] == "TASK_UPDATE_SPOKEN"
    assert rig.frames[0]["milestone"] == "completed"


@pytest.mark.asyncio
async def test_a_partly_failed_task_is_announced_as_finished_with_a_failed_step() -> None:
    rig = Rig(files=("notes.pdf",))
    rig.announcer.start()
    payload = {
        "task_id": "t1",
        "partial": True,
        "failed_steps": [
            {
                "step": "Chapter 3: Loops",
                "error": "model call failed key=AIzaSyA1234567890abcdefghijklmnop",
            }
        ],
    }
    await rig.announcer.on_event("task_completed", payload)
    await rig.wait_spoken(1)
    await rig.announcer.close()
    text = rig.spoken[0]
    assert "finished, but not completely" in text and "Chapter 3: Loops failed" in text
    assert "AIzaSy" not in text and "simply done" in text and "is ready" not in text


@pytest.mark.asyncio
@pytest.mark.parametrize(
    ("event", "payload", "needle"),
    [
        (
            "task_failed",
            {"message": "Rate limit hit key=AIzaSyA1234567890abcdefghijklmnop"},
            "failed",
        ),
        ("task_cancelled", {}, "cancelled"),
        ("task_paused_approval", {"tool_name": "send_email"}, "Approve button"),
        (
            "task_failed",
            {"message": "I need a little more detail before writing this: which audience?"},
            "which audience",
        ),
    ],
)
async def test_each_attention_state_is_spoken(
    event: str, payload: dict[str, Any], needle: str
) -> None:
    rig = Rig()
    rig.announcer.start()
    await rig.announcer.on_event(event, {"task_id": "t1", "subtask_id": "s1", **payload})
    await rig.wait_spoken(1)
    await rig.announcer.close()
    assert needle in rig.spoken[0]
    assert "AIzaSy" not in rig.spoken[0]


@pytest.mark.asyncio
async def test_progress_plan_and_step_events_are_never_spoken() -> None:
    rig = Rig()
    for event in (
        "task_progress",
        "task_plan_created",
        "subtask_dispatched",
        "subtask_status_changed",
    ):
        await rig.announcer.on_event(event, {"task_id": "t1", "status": "completed"})
    assert rig.announcer.queued == 0 and rig.spoken == []


@pytest.mark.asyncio
async def test_duplicate_events_and_reconnects_announce_once() -> None:
    rig = Rig()
    rig.announcer.start()
    await rig.announcer.on_event("task_completed", {"task_id": "t1"})
    await rig.announcer.on_event("task_completed", {"task_id": "t1"})
    await rig.wait_spoken(1)
    await rig.announcer.close()
    # A new connection (new announcer, same store) sees the same event again after a reconnect.
    again = rig.make_announcer(lambda: False)
    await again.on_event("task_completed", {"task_id": "t1"})
    assert len(rig.spoken) == 1 and again.queued == 0


@pytest.mark.asyncio
async def test_nothing_is_spoken_while_busy_then_updates_are_coalesced() -> None:
    rig = Rig()
    rig.busy = True
    rig.announcer.start()
    await rig.announcer.on_event("task_completed", {"task_id": "t1"})
    await rig.announcer.on_event("task_cancelled", {"task_id": "t2"})
    await asyncio.sleep(0.1)
    assert rig.spoken == []
    rig.busy = False
    await rig.wait_spoken(1)
    await rig.announcer.close()
    assert len(rig.spoken) == 1
    assert "done" in rig.spoken[0] and "cancelled" in rig.spoken[0]


@pytest.mark.asyncio
async def test_another_users_task_is_not_announced() -> None:
    rig = Rig()
    rig.owner = uuid4()
    await rig.announcer.on_event("task_completed", {"task_id": "t1"})
    assert rig.announcer.queued == 0


@pytest.mark.asyncio
async def test_unspoken_items_are_released_when_voice_ends() -> None:
    rig = Rig()
    rig.busy = True
    rig.announcer.start()
    await rig.announcer.on_event("task_completed", {"task_id": "t1"})
    await rig.announcer.close()  # voice turned off before it could be spoken
    assert rig.spoken == []
    assert await rig.store.claim(WS, rig.user, "t1:completed:") is True


@pytest.mark.asyncio
async def test_catch_up_is_one_sentence_and_the_queue_clears() -> None:
    rig = Rig()
    done = TaskFacts("t1", rig.user, "Solar deck", "completed")
    waiting = TaskFacts("t2", rig.user, "Budget", "awaiting_approval")
    rig.announcer.add_catch_up(
        [(Milestone.COMPLETED, done), (Milestone.APPROVAL, waiting)],
        ["t1:completed:", "t2:approval:"],
    )
    rig.announcer.start()
    await rig.wait_spoken(1)
    await rig.announcer.close()
    assert rig.spoken[0].startswith("While voice was off")
    assert rig.spoken[0].count(".") == 1
    assert rig.frames[0]["catch_up"] is True
    assert rig.announcer.queued == 0
    assert catch_up_text([]) == ""


def test_event_and_status_mapping() -> None:
    assert milestone_for_event("task_completed", {}) is Milestone.COMPLETED
    assert milestone_for_event("awaiting_approval", {}) is Milestone.APPROVAL
    assert milestone_for_event("task_progress", {"step": {"state": "done"}}) is None
    assert milestone_for_status("canceled") is Milestone.CANCELLED
    assert milestone_for_status("executing") is None


@pytest.mark.asyncio
async def test_bridge_hands_milestones_to_the_watcher_and_stays_silent_itself() -> None:
    rig = Rig()
    bridge = VoiceEventBridge(workspace_id=WS)
    bridge.watcher = rig.announcer
    bridge.track_task("t1")
    await bridge.handle_event(
        {"event_type": TaskEventType.TASK_COMPLETED.value, "payload": {"task_id": "t1"}}
    )
    assert rig.announcer.queued == 1
    announcement = await bridge.get_next_announcement()
    assert announcement.speak is False  # the announcer speaks it, once


@pytest.mark.asyncio
async def test_a_spoken_yes_cannot_approve_anything() -> None:
    registry = VoiceToolRegistry()
    ctx = VoiceExecutionContext(workspace_id=WS, user_id=uuid4(), session_id="s")
    for name in ("approve_action", "confirm_approval", "approve_task"):
        with pytest.raises(VoiceApprovalProhibitedError):
            await registry.execute_tool_async(name, {"approval_id": "x"}, ctx)
    rig = Rig()
    await rig.announcer.on_event("task_paused_approval", {"task_id": "t1", "subtask_id": "s"})
    assert "cannot be approved by voice" in rig.announcer.pending_texts()[0]


# ---- spoken result summary (app.services.task_result) --------------------------------------


class ResultRig(Rig):
    """Facts carry the stored spoken summary of the finished task, as the DB resolver adds it."""

    def __init__(self, spoken: str, status: str = "completed") -> None:
        super().__init__()
        self.spoken_summary = spoken
        self.status = status

    async def resolve(self, ws: UUID, user: UUID, task_id: str) -> TaskFacts | None:
        return TaskFacts(
            task_id,
            self.owner,
            "Best book",
            self.status,
            result_spoken=self.spoken_summary,
        )


@pytest.mark.asyncio
async def test_the_final_update_carries_the_stored_result_summary_once() -> None:
    rig = ResultRig("Atomic Habits leads the list. Two sources disagree on the second book.")
    rig.announcer.start()
    await rig.announcer.on_event("task_completed", {"task_id": "t9"})
    await rig.announcer.on_event("task_completed", {"task_id": "t9"})  # duplicate event
    await rig.wait_spoken(1)
    await asyncio.sleep(0.05)
    await rig.announcer.close()
    assert len(rig.spoken) == 1  # announced once
    assert "Atomic Habits leads the list." in rig.spoken[0]
    assert "2 to 5 short sentences" in rig.spoken[0] and "never say it completed" in rig.spoken[0]


@pytest.mark.asyncio
async def test_a_failed_task_with_a_summary_is_spoken_with_it() -> None:
    rig = ResultRig("The search hit its rate limit; nothing was found.", status="failed")
    rig.announcer.start()
    await rig.announcer.on_event("task_failed", {"task_id": "t8", "error": "rate_limited"})
    await rig.wait_spoken(1)
    await rig.announcer.close()
    assert "rate limit" in rig.spoken[0] and "failed" in rig.spoken[0]


@pytest.mark.asyncio
async def test_without_a_summary_the_update_is_unchanged() -> None:
    rig = ResultRig("")
    rig.announcer.start()
    await rig.announcer.on_event("task_completed", {"task_id": "t7"})
    await rig.wait_spoken(1)
    await rig.announcer.close()
    assert "Result summary" not in rig.spoken[0]


@pytest.mark.asyncio
async def test_the_db_resolver_step_attaches_the_stored_summary(monkeypatch: Any) -> None:
    from app.services import task_result
    from app.voice import task_watch

    class Service:
        async def ensure(self, ws: UUID, task_id: UUID) -> Any:
            return task_result.StoredResult("md", "Spoken text.", "done", (), "m", False)

    monkeypatch.setattr(task_result, "current", lambda: Service())
    facts = TaskFacts("t", uuid4(), "Title", "completed")
    out = await task_watch._with_result(facts, WS, uuid4())
    assert out.result_spoken == "Spoken text."
    running = TaskFacts("t", uuid4(), "Title", "executing")
    assert (await task_watch._with_result(running, WS, uuid4())).result_spoken == ""
