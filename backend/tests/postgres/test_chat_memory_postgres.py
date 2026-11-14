"""Chat memory against real PostgreSQL (row-level security) and real Redis (ADR-0018).

Only the Gemini client is fake: its embeddings are one constant vector, so every stored memory is
an exact match for every query (similarity 1.0). That is enough to prove the SQL, the scoping and
the whole chat path; the similarity thresholds themselves are covered with exact numbers in
``tests/unit/test_chat_memory.py``.
"""

from typing import Any
from uuid import UUID

import pytest
from sqlalchemy import text

from app.memory.chat_memory import STANDING_BLOCK_OPEN
from tests.postgres import test_chat_api_postgres as base
from tests.postgres.test_chat_api_postgres import Stack, events, names, onboard, post
from tests.support.settings import make_settings

stack = base.stack

pytestmark = [pytest.mark.postgres, pytest.mark.redis]


@pytest.fixture(autouse=True)
def tool_path_without_inline_router(stack: Stack) -> None:
    # Inline routing (MAYA_CHAT_INLINE_FILES, on by default) decides some prompts before the
    # model is asked, so the memory tools this module checks would not be offered. Inline
    # routing is covered in tests/integration/test_chat_inline_files.py.
    stack.app.state.settings = make_settings(redis_url=base.redis_url(), chat_inline_files=False)


SAVE = {"name": "save_memory", "args": {"text": "Favourite colour is blue", "kind": "preference"}}
SETTINGS = "/api/v1/settings/chat"
MEMORY = "/api/v1/memory"


def stream_calls(stack: Stack) -> list[dict[str, Any]]:
    return [c for c in stack.fake.aio.models.calls if c["method"] == "generate_content_stream"]


def embed_calls(stack: Stack) -> list[dict[str, Any]]:
    return [c for c in stack.fake.aio.models.calls if c["method"] == "embed_content"]


def declared(call: dict[str, Any]) -> list[str]:
    tools = call["config"].tools
    return [] if tools is None else [d.name for d in tools[0].function_declarations]


def last_turn(call: dict[str, Any]) -> list[str]:
    return [part["text"] for part in call["contents"][-1]["parts"]]


async def memory_rows(stack: Stack, workspace_id: UUID) -> list[Any]:
    async with stack.admin.connect() as connection:
        return list(
            await connection.execute(
                text(
                    "SELECT id, user_id, category, content, metadata, permanent, "
                    "embedding IS NOT NULL AS embedded FROM memory_items "
                    "WHERE workspace_id = :ws ORDER BY created_at"
                ),
                {"ws": workspace_id},
            )
        )


async def test_a_saved_memory_is_stored_scoped_embedded_and_recalled_in_a_new_chat(
    stack: Stack,
) -> None:
    alice = await onboard(stack)
    bob = await onboard(stack)

    # an empty memory costs no embedding call, and the tools are offered
    events(await post(stack, alice, "Hello MAYA"))
    assert embed_calls(stack) == []
    assert declared(stream_calls(stack)[0]) == ["submit_task", "save_memory", "forget_memory"]

    # chat 1: the model saves the memory; the event and the stored metadata carry its id
    stack.models.queued_stream_scripts = [[{"call": SAVE}], [{"text": "Saved."}]]
    frames = events(await post(stack, alice, "Yaad rakhna: mera favourite colour neela hai"))
    assert names(frames)[0] == "message.accepted" and names(frames)[-1] == "done"
    saved = next(f for f in frames if f.event == "memory.saved")
    assert saved.data["text"] == "Favourite colour is blue" and saved.data["updated"] is False
    (item,) = await memory_rows(stack, alice.workspace_id)
    assert str(item.id) == saved.data["memory_id"]
    assert item.user_id == alice.user_id
    assert (item.category, item.permanent, item.embedded) == ("preference", True, True)
    assert item.metadata == {"source": "chat", "kind": "preference"}
    stored = [
        r.metadata
        for r in await base.rows(stack, alice.workspace_id, "orchestrator")
        if "memories_saved" in r.metadata
    ]
    assert [m["memory_id"] for m in stored[0]["memories_saved"]] == [saved.data["memory_id"]]

    # the stored reply exposes the chip data when the conversation is read back
    session_id = frames[0].data["session_id"]
    listed = await stack.client.get(
        f"{base.CHAT}/sessions/{session_id}/messages", headers=alice.headers
    )
    reply = next(m for m in listed.json()["items"] if m["role"] == "assistant")
    assert reply["metadata"]["memories_saved"] == [saved.data]

    # chat 2, a NEW session: the user's message is embedded and the memory is injected as data
    before = len(stream_calls(stack))
    events(await post(stack, alice, "what is my fav color?"))
    call = stream_calls(stack)[before]
    # A preference-like memory is now a standing "About the user" block in the SYSTEM text (framed
    # as data), and a recalled memory that is already in it is not repeated in the user's turn.
    system_text = str(call["config"].system_instruction)
    assert STANDING_BLOCK_OPEN in system_text and "- Favourite colour is blue" in system_text
    assert last_turn(call) == ["what is my fav color?"]

    # another user (another workspace) neither sees it nor causes an embedding call
    count = len(embed_calls(stack))
    other = len(stream_calls(stack))
    events(await post(stack, bob, "what is my fav color?"))
    assert len(embed_calls(stack)) == count
    assert last_turn(stream_calls(stack)[other]) == ["what is my fav color?"]


