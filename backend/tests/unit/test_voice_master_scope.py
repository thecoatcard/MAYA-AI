"""Master (top-bar) voice scope: all of the user's tasks, conversation named, own tasks only."""

from __future__ import annotations

import asyncio
from typing import Any
from uuid import UUID, uuid4

import pytest

from app.voice import protocol
from app.voice.prompts import MASTER_VOICE_INSTRUCTION, build_voice_instruction
from app.voice.task_watch import (
    AnnouncedStore,
    Milestone,
    TaskAnnouncer,
    TaskFacts,
    render_milestone,
)
from tests.support import fake_live as fl
from tests.support.voice_app import FakePersistence, recv, voice_client

WS = uuid4()


def test_scope_defaults_to_conversation_and_only_exact_master_wins() -> None:
    assert protocol.normalize_scope("master") == protocol.SCOPE_MASTER
    assert protocol.normalize_scope(" MASTER ") == protocol.SCOPE_MASTER
    for other in (None, "", "conversation", "global", "all", 3, ["master"]):
        assert protocol.normalize_scope(other) == protocol.SCOPE_CONVERSATION


def test_master_prompt_is_a_tracker_that_never_approves() -> None:
    master = build_voice_instruction(memory_on=False, master=True)
    plain = build_voice_instruction(memory_on=False)
    assert MASTER_VOICE_INSTRUCTION in master and MASTER_VOICE_INSTRUCTION not in plain
    assert "Never approve" in MASTER_VOICE_INSTRUCTION
    assert "nothing changed" in MASTER_VOICE_INSTRUCTION


def test_conversation_is_named_only_for_master() -> None:
    facts = TaskFacts("t1", uuid4(), "Solar deck", "completed", conversation="Q3 planning")
    assert 'conversation "Q3 planning"' in render_milestone(
        Milestone.COMPLETED, facts, name_conversation=True
    )
    assert "Q3 planning" not in render_milestone(Milestone.COMPLETED, facts)


class Rig:
    """Tasks live in many conversations; the resolver returns only the caller's own."""

    def __init__(self, *, name_conversation: bool) -> None:
        self.user = uuid4()
        self.tasks: dict[str, tuple[UUID, str]] = {}
        self.spoken: list[str] = []
        self.announcer = TaskAnnouncer(
            workspace_id=WS,
            user_id=self.user,
            resolver=self.resolve,
            store=AnnouncedStore(),
            speak=self.speak,
            is_busy=lambda: False,
            notify=self.notify,
            poll_seconds=0.002,
            gap_seconds=0.0,
            settle_seconds=0.01,
            name_conversation=name_conversation,
        )

    async def resolve(self, ws: UUID, user: UUID, task_id: str) -> TaskFacts | None:
        entry = self.tasks.get(task_id)
        if entry is None or entry[0] != user:  # the DB resolver's owner check
            return None
        return TaskFacts(task_id, entry[0], f"Task {task_id}", "completed", conversation=entry[1])

    async def speak(self, text: str) -> None:
        self.spoken.append(text)

    async def notify(self, frame: dict[str, Any]) -> None:
        return None

    async def wait(self, count: int) -> None:
        for _ in range(400):
            if len(self.spoken) >= count:
                return
            await asyncio.sleep(0.005)


@pytest.mark.asyncio
async def test_master_announces_tasks_of_every_conversation_with_its_title() -> None:
    rig = Rig(name_conversation=True)
    rig.tasks = {"a": (rig.user, "Chat one"), "b": (rig.user, "Chat two")}
    rig.announcer.start()
    await rig.announcer.on_event("task_completed", {"task_id": "a"})
    await rig.announcer.on_event("task_completed", {"task_id": "b"})
    await rig.wait(1)
    await asyncio.sleep(0.05)
    await rig.announcer.close()
    joined = "\n".join(rig.spoken)
    assert "Chat one" in joined and "Chat two" in joined


@pytest.mark.asyncio
async def test_master_never_exposes_another_users_task() -> None:
    rig = Rig(name_conversation=True)
    rig.tasks = {"mine": (rig.user, "Mine"), "theirs": (uuid4(), "Secret chat")}
    rig.announcer.start()
    await rig.announcer.on_event("task_completed", {"task_id": "theirs"})
    await rig.announcer.on_event("task_completed", {"task_id": "mine"})
    await rig.wait(1)
    await asyncio.sleep(0.05)
    await rig.announcer.close()
    joined = "\n".join(rig.spoken)
    assert "Mine" in joined
    assert "Secret chat" not in joined and "theirs" not in joined


def _system_text(live: fl.FakeLive) -> str:
    return str(live.connects[0][1])


@pytest.mark.parametrize(
    ("query", "master"),
    [("&scope=master", True), ("", False), ("&scope=bogus", False)],
)
def test_ws_scope_parameter_selects_the_prompt(query: str, master: bool) -> None:
    live = fl.FakeLive()
    with voice_client(live=live, persistence=FakePersistence()) as client:
        with client.websocket_connect(
            f"/api/v1/voice/live?token=valid-jwt&protocol=2{query}"
        ) as ws:
            assert recv(ws)["type"] == "READY"
    assert ("progress tracker" in _system_text(live)) is master


def test_ws_hello_scope_selects_master() -> None:
    live = fl.FakeLive()
    with voice_client(live=live, persistence=FakePersistence()) as client:
        with client.websocket_connect(
            "/api/v1/voice/live", headers={"origin": "http://localhost:3000"}
        ) as ws:
            ws.send_json({"type": "HELLO", "protocol": 2, "token": "valid-jwt", "scope": "master"})
            assert recv(ws)["type"] == "READY"
    assert "progress tracker" in _system_text(live)
