"""Scheduled reports by VOICE through the Live bridge and the voice tool registry (ADR-0033).

The owner's failure was a live voice turn: "Can you add a scheduler?" ended in "I cannot schedule".
Here a scripted fake Live model calls the tools: they are declared, a missing detail comes back as
a spoken question for the model (never guessed), the preview card is sent as ``SCHEDULE_DRAFT``,
and voice only PROPOSES: nothing exists until the REST click. No Gemini, no network.
"""

from __future__ import annotations

import asyncio
from typing import Any, cast
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.auth.models import Principal
from app.schedules import runtime as schedule_runtime
from app.schedules.models import Cadence
from app.schedules.service import ScheduleInput
from app.schedules.tools import (
    DELETE_SCHEDULE,
    SCHEDULE_REPORT,
    VOICE_SCHEDULE_TOOL_NAMES,
    schedule_voice_declarations,
)
from app.voice.connection import VoiceConnection, VoiceDeps
from app.voice.gemini_live import GeminiLiveBridge
from app.voice.persistence import VoicePersistenceService
from app.voice.prompts import SCHEDULES_VOICE_INSTRUCTION, build_voice_instruction
from app.voice.tools import VoiceExecutionContext, VoiceToolRegistry
from tests.support import fake_live as fl
from tests.support.schedule_fakes import ACCOUNT, USER, WS, Rig, make_rig

SESSION = uuid4()
NEWS = "The big news in the tech industry today, with sources."


class Persistence:
    def __init__(self, engine: object | None = object()) -> None:
        self.engine = engine
        self.saved: list[dict[str, Any]] = []

    async def save_turn(self, *args: Any, **kwargs: Any) -> UUID:
        self.saved.append(kwargs)
        return uuid4()


@pytest.fixture(autouse=True)
def _reset_runtime() -> Any:
    yield
    schedule_runtime.set_current(None)


def connection(persistence: Persistence) -> tuple[VoiceConnection, list[dict[str, Any]]]:
    principal = Principal(user_id=USER, workspace_id=WS, role="user", session_id=uuid4())
    deps = VoiceDeps(
        persistence=cast(VoicePersistenceService, persistence),  # records save_turn only
        session_manager=AsyncMock(),
        redis=None,
        model_gateway=None,
        queue=None,
        api_key="",
        models=[],
    )
    conn = VoiceConnection(AsyncMock(), principal, deps)
    conn.session_id = SESSION
    frames: list[dict[str, Any]] = []

    async def emit(frame: dict[str, Any]) -> None:
        frames.append(frame)

    conn._emit = emit  # type: ignore[method-assign]
    return conn, frames


async def live_rig(rig: Rig, persistence: Persistence | None = None) -> Any:
    schedule_runtime.set_current(rig.services)
    store = persistence or Persistence()
    conn, frames = connection(store)
    registry = VoiceToolRegistry()
    await conn._enable_connector_tools(registry)
    live = fl.FakeLive()
    bridge = GeminiLiveBridge(
        api_key="k",
        system_instruction="x",
        tool_registry=registry,
        context=VoiceExecutionContext(workspace_id=WS, user_id=USER, session_id=str(SESSION)),
        connector=live.connector,
    )
    await bridge.connect()
    return conn, registry, bridge, live, frames, store


async def call_by_voice(bridge: Any, live: Any, name: str, args: dict[str, Any]) -> dict[str, Any]:
    """The live model calls one tool; return what the bridge reported (and sent back)."""
    events = bridge.receive_events().__aiter__()
    live.session.push_turn(fl.tool_call(name, args, f"c-{len(live.session.tool_responses)}"))
    while True:
        event = await asyncio.wait_for(events.__anext__(), 3)
        if event["type"] == "TOOL_EXECUTED":
            return dict(event["result"])


# ------------------------------------------------------------------------ declarations


async def test_the_live_model_gets_the_schedule_tools_without_delete() -> None:
    conn, registry, bridge, live, _, _ = await live_rig(make_rig())
    declared = {d["name"] for d in registry.get_tool_declarations()}
    assert set(VOICE_SCHEDULE_TOOL_NAMES) <= declared and DELETE_SCHEDULE not in declared
    assert set(VOICE_SCHEDULE_TOOL_NAMES) <= set(registry.allowed_tool_names)
    assert conn._schedules_on is True
    sent = {d["name"]: d for d in schedule_voice_declarations()}
    description = sent[SCHEDULE_REPORT]["description"]
    assert "daily" in description and "never refuse" in description
    assert sent[SCHEDULE_REPORT]["parameters"]["type"] == "OBJECT"
    await bridge.close()


async def test_no_schedule_tools_without_the_scheduler_or_a_database() -> None:
    conn, registry, _, _, _, _ = await live_rig(make_rig())
    schedule_runtime.set_current(None)  # the kill switch / no database
    off, _ = connection(Persistence())
    fresh = VoiceToolRegistry()
    await off._enable_connector_tools(fresh)
    assert not set(VOICE_SCHEDULE_TOOL_NAMES) & {d["name"] for d in fresh.get_tool_declarations()}
    schedule_runtime.set_current(make_rig().services)
    no_db, _ = connection(Persistence(engine=None))
    other = VoiceToolRegistry()
    await no_db._enable_connector_tools(other)
    assert not set(VOICE_SCHEDULE_TOOL_NAMES) & {d["name"] for d in other.get_tool_declarations()}


