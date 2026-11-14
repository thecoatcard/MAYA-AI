"""Scheduled reports through the in-memory chat harness (ADR-0033). No model, no network.

The owner's failure was "Can you add a scheduler?" answered with "I cannot schedule". These tests
drive the REAL chat tool dispatch (scripted fake Gemini streams): the scheduler is offered, a
missing detail is asked once (ask_user card) and never guessed, schedule_report only shows a
PREVIEW (``schedule.draft``), the schedule exists only after the REST click
(``POST /schedules/drafts/{id}/confirm``), and the report is mailed by MAYA's system mailer.
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from datetime import UTC, datetime, timedelta
from typing import Any
from unittest.mock import patch

import pytest

from app.api.v1.chat import get_chat_rate_limiter
from app.schedules import runtime as schedule_runtime
from app.schedules.task_probe import TaskOutcome
from app.schedules.tools import SCHEDULE_TOOL_NAMES
from tests.integration.test_chat_ask_user import ask_script, card_events
from tests.integration.test_chat_inline_files import IdempotentSubmitter, call
from tests.integration.test_chat_latency import wire
from tests.integration.test_chat_memory import Prefs, tool_names
from tests.integration.test_chat_streaming import (
    World,
    events,
    make_world,
    names,
    reply_text,
    send,
    sign_in,
    stream_calls,
)
from tests.support.schedule_fakes import ACCOUNT, Rig, make_rig

START = datetime(2026, 11, 13, 2, 0, tzinfo=UTC)  # 07:30 in Kolkata
FULL_REQUEST = (
    "schedule a daily 8:00 a.m. report sent to me as a mail: big news in the tech industry"
)
NEWS = "The big news in the tech industry today, with sources."
REPORT = "# Tech news\n## Top stories\n### Chip maker wins\nA chip story.\n## Key takeaways\n- x\n"


def install(world: World, rig: Rig) -> None:
    world.app.state.schedule_services = rig.services
    world.app.dependency_overrides[get_chat_rate_limiter] = lambda: world.limiter
    schedule_runtime.set_current(rig.services)


@pytest.fixture
async def world() -> AsyncIterator[World]:
    async with make_world() as value:
        value.submitter = IdempotentSubmitter()  # type: ignore[attr-defined]
        wire(value, prefs=Prefs(memory_on=False), submitter=value.submitter)  # type: ignore[attr-defined]
        yield value
    schedule_runtime.set_current(None)


@pytest.fixture
def rig() -> Rig:
    return make_rig(START)


def drafts(frames: list[Any]) -> list[dict[str, Any]]:
    return [f.data for f in frames if f.event == "schedule.draft"]


async def items(rig: Rig, account: Any) -> list[Any]:
    return (await rig.service.get_doc(account.workspace_id, account.user_id)).items


async def start_request(world: World, account: Any, rig: Rig) -> tuple[str, dict[str, Any]]:
    """Turn 1: the full request. The model calls schedule_report with what the user said (no time
    zone, no recipient choice), gets the questions back and shows them on ONE card."""
    world.models.queued_stream_scripts = [
        call(
            "schedule_report",
            {
                "instructions": NEWS,
                "name": "Tech news",
                "time": "08:00",
                "cadence_type": "daily",
                "mail": True,
            },
        ),
        ask_script(
            {
                "questions": [
                    {"question": "Is Asia/Kolkata the right time zone?", "header": "Time zone"},
                    {
                        "question": "Who should get the mail?",
                        "header": "Recipient",
                        "options": [{"label": "My account email"}, {"label": "Another address"}],
                    },
                ]
            }
        ),
    ]
    frames = events(await send(world, account, FULL_REQUEST))
    (card,) = card_events(frames)
    assert drafts(frames) == []  # nothing is previewed while details are missing
    return frames[0].data["session_id"], card


async def answer_and_preview(
    world: World, account: Any, session_id: str, card: dict[str, Any]
) -> list[Any]:
    world.models.queued_stream_scripts = [
        call(
            "schedule_report",
            {
                "instructions": NEWS,
                "name": "Tech news",
                "time": "08:00",
                "cadence_type": "daily",
                "mail": True,
                "timezone": "Asia/Kolkata",
                "recipients": ["my account email"],
            },
        ),
        [{"text": "Please review the preview and click Create."}],
    ]
    answer = {
        "ask_id": card["ask_id"],
        "answers": [{"selected": ["Asia/Kolkata"]}, {"selected": ["My account email"]}],
    }
    return events(await send(world, account, "x", session_id=session_id, answer=answer))


# ------------------------------------------------------------- (a) "Can you add a scheduler?"


async def test_can_you_add_a_scheduler_is_offered_and_asks_what_to_schedule(
    world: World, rig: Rig
) -> None:
    install(world, rig)
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        ask_script(
            {
                "questions": [
                    {"question": "What should I schedule?", "header": "What"},
                    {"question": "At what time?", "header": "Time"},
                    {
                        "question": "Where should it go?",
                        "header": "Delivery",
                        "options": [{"label": "Mail to me"}, {"label": "Chat only"}],
                    },
                ]
            },
            lead="Yes, I can schedule reports.",
        )
    ]
    frames = events(await send(world, account, "Can you add a scheduler?"))

    (only,) = stream_calls(world)
    declared = tool_names(only, with_ask=True)
    assert set(SCHEDULE_TOOL_NAMES) <= set(declared) and "ask_user" in declared
    system = str(only["config"].system_instruction)
    assert "never say you cannot schedule" in system
    assert "Scheduled reports ARE supported" in system
    (card,) = card_events(frames)
    assert [q["header"] for q in card["questions"]] == ["What", "Time", "Delivery"]
    assert "cannot" not in reply_text(frames).lower()
    assert drafts(frames) == [] and await items(rig, account) == []


# ------------------------------------------------ (b) slots asked once, never guessed; click only


async def test_full_request_asks_the_missing_slots_once_then_shows_a_preview_only(
    world: World, rig: Rig
) -> None:
    install(world, rig)
    account = await sign_in(world)
    session_id, card = await start_request(world, account, rig)

    # The tool result (what the model saw) listed exactly the missing slots, and nothing is stored.
    second = stream_calls(world)[1]
    seen = str(second["contents"])
    assert "needs_details" in seen and "Time zone" in seen and "Recipient" in seen
    assert [q["header"] for q in card["questions"]] == ["Time zone", "Recipient"]
    assert len(card["questions"]) <= 3
    assert await items(rig, account) == []
    doc = await rig.service.get_doc(account.workspace_id, account.user_id)
    assert doc.drafts == []

    frames = await answer_and_preview(world, account, session_id, card)
    (shown,) = drafts(frames)
    assert shown["draft_id"].startswith("drf_") and shown["name"] == "Tech news"
    assert shown["schedule"]["cadence"]["timezone"] == "Asia/Kolkata"
    assert shown["schedule"]["deliver"]["recipients"] == [ACCOUNT]  # the verified account email
    assert shown["schedule"]["format"] == "chat"  # chat unless a file was named
    assert card_events(frames) == []
    assert names(frames)[-1] == "done"
    stored = world.chat.rows("orchestrator")[-1]
    assert stored.metadata["schedule_drafts"][0]["draft_id"] == shown["draft_id"]
    assert await items(rig, account) == []  # a preview is not a schedule


async def test_a_typed_yes_creates_nothing_and_only_the_click_does(world: World, rig: Rig) -> None:
    install(world, rig)
    account = await sign_in(world)
    session_id, card = await start_request(world, account, rig)
    frames = await answer_and_preview(world, account, session_id, card)
    (shown,) = drafts(frames)

    for typed in ("yes", "approved", "yes, create it"):
        world.models.queued_stream_scripts = [[{"text": "Please click Create on the card."}]]
        again = events(await send(world, account, typed, session_id=session_id))
        assert drafts(again) == []
        called = tool_names(stream_calls(world)[-1], with_ask=True)
        assert not any("confirm" in n for n in called)  # no tool can confirm a draft
        assert await items(rig, account) == []

    url = f"/api/v1/schedules/drafts/{shown['draft_id']}/confirm"
    created = await world.client.post(url, headers=account.headers)
    assert created.status_code == 201, created.text
    body = created.json()
    assert body["name"] == "Tech news" and body["cadence"]["time"] == "08:00"
    assert [i.id for i in await items(rig, account)] == [body["id"]]
    # Confirming twice is idempotent: still one schedule.
    repeated = await world.client.post(url, headers=account.headers)
    assert repeated.status_code == 201 and repeated.json()["id"] == body["id"]
    assert len(await items(rig, account)) == 1


# ------------------------------------- (c) list, next run in the device zone, system mailer


async def confirmed(world: World, rig: Rig, account: Any) -> tuple[str, str]:
    await rig.service.set_settings(account.workspace_id, account.user_id, timezone="Asia/Kolkata")
    session_id, card = await start_request(world, account, rig)
    frames = await answer_and_preview(world, account, session_id, card)
    (shown,) = drafts(frames)
    url = f"/api/v1/schedules/drafts/{shown['draft_id']}/confirm"
    created = await world.client.post(url, headers=account.headers)
    assert created.status_code == 201, created.text
    return session_id, str(created.json()["id"])


async def test_confirmed_schedule_is_listed_with_the_right_next_run(world: World, rig: Rig) -> None:
    install(world, rig)
    account = await sign_in(world)
    session_id, schedule_id = await confirmed(world, rig, account)
    (item,) = await items(rig, account)
    assert item.next_run_at == datetime(2026, 11, 13, 2, 30, tzinfo=UTC)  # 08:00 in Kolkata

    world.models.queued_stream_scripts = [
        call("list_schedules", {}),
        [{"text": "You have one schedule: Tech news, daily at 08:00."}],
    ]
    frames = events(
        await send(world, account, "show my schedules", session_id=session_id)  # keyword: schedul
    )
    result = str(stream_calls(world)[-1]["contents"])
    assert schedule_id in result and "Tech news" in result and "2026-11-13T02:30" in result
    assert "Tech news" in reply_text(frames)


async def test_the_report_is_mailed_by_the_system_mailer_to_the_account_email(
    world: World, rig: Rig
) -> None:
    install(world, rig)
    account = await sign_in(world)
    await confirmed(world, rig, account)
    rig.clock.current = START + timedelta(minutes=31)  # 08:01 in Kolkata
    await rig.runner.tick()
    assert len(rig.submitter.calls) == 1 and rig.submitter.calls[0]["source"] == "scheduler"
    rig.probe.outcome = TaskOutcome("completed", report=REPORT, sources=3)
    with patch("app.mail.service.MailService.send", side_effect=AssertionError("user mailbox")):
        await rig.runner.tick()  # the user's own mailbox (app/mail) is never used
    assert rig.sender is not None and [m.to for m in rig.sender.sent] == [ACCOUNT]


# ----------------------------------------------------- (d) pause, resume, run now by chat


async def test_pause_resume_and_run_now_by_chat(world: World, rig: Rig) -> None:
    install(world, rig)
    account = await sign_in(world)
    session_id, _ = await confirmed(world, rig, account)

    async def say(text: str, tool: str) -> None:
        world.models.queued_stream_scripts = [
            call(tool, {"schedule": "Tech news"}),
            [{"text": "Done."}],
        ]
        events(await send(world, account, text, session_id=session_id))

    await say("pause my daily schedule", "pause_schedule")
    assert (await items(rig, account))[0].enabled is False
    await say("resume my daily schedule", "resume_schedule")
    assert (await items(rig, account))[0].enabled is True
    await say("run my daily schedule now", "run_schedule_now")
    assert len(rig.submitter.calls) == 1  # one manual run started


async def test_delete_needs_the_users_word_in_chat(world: World, rig: Rig) -> None:
    install(world, rig)
    account = await sign_in(world)
    session_id, _ = await confirmed(world, rig, account)
    world.models.queued_stream_scripts = [
        call("delete_schedule", {"schedule": "Tech news", "confirm": False}),
        [{"text": "Do you want me to delete it?"}],
    ]
    events(await send(world, account, "delete my daily schedule", session_id=session_id))
    assert len(await items(rig, account)) == 1  # asked first, nothing deleted


# ------------------------------------------------------------------ (e) no mail service


async def test_without_a_mail_service_the_user_is_told_plainly(world: World) -> None:
    rig = make_rig(START, sender=False)
    install(world, rig)
    account = await sign_in(world)
    world.models.queued_stream_scripts = [
        call(
            "schedule_report",
            {
                "instructions": NEWS,
                "time": "08:00",
                "cadence_type": "daily",
                "timezone": "Asia/Kolkata",
                "mail": True,
                "recipients": ["my account email"],
            },
        ),
        [{"text": "Mail is not set up here, so I can only post it in the chat."}],
    ]
    frames = events(await send(world, account, FULL_REQUEST))
    seen = str(stream_calls(world)[1]["contents"])
    assert "mail_unavailable" in seen and "Mail is not set up" in seen
    assert drafts(frames) == [] and await items(rig, account) == []
    assert "not set up" in reply_text(frames)


# ------------------------------------------------------------------ (f) the kill switch


async def test_kill_switch_hides_every_schedule_tool_and_the_rule() -> None:
    async with make_world(schedules_enabled=False) as off:
        wire(off, prefs=Prefs(memory_on=False))
        install(off, make_rig(START))
        try:
            account = await sign_in(off)
            off.models.queued_stream_scripts = [[{"text": "ok"}]]
            events(await send(off, account, "Can you add a scheduler?"))
            (only,) = stream_calls(off)
            assert not set(SCHEDULE_TOOL_NAMES) & set(tool_names(only, with_ask=True))
            assert "never say you cannot schedule" not in str(only["config"].system_instruction)
        finally:
            schedule_runtime.set_current(None)


async def test_a_plain_chat_message_is_not_offered_the_scheduler(world: World, rig: Rig) -> None:
    install(world, rig)
    account = await sign_in(world)
    world.models.queued_stream_scripts = [[{"text": "Hello."}]]
    events(await send(world, account, "hello there"))
    (only,) = stream_calls(world)
    assert not set(SCHEDULE_TOOL_NAMES) & set(tool_names(only, with_ask=True))


def test_a_schedule_request_is_routed_to_the_scheduler_not_a_background_task() -> None:
    from app.services.chat_router import Route, route_message

    on = route_message(FULL_REQUEST, schedules=True)
    assert (on.route, on.reason) == (Route.ANSWER, "schedule_request")
    assert (
        route_message("har din subah 8 baje tech news bhejo", schedules=True).route is Route.ANSWER
    )
    # With the scheduler off the old behaviour is kept.
    assert route_message(FULL_REQUEST).reason == "background"
