"""Scheduler core: next_run (DST, weekdays, zones), validation, store, service, prompt."""

from __future__ import annotations

from datetime import UTC, datetime
from zoneinfo import ZoneInfo

import pytest

from app.agents.prompts.scheduled_report import build_instruction, spoken_summary
from app.schedules.models import Cadence, Deliver, Depth
from app.schedules.next_run import CadenceError, next_run, occurrences_between
from app.schedules.service import (
    LimitReached,
    NotFound,
    ScheduleInput,
    SchedulePatch,
    VersionConflict,
)
from app.schedules.validation import ScheduleValidationError, normalise_days, validate_cadence
from app.services.deliverable_intent import CHAT, requested_deliverable
from tests.support.schedule_fakes import ACCOUNT, USER, WS, make_rig


def utc(year: int, month: int, day: int, hour: int = 0, minute: int = 0) -> datetime:
    return datetime(year, month, day, hour, minute, tzinfo=UTC)


def test_daily_next_run_in_zone() -> None:
    c = Cadence(type="daily", time="08:00", timezone="Asia/Kolkata")
    assert next_run(c, utc(2026, 11, 13, 0, 0)) == utc(2026, 11, 13, 2, 30)
    assert next_run(c, utc(2026, 11, 13, 2, 30)) == utc(2026, 11, 14, 2, 30)  # strictly after


def test_weekdays_skip_weekend() -> None:
    c = Cadence(type="weekdays", time="09:00", timezone="UTC")
    friday = utc(2026, 11, 13, 10, 0)  # a Friday
    assert next_run(c, friday) == utc(2026, 11, 16, 9, 0)  # Monday


def test_weekly_and_custom_days() -> None:
    weekly = Cadence(type="weekly", time="07:30", days=["wed"], timezone="UTC")
    assert next_run(weekly, utc(2026, 11, 13, 0, 0)) == utc(2026, 11, 18, 7, 30)
    custom = Cadence(type="custom_days", time="07:30", days=["mon", "sat"], timezone="UTC")
    assert next_run(custom, utc(2026, 11, 13, 0, 0)) == utc(2026, 11, 14, 7, 30)


def test_dst_spring_forward_gap_runs_after_gap() -> None:
    c = Cadence(type="daily", time="02:30", timezone="America/New_York")
    moment = next_run(c, utc(2027, 3, 14, 0, 0))  # 2027-03-14 02:30 does not exist
    local = moment.astimezone(ZoneInfo("America/New_York"))
    assert local.date().isoformat() == "2027-03-14" and local.hour == 3


def test_dst_keeps_wall_time_across_change() -> None:
    c = Cadence(type="daily", time="08:00", timezone="Europe/Berlin")
    before = next_run(c, utc(2026, 10, 24, 12, 0)).astimezone(ZoneInfo("Europe/Berlin"))
    after = next_run(c, utc(2026, 10, 25, 12, 0)).astimezone(ZoneInfo("Europe/Berlin"))
    assert before.hour == after.hour == 8


def test_dst_fall_back_runs_once_per_day() -> None:
    c = Cadence(type="daily", time="01:30", timezone="America/New_York")
    found = occurrences_between(c, utc(2026, 11, 1, 0, 0), utc(2026, 11, 2, 12, 0))
    assert len(found) == 2  # Nov 1 and Nov 2, never twice on the ambiguous day


def test_bad_zone_time_and_days() -> None:
    with pytest.raises(CadenceError):
        next_run(Cadence(timezone="Mars/Base"), utc(2026, 1, 1))
    with pytest.raises(ScheduleValidationError):
        validate_cadence(Cadence(time="25:00"))
    with pytest.raises(ScheduleValidationError):
        validate_cadence(Cadence(type="weekly", days=[]))
    assert normalise_days([0, "Friday", "sun", "fri"]) == ["mon", "fri", "sun"]


def _input(**over: object) -> ScheduleInput:
    base: dict[str, object] = {
        "name": "Tech news",
        "instructions": "The big news in the tech industry today.",
        "cadence": Cadence(time="08:00", timezone="Asia/Kolkata"),
        "deliver": Deliver(mail=True, recipients=[ACCOUNT.upper()]),
    }
    base.update(over)
    return ScheduleInput.model_validate(base)


