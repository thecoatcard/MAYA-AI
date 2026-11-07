"""Unit tests for LiveVoiceSession lifecycle, audio flow, and tool calling."""

from typing import Any
from uuid import uuid4

import pytest

from app.voice.live_session import (
    LiveVoiceSession,
    OutgoingMessageType,
)
from app.voice.tools import VoiceToolRegistry


@pytest.mark.asyncio
async def test_session_init_and_properties() -> None:
    ws_id = uuid4()
    u_id = uuid4()
    session = LiveVoiceSession(workspace_id=ws_id, user_id=u_id)

    assert session.workspace_id == ws_id
    assert session.user_id == u_id
    assert session.session_id is not None
    assert session.is_speaking is False
    assert session.is_listening is True


@pytest.mark.asyncio
async def test_client_audio_input_and_model_audio_output() -> None:
    session = LiveVoiceSession(workspace_id=uuid4(), user_id=uuid4())

    # Send client 16 kHz audio chunk (100 ms = 3200 bytes)
    client_chunk = b"\x00\x10" * 1600
    await session.process_client_audio(client_chunk)
    assert session.total_client_bytes_received == 3200

    # Model generates 24 kHz audio chunk (100 ms = 4800 bytes)
    model_chunk = b"\x00\x20" * 2400
    await session.enqueue_model_audio(model_chunk)

    msg = await session.get_next_outgoing_message()
    assert msg.msg_type == OutgoingMessageType.AUDIO
    assert msg.audio_bytes == model_chunk


@pytest.mark.asyncio
async def test_transcripts_emission() -> None:
    session = LiveVoiceSession(workspace_id=uuid4(), user_id=uuid4())

    await session.emit_transcript_user("What is the status?")
    user_msg = await session.get_next_outgoing_message()
    assert user_msg.payload == {"type": "TRANSCRIPT_USER", "text": "What is the status?"}

    await session.emit_transcript_model("All tasks are operating normally.")
    model_msg = await session.get_next_outgoing_message()
    assert model_msg.payload == {
        "type": "TRANSCRIPT_MODEL",
        "text": "All tasks are operating normally.",
    }


@pytest.mark.asyncio
async def test_client_vad_control_messages() -> None:
    session = LiveVoiceSession(workspace_id=uuid4(), user_id=uuid4())

    # Speech start during playback should trigger interrupt
    await session.enqueue_model_audio(b"\x00\x10" * 100)
    assert session.pending_audio_chunks_count == 1

    await session.handle_client_control_message({"type": "VAD_SPEECH_START"})
    assert session.pending_audio_chunks_count == 0

    interrupt_msg = await session.get_next_outgoing_message()
    assert interrupt_msg.payload == {"type": "AUDIO_INTERRUPT"}

    # Speech stop
    await session.handle_client_control_message({"type": "VAD_SPEECH_STOP"})
    assert session.is_interrupted is False


@pytest.mark.asyncio
async def test_voice_session_executes_submit_task_tool() -> None:
    registry = VoiceToolRegistry()
    submitted_intent = ""

    async def custom_submitter(intent: str, params: dict[str, Any], ctx: Any) -> dict[str, Any]:
        nonlocal submitted_intent
        submitted_intent = intent
        return {"task_id": "task-test-123", "status": "pending"}

    registry.set_task_submitter(custom_submitter)
    session = LiveVoiceSession(workspace_id=uuid4(), user_id=uuid4(), tool_registry=registry)

    result = await session.execute_tool("submit_task", {"intent": "Run security check"})
    assert submitted_intent == "Run security check"
    assert result["task_id"] == "task-test-123"