async def test_saving_the_same_thing_again_updates_instead_of_duplicating(stack: Stack) -> None:
    alice = await onboard(stack)
    stack.models.queued_stream_scripts = [[{"call": SAVE}], [{"text": "Saved."}]]
    events(await post(stack, alice, "Remember my colour"))
    again = {"name": "save_memory", "args": {"text": "My favourite colour is now blue"}}
    stack.models.queued_stream_scripts = [[{"call": again}], [{"text": "Updated."}]]
    frames = events(await post(stack, alice, "Remember my colour again"))
    assert next(f for f in frames if f.event == "memory.saved").data["updated"] is True
    (item,) = await memory_rows(stack, alice.workspace_id)
    assert item.content == "My favourite colour is now blue"


async def test_a_credential_is_never_stored_or_embedded(stack: Stack) -> None:
    alice = await onboard(stack)
    secret = {"name": "save_memory", "args": {"text": "my password is hunter2xyz"}}
    stack.models.queued_stream_scripts = [[{"call": secret}], [{"text": "I do not store that."}]]
    frames = events(await post(stack, alice, "Remember my password is hunter2xyz"))
    assert "memory.saved" not in names(frames)
    assert await memory_rows(stack, alice.workspace_id) == []
    assert embed_calls(stack) == []
    response = stream_calls(stack)[1]["contents"][-1]["parts"][0]["function_response"]["response"]
    assert response["status"] == "refused"


async def test_undo_deletes_the_memory_through_the_memory_api_and_it_is_not_recalled(
    stack: Stack,
) -> None:
    alice = await onboard(stack)
    bob = await onboard(stack)
    stack.models.queued_stream_scripts = [[{"call": SAVE}], [{"text": "Saved."}]]
    frames = events(await post(stack, alice, "Remember my colour"))
    memory_id = next(f for f in frames if f.event == "memory.saved").data["memory_id"]

    # another user cannot delete it
    denied = await stack.client.delete(f"{MEMORY}/items/{memory_id}", headers=bob.headers)
    assert denied.status_code == 404
    assert len(await memory_rows(stack, alice.workspace_id)) == 1

    # the owner can (the chip's Undo)
    done = await stack.client.delete(f"{MEMORY}/items/{memory_id}", headers=alice.headers)
    assert done.status_code == 200
    assert await memory_rows(stack, alice.workspace_id) == []
    before = len(embed_calls(stack))
    events(await post(stack, alice, "what is my fav color?"))
    assert len(embed_calls(stack)) == before  # empty again: no embedding call


async def test_forget_memory_deletes_the_matching_memory(stack: Stack) -> None:
    alice = await onboard(stack)
    stack.models.queued_stream_scripts = [[{"call": SAVE}], [{"text": "Saved."}]]
    events(await post(stack, alice, "Remember my colour"))
    forget = {"name": "forget_memory", "args": {"query": "favourite colour is blue"}}
    stack.models.queued_stream_scripts = [[{"call": forget}], [{"text": "Forgot it."}]]
    frames = events(await post(stack, alice, "Forget that I like blue"))
    assert names(frames)[-1] == "done"
    assert await memory_rows(stack, alice.workspace_id) == []
    response = stream_calls(stack)[-1]["contents"][-1]["parts"][0]["function_response"]["response"]
    assert response["status"] == "deleted"
    assert response["deleted_text"] == "Favourite colour is blue"


async def test_the_setting_defaults_on_round_trips_and_turns_chat_memory_off(
    stack: Stack,
) -> None:
    alice = await onboard(stack)
    first = await stack.client.get(SETTINGS, headers=alice.headers)
    assert first.json()["memory_in_chat"] is True
    off = await stack.client.put(SETTINGS, json={"memory_in_chat": False}, headers=alice.headers)
    assert off.status_code == 200 and off.json()["memory_in_chat"] is False
    assert off.json()["intent_classifier"] is False  # the other setting is untouched
    assert (await stack.client.get(SETTINGS, headers=alice.headers)).json()["memory_in_chat"] is (
        False
    )

    # off: no tools about memory, no recall, no embedding
    events(await post(stack, alice, "Remember my colour is blue"))
    call = stream_calls(stack)[0]
    assert declared(call) == ["submit_task"]
    assert "save_memory" not in str(call["config"].system_instruction)
    assert embed_calls(stack) == []

    on = await stack.client.put(SETTINGS, json={"memory_in_chat": True}, headers=alice.headers)
    assert on.json()["memory_in_chat"] is True
    events(await post(stack, alice, "Hello again"))
    assert declared(stream_calls(stack)[1]) == ["submit_task", "save_memory", "forget_memory"]


async def test_the_memory_setting_is_private_to_the_user(stack: Stack) -> None:
    alice = await onboard(stack)
    bob = await onboard(stack)
    await stack.client.put(SETTINGS, json={"memory_in_chat": False}, headers=alice.headers)
    assert (await stack.client.get(SETTINGS, headers=bob.headers)).json()["memory_in_chat"] is True


async def test_an_empty_settings_update_is_rejected(stack: Stack) -> None:
    alice = await onboard(stack)
    for body in ({}, {"memory_in_chat": "banana"}, {"memory_in_chat": True, "role": "admin"}):
        response = await stack.client.put(SETTINGS, json=body, headers=alice.headers)
        assert response.status_code == 422
