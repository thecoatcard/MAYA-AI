"""Voice proposes Notion writes (approval-gated), knows the user's database names, stays honest.

Fake Notion (``httpx.MockTransport``), fake task submitter, fake live registry. Nothing reaches
Notion or a model.
"""

from __future__ import annotations

from types import SimpleNamespace
from typing import Any
from uuid import uuid4

import pytest

from app.mcp.notion_tools import NotionToolkit
from app.mcp.notion_write import NOTION_WRITE_CHAT_INSTRUCTION
from app.voice import task_digest
from app.voice.notion_voice import (
    VOICE_NOTION_WRITE_NAMES,
    database_names,
    names_rule,
    notion_write_voice_declarations,
    propose_by_voice,
)
from app.voice.prompts import build_voice_instruction
from app.voice.task_watch import Milestone, TaskAnnouncer, TaskFacts, render_milestone
from tests.support.notion_fake import DB, FakeNotionApi
from tests.unit.test_voice_task_digest import digest_of, row

pytestmark = pytest.mark.asyncio
WS, USER, SESSION = uuid4(), uuid4(), str(uuid4())


class Submitter:
    def __init__(self) -> None:
        self.calls: list[dict[str, Any]] = []
        self.by_key: dict[str, Any] = {}

    async def submit(self, **kwargs: Any) -> Any:
        key = kwargs["idempotency_key"]
        is_new = key not in self.by_key
        if is_new:
            self.calls.append(kwargs)
            self.by_key[key] = uuid4()
        return SimpleNamespace(task_id=self.by_key[key], is_new=is_new)


def fake_with_database() -> FakeNotionApi:
    fake = FakeNotionApi()
    fake.database_titles[DB] = "MAYA DB"
    return fake


async def propose(
    fake: FakeNotionApi, submitter: Submitter, args: dict[str, Any]
) -> dict[str, Any]:
    return await propose_by_voice(
        fake.toolkit(),
        submitter,
        name="notion_add_row",
        args=args,
        workspace_id=WS,
        user_id=USER,
        session_id=SESSION,
        scope="scope",
    )


async def test_a_voice_write_only_proposes_and_the_card_task_exists() -> None:
    fake, submitter = fake_with_database(), Submitter()
    result = await propose(fake, submitter, {"database_id": DB, "title": "Java 8 basics"})
    assert result["status"] == "waiting_for_approval"
    assert result["task_id"] == result["job_id"] and result["tool"] == "notion_add_row"
    assert fake.writes == []  # nothing reached Notion
    (call,) = submitter.calls
    (step,) = call["steps"]
    assert step.tool == "notion_add_row" and call["source"] == "voice"
    assert (
        "waits for their approval" in result["message"]
        and "Nothing has been written" in result["message"]
    )


async def test_asking_twice_returns_the_same_waiting_task_not_a_second_approval() -> None:
    fake, submitter = fake_with_database(), Submitter()
    first = await propose(fake, submitter, {"database_id": DB, "title": "Java 8 basics"})
    again = await propose(fake, submitter, {"database_id": DB, "title": "Java 8 basics"})
    assert again["status"] == "already_waiting" and again["job_id"] == first["job_id"]
    assert len(submitter.calls) == 1


async def test_a_bad_request_or_unavailable_submission_is_an_honest_error() -> None:
    fake, submitter = fake_with_database(), Submitter()
    bad = await propose(fake, submitter, {"database_id": "not-an-id", "title": "x"})
    assert bad["status"] == "error" and submitter.calls == []

    class Down:
        async def submit(self, **kwargs: Any) -> Any:
            raise RuntimeError("db down")

    down = await propose_by_voice(
        fake.toolkit(),
        Down(),
        name="notion_add_row",
        args={"database_id": DB, "title": "x"},
        workspace_id=uuid4(),
        user_id=uuid4(),
        session_id=None,
        scope=None,
    )
    assert down["status"] == "error" and "Nothing was written" in down["message"]


async def test_only_the_four_propose_tools_are_available_by_voice() -> None:
    fake, submitter = fake_with_database(), Submitter()
    refused = await propose_by_voice(
        fake.toolkit(),
        submitter,
        name="notion_replace_text",
        args={},
        workspace_id=uuid4(),
        user_id=uuid4(),
        session_id=None,
        scope=None,
    )
    assert refused["status"] == "error" and submitter.calls == []
    declared = [d["name"] for d in notion_write_voice_declarations()]
    assert sorted(declared) == sorted(VOICE_NOTION_WRITE_NAMES) and len(declared) == 4
    assert all("approve" not in name for name in declared)


