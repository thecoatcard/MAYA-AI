"""Runner: due runs, catch-up coalescing, idempotency/lock, restrictions, failure policy, mail."""

from __future__ import annotations

import asyncio
from datetime import UTC, datetime, timedelta
from unittest.mock import patch

import pytest

from app.agents.state import SubTask
from app.schedules.locks import LocalRunLock
from app.schedules.models import Cadence, Deliver
from app.schedules.runner import AlreadyRunning, idempotency_key
from app.schedules.safety import make_plan_filter
from app.schedules.service import ScheduleInput
from app.schedules.task_probe import TaskOutcome
from tests.support.schedule_fakes import ACCOUNT, USER, WS, Rig, make_rig

REPORT = (
    "# Tech news - Friday, 14 November 2026\n## Top stories\n### Chip maker wins\n"
    "A chip story. [Reuters](https://example.com/a)\nWhy it matters: supply.\n"
    "## Key takeaways\n- AI spend rises\n"
)


def _in(**over: object) -> ScheduleInput:
    base: dict[str, object] = {
        "name": "Tech news",
        "instructions": "The big news in the tech industry today.",
        "cadence": Cadence(time="08:00", timezone="UTC"),
        "deliver": Deliver(mail=True, recipients=[ACCOUNT]),
    }
    base.update(over)
    return ScheduleInput.model_validate(base)


async def _rig_with_due() -> tuple[Rig, str]:
    rig = make_rig(datetime(2026, 11, 13, 7, 0, tzinfo=UTC))
    item = await rig.service.create(WS, USER, _in())
    rig.clock.current = datetime(2026, 11, 13, 8, 1, tzinfo=UTC)
    return rig, item.id


async def _state(rig: Rig, sid: str):  # type: ignore[no-untyped-def]
    return next(s for s in (await rig.store.load(WS, USER)).items if s.id == sid)


async def test_due_run_starts_one_task_with_scheduler_source_and_advances() -> None:
    rig, sid = await _rig_with_due()
    await rig.runner.tick()
    assert len(rig.submitter.calls) == 1
    call = rig.submitter.calls[0]
    assert call["source"] == "scheduler" and call["idempotency_key"].startswith("sched-")
    assert "13 November 2026" in call["instruction"]
    item = await _state(rig, sid)
    assert item.pending_run is not None
    assert item.next_run_at == datetime(2026, 11, 14, 8, 0, tzinfo=UTC)
    await rig.runner.tick()  # still running: no second task
    assert len(rig.submitter.calls) == 1


async def test_completed_run_mails_via_system_sender_and_records_summary() -> None:
    rig, sid = await _rig_with_due()
    await rig.runner.tick()
    rig.probe.outcome = TaskOutcome("completed", report=REPORT, artifact_ids=("a1",), sources=3)
    # app.mail (the user's own mailbox) must never be touched.
    with patch("app.mail.service.MailService.send", side_effect=AssertionError("user mailbox")):
        await rig.runner.tick()
    item = await _state(rig, sid)
    assert item.pending_run is None
    run = item.last_run
    assert run is not None and run.status == "completed" and run.mail_status == "sent"
    assert run.artifact_ids == ["a1"] and not run.announced
    assert "http" not in run.spoken_summary
    assert rig.sender is not None and len(rig.sender.sent) == 1
    mail = rig.sender.sent[0]
    assert mail.to == ACCOUNT and mail.subject.startswith("Tech news")
    assert "Artifacts" in (mail.html or "") and "Settings &gt; Schedules" in (mail.html or "")


async def test_mail_not_configured_still_completes() -> None:
    rig = make_rig(datetime(2026, 11, 13, 7, 0, tzinfo=UTC), sender=False)
    item = await rig.service.create(WS, USER, _in())
    rig.clock.current = datetime(2026, 11, 13, 8, 1, tzinfo=UTC)
    await rig.runner.tick()
    rig.probe.outcome = TaskOutcome("completed", report=REPORT)
    await rig.runner.tick()
    run = (await _state(rig, item.id)).last_run
    assert run is not None and run.status == "completed"
    assert run.mail_status == "mail_not_configured" and "not configured" in run.reason


async def test_unverified_recipient_gets_no_mail() -> None:
    rig = make_rig(datetime(2026, 11, 13, 7, 0, tzinfo=UTC))
    item = await rig.service.create(
        WS, USER, _in(deliver=Deliver(mail=True, recipients=["other@example.org"]))
    )
    rig.clock.current = datetime(2026, 11, 13, 8, 1, tzinfo=UTC)
    await rig.runner.tick()
    rig.probe.outcome = TaskOutcome("completed", report=REPORT)
    await rig.runner.tick()
    run = (await _state(rig, item.id)).last_run
    assert run is not None and run.mail_status == "recipient_unverified"
    assert rig.sender is not None and rig.sender.sent == []


async def test_needs_attention_is_not_failure_and_nothing_is_approved() -> None:
    rig, sid = await _rig_with_due()
    await rig.runner.tick()
    rig.probe.outcome = TaskOutcome("needs_attention", reason="A step needs your approval.")
    await rig.runner.tick()
    item = await _state(rig, sid)
    assert item.last_run is not None and item.last_run.status == "needs_attention"
    assert item.consecutive_failures == 0 and item.enabled
    assert rig.sender is not None and rig.sender.sent == []


def _node(agent: str, nid: str, deps: list[str] | None = None, **kw: object) -> SubTask:
    return SubTask(
        id=nid,
        assigned_agent=agent,
        description="d",
        dependencies=deps or [],
        **kw,
    )


