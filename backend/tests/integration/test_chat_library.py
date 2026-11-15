"""The right-click menus' backend: rename, pin, archive, duplicate, export, message feedback and
"this message" as the source of a file, a Notion save or an email.

Everything runs through the in-memory chat harness (no model, no network, no database): the flags
live in a fake per-user settings store with the same compare-and-set contract as the real one.
"""

from __future__ import annotations

import json
from collections.abc import AsyncIterator
from pathlib import Path
from typing import Any
from uuid import UUID, uuid4

import httpx
import pytest

from app.services import chat_library
from app.services.notion_source import NOTION_TOOL
from tests.integration.test_chat_inline_files import IdempotentSubmitter
from tests.integration.test_chat_phase2_tools import wire
from tests.integration.test_chat_routing_regressions import WorkspaceNotionRepo
from tests.integration.test_chat_streaming import (
    CHAT,
    REPLY,
    Account,
    World,
    events,
    make_world,
    send,
    sign_in,
)
from tests.support.notion_fake import FakeNotionApi

PAGE = "11112222333344445555666677778888"
NOTES = (
    "# Java Streams\n\nA stream is a pipeline of aggregate operations over a source. It does "
    "not store data. Intermediate operations such as filter and map are lazy; a terminal "
    "operation such as collect starts the work.\n\n## Operations\n\n- filter\n- map\n- collect\n"
)
LATER = "# Something Else\n\n" + "A different, later and much longer answer. " * 12


class SettingsFake:
    """The per-user settings store: text values by (workspace, user, key), compare-and-set."""

    def __init__(self) -> None:
        self.values: dict[tuple[UUID, UUID, str], str] = {}

    async def intent_classifier_enabled(self, workspace_id: UUID, user_id: UUID) -> bool:
        return False

    async def memory_in_chat_enabled(self, workspace_id: UUID, user_id: UUID) -> bool:
        return False

    async def get_text(self, workspace_id: UUID, user_id: UUID, key: str) -> str | None:
        return self.values.get((workspace_id, user_id, key))

    async def compare_and_set_text(
        self, workspace_id: UUID, user_id: UUID, key: str, expected: str | None, value: str
    ) -> bool:
        slot = (workspace_id, user_id, key)
        if self.values.get(slot) != expected:
            return False
        self.values[slot] = value
        return True

    def doc(self, account: Account, key: str) -> dict[str, Any]:
        raw = self.values.get((account.workspace_id, account.user_id, key))
        return json.loads(raw) if raw else {}


@pytest.fixture
async def world(tmp_path: Path) -> AsyncIterator[World]:
    api = FakeNotionApi()
    api.pages[PAGE] = {
        "object": "page",
        "id": PAGE,
        "url": "https://notion.so/ai-notes",
        "parent": {"type": "workspace"},
        "properties": {"title": {"type": "title", "title": [{"plain_text": "Ai NOTES"}]}},
    }
    api.children[PAGE] = []
    async with make_world(artifacts_dir=tmp_path / "artifacts", chat_tool_loop=True) as value:
        value.submitter = IdempotentSubmitter()  # type: ignore[attr-defined]
        value.settings_fake = SettingsFake()  # type: ignore[attr-defined]
        wire(
            value,
            submitter=value.submitter,  # type: ignore[attr-defined]
            repo_tools=WorkspaceNotionRepo(api),
            prefs=value.settings_fake,  # type: ignore[attr-defined]
        )
        yield value


def flags_store(world: World) -> SettingsFake:
    return world.settings_fake  # type: ignore[attr-defined, no-any-return]


async def chat(world: World, account: Account, text: str, **kw: Any) -> tuple[UUID, UUID]:
    """One finished turn: ``(session id, assistant message id)``."""
    frames = events(await send(world, account, text, **kw))
    data = frames[0].data
    return UUID(data["session_id"]), UUID(data["assistant_message_id"])


async def listing(world: World, account: Account, query: str = "") -> dict[str, Any]:
    response = await world.client.get(f"{CHAT}/sessions{query}", headers=account.headers)
    assert response.status_code == 200, response.text
    return response.json()  # type: ignore[no-any-return]