async def test_create_normalises_and_limits() -> None:
    rig = make_rig()
    item = await rig.service.create(WS, USER, _input())
    assert item.deliver.recipients == [ACCOUNT]  # normalised
    assert item.next_run_at is not None and item.depth.sources_target == 10
    for _ in range(4):
        await rig.service.create(WS, USER, _input())
    with pytest.raises(LimitReached):
        await rig.service.create(WS, USER, _input())


@pytest.mark.parametrize(
    "over",
    [
        {"instructions": "short"},
        {"instructions": "x" * 2001},
        {"instructions": "use password: hunter2hunter2 please for the report"},
        {"instructions": "key AIzaSyA1234567890123456789012345678901234 here"},
        {"deliver": Deliver(mail=True, recipients=[])},
        {"deliver": Deliver(mail=True, recipients=["a@b.com\nbcc:x@y.com"])},
        {"deliver": Deliver(mail=True, recipients=[f"a{i}@b.com" for i in range(4)])},
        {"depth": Depth(sources_target=99)},
        {"format": "exe"},
    ],
)
async def test_validation_rejects(over: dict[str, object]) -> None:
    rig = make_rig()
    with pytest.raises((ScheduleValidationError, ValueError)):
        await rig.service.create(WS, USER, _input(**over))


async def test_patch_version_pause_resume_and_delete() -> None:
    rig = make_rig()
    item = await rig.service.create(WS, USER, _input())
    paused = await rig.service.patch(WS, USER, item.id, SchedulePatch(enabled=False))
    assert not paused.enabled and paused.next_run_at is None and paused.paused_reason == "user"
    with pytest.raises(VersionConflict):
        await rig.service.patch(WS, USER, item.id, SchedulePatch(name="x2", version=1))
    resumed = await rig.service.patch(WS, USER, item.id, SchedulePatch(enabled=True))
    assert resumed.enabled and resumed.next_run_at is not None
    await rig.service.delete(WS, USER, item.id)
    with pytest.raises(NotFound):
        await rig.service.delete(WS, USER, item.id)


async def test_store_retries_on_cas_race_and_indexes_user() -> None:
    rig = make_rig()
    rig.backend.fail_next_cas = 2
    await rig.service.create(WS, USER, _input())
    assert (WS, USER) in await rig.store.index.members()
    assert len((await rig.store.load(WS, USER)).items) == 1


def test_chat_instruction_names_no_format_and_file_instruction_does() -> None:
    kwargs = {
        "name": "Tech news",
        "instructions": "Top tech stories",
        "topic": "tech industry",
        "day": datetime(2026, 11, 14).date(),
        "timezone": "Asia/Kolkata",
        "sources_target": 10,
    }
    chat = build_instruction(file_format="chat", **kwargs)  # type: ignore[arg-type]
    assert requested_deliverable(chat) == CHAT
    assert "Saturday, 14 November 2026" in chat
    assert requested_deliverable(build_instruction(file_format="pdf", **kwargs)) == "pdf"  # type: ignore[arg-type]


def test_spoken_summary_has_no_urls() -> None:
    md = (
        "# Tech - Friday\n## Top stories\n### Chip maker wins\nText [Reuters](https://r.com/x).\n"
        "## Key takeaways\n- AI spend rises\n- Chips tight\n"
    )
    spoken = spoken_summary(md)
    assert "http" not in spoken and "Chip maker wins" in spoken and "AI spend rises" in spoken


async def test_run_due_waits_until_nothing_is_due_or_running() -> None:
    from app.schedules.run_due import outstanding, wait_until_idle

    rig = make_rig(datetime(2026, 11, 13, 7, 0, tzinfo=UTC))
    await rig.service.create(WS, USER, _input())
    rig.clock.current = datetime(2026, 11, 14, 3, 0, tzinfo=UTC)
    doc = await rig.store.load(WS, USER)
    assert outstanding(doc, rig.clock.now()) == {"running": 0, "due": 1, "retry": 0}
    slept: list[float] = []

    async def sleep(seconds: float) -> None:
        slept.append(seconds)
        await rig.runner.tick()  # the backend's runner does the work meanwhile
        if len(slept) == 2:
            from app.schedules.task_probe import TaskOutcome

            rig.probe.outcome = TaskOutcome("completed", report="# R\n- a")

    result = await wait_until_idle(rig.store, rig.clock, max_seconds=600, sleep=sleep)
    assert result["idle"] is True and len(slept) >= 2
    capped = await wait_until_idle(make_rig().store, rig.clock, max_seconds=0, sleep=sleep)
    assert capped["idle"] is True  # nothing registered: nothing to wait for