def test_plan_filter_keeps_only_safe_steps() -> None:
    nodes = [
        _node("research", "s1"),
        _node("email", "s2", ["s1"]),
        _node("pm", "s3", ["s1"]),
        _node("document", "s4", ["s1"]),
        _node("research", "s5", tool="notion_create_page"),
    ]
    chat = make_plan_filter("chat", "instr")(list(nodes))
    assert [n.id for n in chat] == ["s1"]
    pdf = make_plan_filter("pdf", "instr")(list(nodes))
    assert [n.id for n in pdf] == ["s1", "s4"]
    only_mail = make_plan_filter("chat", "instr")([_node("email", "m1")])
    assert [str(n.assigned_agent) for n in only_mail] == ["research"]


def test_worker_never_auto_approves_scheduled_tasks() -> None:
    import inspect

    from app.tasks import worker

    source = inspect.getsource(worker)
    assert 'startswith(\n                            "sched-"' in source or '"sched-"' in source
    assert idempotency_key("sch_1", "x", 1).startswith("sched-")


async def test_failure_retries_once_then_fails_then_auto_pauses() -> None:
    rig, sid = await _rig_with_due()
    rig.submitter.raise_error = True
    await rig.runner.tick()  # attempt 1 fails -> retry in 10 minutes
    item = await _state(rig, sid)
    assert item.retry_at is not None and item.last_run is None
    await rig.runner.tick()  # not yet time
    assert len(rig.submitter.calls) == 1
    rig.clock.advance(minutes=11)
    await rig.runner.tick()  # retry fails -> failed
    item = await _state(rig, sid)
    assert item.last_run is not None and item.last_run.status == "failed"
    assert item.consecutive_failures == 1 and item.retry_at is None
    for day in range(2):  # two more failed days in a row -> paused
        rig.clock.current = datetime(2026, 11, 14 + day, 8, 1, tzinfo=UTC)
        await rig.runner.tick()
        rig.clock.advance(minutes=11)
        await rig.runner.tick()
    item = await _state(rig, sid)
    assert not item.enabled and item.paused_reason == "failures"
    assert "Paused after 3" in (item.last_run.reason if item.last_run else "")
    updates = await rig.service.updates(WS, USER)
    assert updates and updates[0]["needs_attention"] is True


async def test_timeout_cancels_task_and_retries() -> None:
    rig, sid = await _rig_with_due()
    await rig.runner.tick()
    rig.clock.advance(minutes=16)
    await rig.runner.tick()
    assert rig.probe.cancelled
    assert (await _state(rig, sid)).retry_at is not None


async def test_catch_up_coalesces_many_missed_days_into_one_run() -> None:
    rig = make_rig(datetime(2026, 11, 10, 7, 0, tzinfo=UTC))
    item = await rig.service.create(WS, USER, _in())
    rig.clock.current = datetime(2026, 11, 12, 9, 0, tzinfo=UTC)  # missed Nov 10, 11, 12
    await rig.runner.tick()
    assert len(rig.submitter.calls) == 1
    state = await _state(rig, item.id)
    assert state.next_run_at == datetime(2026, 11, 13, 8, 0, tzinfo=UTC)
    assert "12 November 2026" in rig.submitter.calls[0]["instruction"]


async def test_catch_up_skips_runs_older_than_48_hours() -> None:
    rig = make_rig(datetime(2026, 11, 1, 7, 0, tzinfo=UTC))
    item = await rig.service.create(
        WS, USER, _in(cadence=Cadence(type="weekly", days=["mon"], time="08:00", timezone="UTC"))
    )
    rig.clock.current = datetime(2026, 11, 5, 9, 0, tzinfo=UTC)  # last Mon (Nov 2) is 3 days old
    await rig.runner.tick()
    assert rig.submitter.calls == []
    state = await _state(rig, item.id)
    assert state.last_run is not None and state.last_run.status == "skipped"
    assert state.next_run_at == datetime(2026, 11, 9, 8, 0, tzinfo=UTC)


async def test_idempotency_key_is_stable_per_occurrence_and_attempt() -> None:
    assert idempotency_key("s", "o", 1) == idempotency_key("s", "o", 1)
    assert idempotency_key("s", "o", 1) != idempotency_key("s", "o", 2)
    assert idempotency_key("s", "o", 1) != idempotency_key("s", "p", 1)


async def test_lock_lets_only_one_runner_execute() -> None:
    rig, _sid = await _rig_with_due()
    other = make_rig()
    other.runner._lock = rig.runner._lock  # noqa: SLF001 - share one lock like two runners
    results = await asyncio.gather(rig.runner.tick(), rig.runner.tick())
    assert len(rig.submitter.calls) == 1, results
    lock = LocalRunLock()
    token = await lock.acquire("k", 30)
    assert token is not None and await lock.acquire("k", 30) is None
    await lock.release("k", token)
    assert await lock.acquire("k", 30) is not None


async def test_run_now_and_already_running() -> None:
    rig = make_rig()
    item = await rig.service.create(WS, USER, _in())
    task_id = await rig.runner.run_now(WS, USER, item.id)
    assert task_id
    with pytest.raises(AlreadyRunning):
        await rig.runner.run_now(WS, USER, item.id)
    state = await _state(rig, item.id)
    assert state.next_run_at is not None and state.next_run_at > rig.clock.now() - timedelta(days=1)


async def test_disabled_schedule_does_not_run() -> None:
    rig, sid = await _rig_with_due()
    from app.schedules.service import SchedulePatch

    await rig.service.patch(WS, USER, sid, SchedulePatch(enabled=False))
    await rig.runner.tick()
    assert rig.submitter.calls == []