def ids(page: dict[str, Any]) -> list[str]:
    return [item["id"] for item in page["items"]]


async def patch(
    world: World, account: Account, session_id: UUID | str, body: dict[str, Any]
) -> httpx.Response:
    return await world.client.patch(
        f"{CHAT}/sessions/{session_id}", json=body, headers=account.headers
    )


# --------------------------------------------------------------- rename, pin, archive


async def test_pin_archive_filter_and_order(world: World) -> None:
    account = await sign_in(world)
    one, _ = await chat(world, account, "first chat")
    two, _ = await chat(world, account, "second chat")
    three, _ = await chat(world, account, "third chat")
    page = await listing(world, account)
    assert ids(page) == [str(three), str(two), str(one)]
    assert all(i["pinned"] is False and i["archived"] is False for i in page["items"])

    pinned = await patch(world, account, one, {"pinned": True})
    assert pinned.status_code == 200
    assert pinned.json()["pinned"] is True and pinned.json()["archived"] is False
    assert set(pinned.json()) == {
        "id",
        "title",
        "channel",
        "status",
        "created_at",
        "pinned",
        "archived",
    }
    assert ids(await listing(world, account)) == [str(one), str(three), str(two)]  # pinned first

    await patch(world, account, three, {"pinned": True})  # pinned later -> before the older pin
    assert ids(await listing(world, account)) == [str(three), str(one), str(two)]

    assert (await patch(world, account, two, {"archived": True})).json()["archived"] is True
    assert ids(await listing(world, account)) == [str(three), str(one)]  # default hides archived
    assert ids(await listing(world, account, "?archived=true")) == [str(two)]
    assert ids(await listing(world, account, "?archived=all")) == [str(three), str(one), str(two)]

    # paging over the ordered list keeps the order and ends cleanly
    first = await listing(world, account, "?archived=all&limit=2")
    assert ids(first) == [str(three), str(one)] and first["next_cursor"]
    rest = await listing(world, account, f"?archived=all&limit=2&cursor={first['next_cursor']}")
    assert ids(rest) == [str(two)] and rest["next_cursor"] is None

    await patch(world, account, two, {"archived": False})
    await patch(world, account, one, {"pinned": False})
    await patch(world, account, three, {"pinned": False})
    assert ids(await listing(world, account)) == [str(three), str(two), str(one)]
    # nothing set any more: the entries are gone, not left as empty rows
    assert flags_store(world).doc(account, chat_library.SESSION_FLAGS_KEY) == {}


async def test_rename_changes_the_title_the_list_shows_and_locks_it(world: World) -> None:
    account = await sign_in(world)
    sid, _ = await chat(world, account, "hello there")
    renamed = await patch(world, account, sid, {"title": "  Q3   plan \n review  "})
    assert renamed.status_code == 200 and renamed.json()["title"] == "Q3 plan review"
    assert (await listing(world, account))["items"][0]["title"] == "Q3 plan review"
    flags = flags_store(world).doc(account, chat_library.SESSION_FLAGS_KEY)
    assert flags[str(sid)] == {"title_locked": True}
    assert chat_library.title_locked(flags, str(sid)) is True
    # a later message in the session does not touch the user's title (nothing else sets titles)
    await chat(world, account, "another turn", session_id=sid)
    assert (await listing(world, account))["items"][0]["title"] == "Q3 plan review"


async def test_patch_rejects_bad_bodies_and_foreign_sessions(world: World) -> None:
    account = await sign_in(world)
    other = await sign_in(world)
    sid, _ = await chat(world, account, "mine")
    for body in (
        {},
        {"title": ""},
        {"title": "   "},
        {"title": "x" * 121},
        {"title": "bad\x00title"},
        {"title": "bell\x07"},
        {"title": 5},
        {"pinned": "yes"},
        {"archived": None, "pinned": None},
        {"unknown": True},
    ):
        response = await patch(world, account, sid, body)
        assert response.status_code == 422, body
        assert response.json()["error"] == "validation_error"
    assert (await patch(world, account, sid, {"title": "x" * 120})).status_code == 200
    # another user's id and a random id both read as not found; nothing was changed
    assert (await patch(world, other, sid, {"pinned": True})).status_code == 404
    assert (await patch(world, other, sid, {"title": "stolen"})).status_code == 404
    assert (await patch(world, account, uuid4(), {"pinned": True})).status_code == 404
    assert flags_store(world).doc(other, chat_library.SESSION_FLAGS_KEY) == {}
    unauth = await world.client.patch(f"{CHAT}/sessions/{sid}", json={"pinned": True})
    assert unauth.status_code == 401