def test_the_prompt_says_maya_can_schedule_and_must_not_refuse() -> None:
    text = build_voice_instruction(memory_on=False, schedules_on=True)
    assert SCHEDULES_VOICE_INSTRUCTION in text
    assert "you CAN add a scheduler" in text and "never say you cannot schedule" in text
    assert "say yes and ask" in text  # "Can you add a scheduler?" gets a yes and a question
    assert "one short question at a time" in text
    assert "Nothing is scheduled until the user taps Create" in text
    assert "a spoken 'yes' does not create it" in text
    assert "schedule_report" not in build_voice_instruction(memory_on=False)  # only when on


# ------------------------------------------------------------- the spoken conversation


async def test_missing_slots_are_asked_aloud_never_guessed_and_voice_only_proposes() -> None:
    rig = make_rig()
    conn, registry, bridge, live, frames, store = await live_rig(rig)
    conn._user_now = "I want you to schedule a daily 8:00 a.m. report sent to me as a mail"

    # The model calls the tool at once with what it heard (no time zone, no recipient choice).
    first = await call_by_voice(
        bridge,
        live,
        SCHEDULE_REPORT,
        {
            "instructions": NEWS,
            "name": "Tech news",
            "time": "08:00",
            "cadence_type": "daily",
            "mail": True,
        },
    )
    assert first["status"] == "needs_details" and first["missing"] == ["Time zone", "Recipient"]
    assert "ALOUD" in first["message"] and "one short spoken question at a time" in first["message"]
    assert "ask_user" not in first["message"] and "Do not guess" in first["message"]
    assert live.session.tool_responses[0].response == first  # the model heard exactly that
    assert not [f for f in frames if f.get("type") == "SCHEDULE_DRAFT"]
    assert (await rig.service.get_doc(WS, USER)).drafts == []

    # After the spoken answers the model calls again: a preview card, not a schedule.
    second = await call_by_voice(
        bridge,
        live,
        SCHEDULE_REPORT,
        {
            "instructions": NEWS,
            "name": "Tech news",
            "time": "08:00",
            "cadence_type": "daily",
            "mail": True,
            "timezone": "Asia/Kolkata",
            "recipients": ["my account email"],
        },
    )
    assert second["status"] == "preview_shown" and "NOTHING is scheduled" in second["message"]
    (card_frame,) = [f for f in frames if f.get("type") == "SCHEDULE_DRAFT"]
    card = card_frame["card"]
    assert card["draft_id"].startswith("drf_") and card["schedule"]["deliver"]["recipients"] == [
        ACCOUNT
    ]
    assert card["schedule"]["cadence"]["timezone"] == "Asia/Kolkata"
    (saved,) = store.saved  # the card is also a stored chat message
    assert saved["metadata"]["schedule_drafts"][0]["draft_id"] == card["draft_id"]
    doc = await rig.service.get_doc(WS, USER)
    assert doc.items == [] and [d.status for d in doc.drafts] == ["pending"]  # proposed only

    # Voice has no tool that confirms or deletes; "yes" said aloud is not a tool call.
    assert not any("confirm" in n for n in registry.allowed_tool_names)
    with pytest.raises(Exception):  # noqa: B017, PT011 - any refusal is fine
        await registry.execute_tool_async(
            DELETE_SCHEDULE,
            {"schedule": "Tech news", "confirm": True},
            VoiceExecutionContext(workspace_id=WS, user_id=USER, session_id=str(SESSION)),
        )
    assert (await rig.service.get_doc(WS, USER)).items == []
    await bridge.close()


async def test_list_pause_resume_and_run_now_by_voice() -> None:
    rig = make_rig()
    conn, registry, bridge, live, _, _ = await live_rig(rig)
    draft = await rig.service.create_draft(
        WS,
        USER,
        ScheduleInput(
            name="Tech news",
            instructions=NEWS,
            cadence=Cadence(time="08:00", timezone="Asia/Kolkata"),
        ),
        account_email=ACCOUNT,
    )
    await rig.service.confirm_draft(WS, USER, draft.id)  # the click, done by the test

    listed = await call_by_voice(bridge, live, "list_schedules", {})
    assert listed["status"] == "ok" and listed["schedules"][0]["name"] == "Tech news"
    paused = await call_by_voice(bridge, live, "pause_schedule", {"schedule": "Tech news"})
    assert paused["status"] == "ok"
    assert (await rig.service.get_doc(WS, USER)).items[0].enabled is False
    await call_by_voice(bridge, live, "resume_schedule", {"schedule": "Tech news"})
    assert (await rig.service.get_doc(WS, USER)).items[0].enabled is True
    ran = await call_by_voice(bridge, live, "run_schedule_now", {"schedule": "Tech news"})
    assert ran["status"] == "ok" and len(rig.submitter.calls) == 1
    await bridge.close()


async def test_mail_not_set_up_is_said_plainly_by_voice() -> None:
    rig = make_rig(sender=False)
    _, _, bridge, live, frames, _ = await live_rig(rig)
    result = await call_by_voice(
        bridge,
        live,
        SCHEDULE_REPORT,
        {
            "instructions": NEWS,
            "time": "08:00",
            "cadence_type": "daily",
            "timezone": "Asia/Kolkata",
            "mail": True,
            "recipients": ["my account email"],
        },
    )
    assert result["status"] == "mail_unavailable" and "Mail is not set up" in result["message"]
    assert not [f for f in frames if f.get("type") == "SCHEDULE_DRAFT"]
    await bridge.close()
