"""show_in_chat and the live CHAT_MESSAGE frame: content MAYA composes reaches the chat at once.

No real Gemini, database or WebSocket: persistence is a recording fake and the session is a mock
that records the frames sent to the client.
"""

from typing import Any
from unittest.mock import AsyncMock

from app.voice import protocol
from app.voice.connection import CHAT_TOOL_TEXT_MAX_CHARS, VoiceConnection
from app.voice.prompts import build_voice_instruction
from app.voice.tools import SHOW_IN_CHAT, VoiceExecutionContext, VoiceToolRegistry
from tests.unit.test_voice_read_tools import (
    SESSION,
    USER,
    WS,
    FakeRepoTools,
    RecordingPersistence,
    make_connection,
)

CODE = "```python\ndef two_sum(nums, target):\n    return []\n```"


def _context() -> VoiceExecutionContext:
    return VoiceExecutionContext(workspace_id=WS, user_id=USER, session_id=str(SESSION))


def _wired(persistence: RecordingPersistence) -> tuple[VoiceConnection, AsyncMock]:
    connection = make_connection(None, persistence)
    session = AsyncMock()
    connection.session = session
    return connection, session.emit_control


def _chat_frames(emit: AsyncMock) -> list[dict[str, Any]]:
    return [c.args[0] for c in emit.await_args_list if c.args[0]["type"] == "CHAT_MESSAGE"]


def test_chat_message_frame_shape() -> None:
    assert protocol.chat_message("m1", "hi", "show_in_chat") == {
        "type": "CHAT_MESSAGE",
        "message_id": "m1",
        "role": "assistant",
        "content": "hi",
        "tool": "show_in_chat",
    }


async def test_declared_whenever_messages_can_be_stored() -> None:
    persistence = RecordingPersistence()
    persistence.engine = object()  # type: ignore[assignment]
    connection = make_connection(None, persistence)
    registry = VoiceToolRegistry()
    connection._enable_chat_tool(registry)
    names = {d["name"] for d in registry.get_tool_declarations()}
    assert SHOW_IN_CHAT in names and SHOW_IN_CHAT in registry.allowed_tool_names
    decl = next(d for d in registry.get_tool_declarations() if d["name"] == SHOW_IN_CHAT)
    assert decl["parameters"]["required"] == ["content"]
    assert "title" in decl["parameters"]["properties"]

    off = VoiceToolRegistry()
    make_connection(None, RecordingPersistence())._enable_chat_tool(off)  # no database
    assert SHOW_IN_CHAT not in {d["name"] for d in off.get_tool_declarations()}


async def test_tool_saves_the_message_and_emits_the_frame() -> None:
    persistence = RecordingPersistence()
    connection, emit = _wired(persistence)
    registry = VoiceToolRegistry()
    registry.set_chat_handler(connection._show_in_chat)

    result = await registry.execute_tool_async(SHOW_IN_CHAT, {"content": CODE}, _context())

    assert result["ok"] is True and result["posted_to_chat"] is True
    assert "Do not read it aloud" in result["message"]
    saved = persistence.calls[0]
    assert saved["role"] == "assistant" and saved["modality"] == "text"
    assert saved["content"] == CODE
    assert saved["metadata"] == {"source": "voice_tool", "tool": SHOW_IN_CHAT}
    frames = _chat_frames(emit)
    assert len(frames) == 1
    assert frames[0]["content"] == CODE and frames[0]["tool"] == SHOW_IN_CHAT
    assert frames[0]["role"] == "assistant" and frames[0]["message_id"]


async def test_title_becomes_a_heading() -> None:
    persistence = RecordingPersistence()
    connection, _ = _wired(persistence)
    await connection._show_in_chat({"content": "- a\n- b", "title": "Shortcuts"})
    assert persistence.calls[0]["content"].startswith("### Shortcuts\n\n- a")


async def test_empty_content_is_rejected_without_saving() -> None:
    persistence = RecordingPersistence()
    connection, emit = _wired(persistence)
    for args in ({}, {"content": ""}, {"content": "  \n "}):
        result = await connection._show_in_chat(args)
        assert result["ok"] is False
    assert persistence.calls == []
    assert _chat_frames(emit) == []


async def test_no_session_or_database_is_never_reported_as_success() -> None:
    persistence = RecordingPersistence()
    connection, emit = _wired(persistence)
    connection.session_id = None
    result = await connection._show_in_chat({"content": CODE})
    assert result["ok"] is False and "could not show it" in result["error"]
    assert "posted_to_chat" not in result

    for mode in ("no_database", "fail"):
        failing = RecordingPersistence(mode=mode)
        conn, emit2 = _wired(failing)
        out = await conn._show_in_chat({"content": CODE})
        assert out["ok"] is False
        assert _chat_frames(emit2) == []
    assert _chat_frames(emit) == []


async def test_content_is_capped() -> None:
    persistence = RecordingPersistence()
    connection, _ = _wired(persistence)
    await connection._show_in_chat({"content": "x" * (CHAT_TOOL_TEXT_MAX_CHARS + 500)})
    assert len(persistence.calls[0]["content"]) < CHAT_TOOL_TEXT_MAX_CHARS + 100
    assert "truncated" in persistence.calls[0]["content"]


async def test_post_to_chat_emits_the_frame_for_show_code_and_long_results() -> None:
    persistence = RecordingPersistence()
    connection = make_connection(FakeRepoTools(notion_connected=True), persistence)
    session = AsyncMock()
    connection.session = session
    assert await connection._post_to_chat("show_code", CODE) is True
    frames = _chat_frames(session.emit_control)
    assert len(frames) == 1 and frames[0]["tool"] == "show_code"
    assert frames[0]["content"] == CODE


async def test_post_to_chat_failure_sends_no_frame() -> None:
    connection, emit = _wired(RecordingPersistence(mode="no_database"))
    assert await connection._post_to_chat("show_code", CODE) is False
    assert _chat_frames(emit) == []


def test_prompt_rule_only_when_the_tool_is_on() -> None:
    assert "show_in_chat" not in build_voice_instruction(memory_on=False)
    text = build_voice_instruction(memory_on=False, chat_on=True)
    assert "show_in_chat" in text and "Never say you are putting" in text