async def test_flags_are_per_user_and_deleting_a_session_removes_them(world: World) -> None:
    account = await sign_in(world)
    other = await sign_in(world)
    sid, mid = await chat(world, account, "to be removed")
    keep, _ = await chat(world, account, "kept")
    await patch(world, account, sid, {"pinned": True, "title": "Pinned one"})
    await patch(world, account, keep, {"archived": True})
    assert (
        await world.client.post(
            f"{CHAT}/messages/{mid}/feedback", json={"value": "up"}, headers=account.headers
        )
    ).status_code == 204
    assert (await listing(world, other))["items"] == []
    assert ids(await listing(world, account, "?archived=all"))[0] == str(sid)

    deleted = await world.client.delete(f"{CHAT}/sessions/{sid}", headers=account.headers)
    assert deleted.status_code == 204
    flags = flags_store(world).doc(account, chat_library.SESSION_FLAGS_KEY)
    assert str(sid) not in flags and str(keep) in flags
    assert flags_store(world).doc(account, chat_library.FEEDBACK_KEY) == {}


async def test_listing_prunes_flags_of_sessions_that_are_gone(world: World) -> None:
    account = await sign_in(world)
    sid, _ = await chat(world, account, "ghost")
    await patch(world, account, sid, {"pinned": True})
    del world.chat.sessions[sid]  # removed behind the API's back (for example by a cascade)
    assert (await listing(world, account))["items"] == []
    assert flags_store(world).doc(account, chat_library.SESSION_FLAGS_KEY) == {}


# ------------------------------------------------------------------------------ duplicate


async def test_duplicate_copies_plain_turns_up_to_the_chosen_message(world: World) -> None:
    account = await sign_in(world)
    sid, first_reply = await chat(world, account, "question one")
    await chat(world, account, "question two", session_id=sid)
    # decorate the first reply with cards and files: none of it may be copied
    for message in world.chat.messages:
        if message.id == first_reply:
            message.metadata.update(
                {
                    "task": {"task_id": str(uuid4()), "title": "T", "status": "queued"},
                    "files_created": [{"name": "a.pdf"}],
                    "progress": [{"id": "x"}],
                    "usage": {"total_tokens": 9},
                    "api_secret": "ZXQ-secret",
                }
            )
    await patch(world, account, sid, {"title": "Roadmap"})

    full = await world.client.post(
        f"{CHAT}/sessions/{sid}/duplicate", json={}, headers=account.headers
    )
    assert full.status_code == 201
    copy = full.json()
    assert copy["title"] == "Roadmap (copy)" and copy["id"] != str(sid)
    assert copy["pinned"] is False and copy["archived"] is False
    msgs = world.chat.messages
    copied = [m for m in msgs if str(m.session_id) == copy["id"]]
    assert [(m.sender, m.content) for m in copied] == [
        (m.sender, m.content) for m in msgs if m.session_id == sid
    ]
    assert all(m.metadata == {} and m.modality == "text" for m in copied)
    assert "ZXQ-secret" not in json.dumps([m.metadata for m in copied])

    partial = await world.client.post(
        f"{CHAT}/sessions/{sid}/duplicate",
        json={"from_message_id": str(first_reply)},
        headers=account.headers,
    )
    assert partial.status_code == 201
    got = [m for m in world.chat.messages if str(m.session_id) == partial.json()["id"]]
    assert [m.sender for m in got] == ["user", "orchestrator"] and got[0].content == "question one"
    # the original is untouched and the copy shows up in the list
    assert len([m for m in world.chat.messages if m.session_id == sid]) == 4
    assert partial.json()["id"] in ids(await listing(world, account))

    nobody = await world.client.post(f"{CHAT}/sessions/{sid}/duplicate", headers=account.headers)
    assert nobody.status_code == 201  # the body is optional