async def test_the_instruction_has_write_rules_names_counts_and_chat_honesty() -> None:
    text = build_voice_instruction(
        memory_on=True,
        notion_on=True,
        notion_write_on=True,
        notion_names=["MAYA DB", "People", "Deep Note"],
    )
    assert "waiting_for_approval" in text and "Saying yes aloud cannot approve it" in text
    assert "never say it was written" in text  # wording changed: the system reports the state
    assert '"MAYA DB"; "People"; "Deep Note"' in text and "MariaDB" in text
    assert "ask which one" in text
    assert "plain numerals" in text and "posted_to_chat" in text
    read_only = build_voice_instruction(memory_on=False, notion_on=True)
    assert "notion_add_row" not in read_only


async def test_names_are_capped_cleaned_and_a_failure_gives_no_names() -> None:
    rule = names_rule([f"DB {i} <x>" for i in range(50)])
    assert rule.count('"DB ') == 20 and "<" not in rule
    assert names_rule([]) == ""

    class Boom:
        async def database_names(self, limit: int) -> list[str]:
            raise RuntimeError("no notion")

    assert await database_names(Boom()) == []


async def test_database_names_come_from_the_notion_search() -> None:
    class Connector:
        async def search_databases(self, query: str, limit: int) -> dict[str, Any]:
            return {
                "results": [
                    {"title": [{"plain_text": "MAYA DB"}]},
                    {"title": [{"plain_text": "People"}]},
                    {"title": []},
                ]
            }

    async def provider() -> tuple[Any, None]:
        return Connector(), None

    assert await NotionToolkit(provider).database_names(5) == ["MAYA DB", "People"]


# ---- memory after the approval result -------------------------------------------------------


def _facts(status: str) -> TaskFacts:
    rows = [
        row(1, status, seconds=10, tool="notion_add_row"),
    ]
    rows[0].description = "Notion: add a row 'Java 8 notes' to the database MAYA DB"
    return TaskFacts(
        "t1",
        uuid4(),
        "Java notes",
        "completed" if status == "completed" else "awaiting_approval",
        digest=digest_of(rows),
    )


async def test_memory_is_asked_for_only_after_the_note_was_really_written() -> None:
    done = render_milestone(Milestone.COMPLETED, _facts("completed"), memory_on=True)
    assert "MEMORY RULE" in done and "MAYA DB" in done and "save_memory" in done
    assert "never store the note's text" in done.lower().replace("never store", "never store")
    waiting = render_milestone(Milestone.APPROVAL, _facts("pending"), {}, memory_on=True)
    assert "MEMORY RULE" not in waiting  # never before the approval result is known
    off = render_milestone(Milestone.COMPLETED, _facts("completed"), memory_on=False)
    assert "MEMORY RULE" not in off
    assert task_digest.memory_rule(digest_of([row(1, "completed", seconds=1)]), "2026-11-12") == ""


async def test_the_announcer_passes_memory_on_to_the_completion_digest() -> None:
    spoken: list[str] = []

    async def speak(text: str) -> None:
        spoken.append(text)

    user = uuid4()
    facts = _facts("completed")

    async def resolve(ws: Any, u: Any, task_id: str) -> TaskFacts:
        return TaskFacts(task_id, user, facts.title, facts.status, digest=facts.digest)

    async def notify(frame: dict[str, Any]) -> None:
        return None

    from app.voice.task_watch import AnnouncedStore

    announcer = TaskAnnouncer(
        workspace_id=uuid4(),
        user_id=user,
        resolver=resolve,
        store=AnnouncedStore(),
        speak=speak,
        is_busy=lambda: False,
        notify=notify,
        poll_seconds=0.002,
        gap_seconds=0.0,
        settle_seconds=0.01,
        memory_on=True,
    )
    announcer.start()
    await announcer.on_event("task_completed", {"task_id": "t1"})
    import asyncio

    for _ in range(200):
        if spoken:
            break
        await asyncio.sleep(0.005)
    await announcer.close()
    assert spoken and "MEMORY RULE" in spoken[0]


async def test_chat_instruction_asks_for_a_where_it_lives_memory_not_the_body() -> None:
    text = NOTION_WRITE_CHAT_INSTRUCTION
    assert "save_memory" in text and "WHERE the note lives" in text
    assert "never save the note's text" in text and "before the approval result is known" in text


async def test_a_short_read_result_says_it_was_not_posted_to_the_chat() -> None:
    from app.voice.connection import VoiceConnection

    outcome = {"ok": True, "result": "3 database(s): MAYA DB, People, Deep Note"}
    connection: Any = VoiceConnection
    told = await connection._for_voice(SimpleNamespace(), "notion_list_databases", outcome)
    assert told["posted_to_chat"] is False and told["result"] == outcome["result"]