async def test_duplicate_is_idempotent_per_key_and_rejects_foreign_ids(world: World) -> None:
    account = await sign_in(world)
    other = await sign_in(world)
    sid, _ = await chat(world, account, "branch me")
    headers = {**account.headers, "Idempotency-Key": "dup-key-0001"}
    one = await world.client.post(f"{CHAT}/sessions/{sid}/duplicate", json={}, headers=headers)
    two = await world.client.post(f"{CHAT}/sessions/{sid}/duplicate", json={}, headers=headers)
    assert one.status_code == two.status_code == 201 and one.json()["id"] == two.json()["id"]
    assert len(world.chat.sessions) == 2
    bad_key = {**account.headers, "Idempotency-Key": "short"}
    rejected = await world.client.post(f"{CHAT}/sessions/{sid}/duplicate", headers=bad_key)
    assert rejected.status_code == 400 and rejected.json()["error"] == "idempotency_key_required"

    assert (
        await world.client.post(f"{CHAT}/sessions/{sid}/duplicate", headers=other.headers)
    ).status_code == 404
    elsewhere, other_reply = await chat(world, other, "theirs")
    wrong = await world.client.post(
        f"{CHAT}/sessions/{sid}/duplicate",
        json={"from_message_id": str(other_reply)},
        headers=account.headers,
    )
    assert wrong.status_code == 422 and wrong.json()["fields"] == ["from_message_id"]
    assert elsewhere not in [UUID(s["id"]) for s in (await listing(world, account))["items"]]


# --------------------------------------------------------------------------------- export


async def test_export_is_markdown_with_a_safe_filename_and_no_noise(world: World) -> None:
    account = await sign_in(world)
    other = await sign_in(world)
    sid, reply = await chat(world, account, "Explain **streams**")
    await patch(world, account, sid, {"title": 'Q3/plan: "final"?..\\x'})
    for message in world.chat.messages:
        if message.id == reply:
            message.metadata.update(
                {
                    "files_created": [{"name": "notes.pdf", "file_id": str(uuid4())}],
                    "progress": [{"id": "s1", "label": "Searching ZXQ-noise"}],
                    "usage": {"total_tokens": 99},
                    "model_id": "ZXQ-model",
                }
            )
        if message.session_id == sid and message.sender == "user":
            message.metadata["attachments"] = [{"name": "input.docx", "id": "abc"}]
    response = await world.client.get(f"{CHAT}/sessions/{sid}/export", headers=account.headers)
    assert response.status_code == 200
    assert response.headers["content-type"].startswith("text/markdown")
    disposition = response.headers["content-disposition"]
    assert disposition == 'attachment; filename="Q3-plan-final-x.md"'
    assert response.headers["cache-control"] == "no-store"
    body = response.text
    assert body.startswith('# Q3/plan: "final"?..\\x\n')
    assert "## You - " in body and "## MAYA - " in body and " UTC" in body
    assert "Explain **streams**" in body and REPLY in body
    assert "Attached: input.docx" in body and "Attached: notes.pdf" in body
    assert "ZXQ-noise" not in body and "ZXQ-model" not in body and "total_tokens" not in body
    assert body.index("## You") < body.index("## MAYA")

    assert (
        await world.client.get(f"{CHAT}/sessions/{sid}/export", headers=other.headers)
    ).status_code == 404
    bad = await world.client.get(
        f"{CHAT}/sessions/{sid}/export?format=pdf", headers=account.headers
    )
    assert bad.status_code == 422


def test_filenames_never_carry_path_or_header_characters() -> None:
    for title in ("../../etc/passwd", 'a"b\r\nSet-Cookie: x', "नमस्ते", "", None, "C:\\x\\y"):
        name = chat_library.export_filename(title)
        assert name.endswith(".md") and len(name) <= 64
        assert all(c.isascii() and c not in '"\\/\r\n:' for c in name)
    assert chat_library.export_filename("नमस्ते") == "chat.md"


# -------------------------------------------------------------------------------- feedback


async def test_feedback_set_clear_and_shown_on_the_message_list(world: World) -> None:
    account = await sign_in(world)
    other = await sign_in(world)
    sid, reply = await chat(world, account, "rate me")
    url = f"{CHAT}/messages/{reply}/feedback"

    async def messages(who: Account) -> list[dict[str, Any]]:
        got = await world.client.get(f"{CHAT}/sessions/{sid}/messages", headers=who.headers)
        return got.json()["items"]  # type: ignore[no-any-return]

    assert [m["feedback"] for m in await messages(account)] == [None, None]
    up = await world.client.post(url, json={"value": "up"}, headers=account.headers)
    assert up.status_code == 204 and up.content == b""
    assert [m["feedback"] for m in await messages(account)] == [None, "up"]
    down = await world.client.post(
        url, json={"value": "down", "note": "too long"}, headers=account.headers
    )
    assert down.status_code == 204
    assert [m["feedback"] for m in await messages(account)] == [None, "down"]
    entry = flags_store(world).doc(account, chat_library.FEEDBACK_KEY)[str(reply)]
    assert entry["value"] == "down" and entry["note"] == "too long"
    assert entry["session_id"] == str(sid) and "ts" in entry
    assert REPLY not in json.dumps(entry)  # never the message text
    state = await world.client.get(
        f"{CHAT}/sessions/{sid}/messages/{reply}", headers=account.headers
    )
    assert state.json()["message"]["feedback"] == "down"

    # another user can neither rate nor see it
    assert (
        await world.client.post(url, json={"value": "up"}, headers=other.headers)
    ).status_code == 404
    assert world.chat.sessions  # sanity: the session still exists for the owner

    cleared = await world.client.post(url, json={"value": None}, headers=account.headers)
    assert cleared.status_code == 204
    assert [m["feedback"] for m in await messages(account)] == [None, None]
    assert flags_store(world).doc(account, chat_library.FEEDBACK_KEY) == {}


async def test_feedback_validation(world: World) -> None:
    account = await sign_in(world)
    sid, reply = await chat(world, account, "rate me")
    user_message = next(m.id for m in world.chat.messages if m.sender == "user")
    url = f"{CHAT}/messages/{reply}/feedback"
    for body in ({}, {"value": "meh"}, {"value": "up", "note": "x" * 501}, {"value": "up", "x": 1}):
        assert (
            await world.client.post(url, json=body, headers=account.headers)
        ).status_code == 422, body
    assert (
        await world.client.post(
            url, json={"value": "up", "note": "x" * 500}, headers=account.headers
        )
    ).status_code == 204
    # a user message and an unknown id are not rateable
    for target in (user_message, uuid4()):
        response = await world.client.post(
            f"{CHAT}/messages/{target}/feedback", json={"value": "up"}, headers=account.headers
        )
        assert response.status_code == 404 and response.json() == {"error": "message_not_found"}
    assert sid  # the session is the caller's


def test_feedback_is_capped_to_the_newest_entries() -> None:
    doc: dict[str, Any] = {}
    for index in range(chat_library.MAX_FEEDBACK + 50):
        chat_library.set_feedback(
            doc,
            f"m{index}",
            value="up",
            note=None,
            session_id="s",
            model_id=None,
            ts=f"2026-11-13T00:{index // 60:02d}:{index % 60:02d}+00:00",
        )
    chat_library.trim_feedback(doc)
    assert len(doc) == chat_library.MAX_FEEDBACK
    assert "m0" not in doc and f"m{chat_library.MAX_FEEDBACK + 49}" in doc


def test_flags_are_capped_and_pinned_ones_survive() -> None:
    doc: dict[str, Any] = {
        f"s{i}": {"archived": True} for i in range(chat_library.MAX_SESSION_FLAGS)
    }
    chat_library.apply_session_patch(
        doc, "keep", pinned=True, archived=None, lock_title=False, now="2026-11-13T00:00:00+00:00"
    )
    chat_library.trim_flags(doc)
    assert len(doc) == chat_library.MAX_SESSION_FLAGS and "keep" in doc


# --------------------------------------------------------------- this message as the source


async def test_notion_as_is_from_a_chosen_older_message(world: World) -> None:
    account = await sign_in(world)
    world.models.queued_stream_scripts = [[{"text": NOTES}], [{"text": LATER}]]
    sid, notes_id = await chat(world, account, "explain java streams")
    await chat(world, account, "now something else", session_id=sid)
    world.models.queued_stream_scripts = []
    url = "https://www.notion.so/Ai-NOTES-" + PAGE
    frames = events(
        await send(
            world,
            account,
            f"save this in notion {url} as it is",
            session_id=sid,
            source_message_id=str(notes_id),
        )
    )
    assert [f for f in frames if f.event == "input.requested"] == []
    (call,) = world.submitter.submitted  # type: ignore[attr-defined]
    hints = call["steps"][0].input_data["notion_book"]
    assert call["steps"][0].tool == NOTION_TOOL
    assert hints["inline_markdown"] == NOTES.strip()  # THAT message, not the newest long one
    assert hints["as_is"] is True and hints["parent_page_id"] == PAGE


async def test_a_short_message_is_still_the_source_when_it_is_named(world: World) -> None:
    account = await sign_in(world)
    sid, short_id = await chat(world, account, "say hi")  # a short reply, normally "not notes"
    url = "https://www.notion.so/Ai-NOTES-" + PAGE
    events(
        await send(
            world,
            account,
            f"save this in notion {url} as it is",
            session_id=sid,
            source_message_id=str(short_id),
        )
    )
    (call,) = world.submitter.submitted  # type: ignore[attr-defined]
    assert call["steps"][0].input_data["notion_book"]["inline_markdown"] == REPLY


async def test_a_file_request_tells_the_model_which_message_is_meant(world: World) -> None:
    account = await sign_in(world)
    world.models.queued_stream_scripts = [[{"text": NOTES}]]
    sid, notes_id = await chat(world, account, "explain java streams")
    await chat(world, account, "unrelated", session_id=sid)
    start = len(world.models.calls)
    events(
        await send(
            world,
            account,
            "make this a PDF",
            session_id=sid,
            source_message_id=str(notes_id),
        )
    )
    calls = world.models.calls[start:]
    system = "\n".join(str(c["config"].system_instruction) for c in calls)
    assert "<<<MESSAGE" in system and "A stream is a pipeline" in system
    assert "as it is" in system and "no rewriting" in system
    # a normal turn without the id never carries it
    start = len(world.models.calls)
    events(await send(world, account, "thanks", session_id=sid))
    assert "<<<MESSAGE" not in "\n".join(
        str(c["config"].system_instruction) for c in world.models.calls[start:]
    )


async def test_source_message_must_be_an_assistant_message_of_the_same_own_session(
    world: World,
) -> None:
    account = await sign_in(world)
    other = await sign_in(world)
    sid, reply = await chat(world, account, "mine")
    elsewhere, elsewhere_reply = await chat(world, account, "another chat")
    theirs, their_reply = await chat(world, other, "theirs")
    user_message = next(m.id for m in world.chat.messages if m.sender == "user")

    async def attempt(who: Account, session: UUID | None, message: UUID | str) -> httpx.Response:
        return await send(
            world, who, "make this a PDF", session_id=session, source_message_id=str(message)
        )

    # a message of another session of the same user, a user message, an unknown id, no session
    for message in (elsewhere_reply, user_message, uuid4()):
        response = await attempt(account, sid, message)
        assert response.status_code == 422
        assert response.json() == {"error": "validation_error", "fields": ["source_message_id"]}
    assert (await attempt(account, None, reply)).status_code == 422
    # another user's session and message: the session is not theirs to use
    assert (await attempt(account, theirs, their_reply)).status_code == 404
    assert (await attempt(other, sid, reply)).status_code == 404
    assert (await send(world, account, "x", source_message_id="not-a-uuid")).status_code == 422
    assert elsewhere and sid
