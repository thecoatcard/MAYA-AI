"""Unit tests for Sub-150 ms Barge-In Interruption Protocol (Decision D12)."""

import time
from uuid import uuid4

import pytest

from app.voice.live_session import LiveVoiceSession, OutgoingMessageType


@pytest.mark.asyncio
async def test_barge_in_buffer_flushed_instantly() -> None:
    session = LiveVoiceSession(workspace_id=uuid4(), user_id=uuid4())

    # Queue some model audio chunks
    dummy_audio_chunk = b"\x00\x10" * 1200  # 24 kHz 100 ms chunk
    for _ in range(5):
        await session.enqueue_model_audio(dummy_audio_chunk)

    assert session.pending_audio_chunks_count == 5
    assert session.is_speaking is True

    # Interruption triggered
    start_time = time.perf_counter()
    await session.handle_interrupt(source="client_vad")
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    # Decision D12: latency must be <= 150 ms
    assert elapsed_ms < 150.0

    # Outgoing audio queue must be completely flushed
    assert session.pending_audio_chunks_count == 0
    assert session.is_speaking is False
    assert session.is_interrupted is True

    # Must have dispatched an AUDIO_INTERRUPT frame
    interrupt_msg = await session.get_next_outgoing_message()
    assert interrupt_msg.msg_type == OutgoingMessageType.CONTROL
    assert interrupt_msg.payload == {"type": "AUDIO_INTERRUPT"}


@pytest.mark.asyncio
async def test_barge_in_triggered_by_client_interrupt_message() -> None:
    session = LiveVoiceSession(workspace_id=uuid4(), user_id=uuid4())

    await session.enqueue_model_audio(b"\x00\x20" * 600)
    assert session.pending_audio_chunks_count == 1

    start_time = time.perf_counter()
    await session.handle_client_control_message({"type": "CLIENT_INTERRUPT"})
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    assert elapsed_ms < 150.0
    assert session.pending_audio_chunks_count == 0

    msg = await session.get_next_outgoing_message()
    assert msg.payload == {"type": "AUDIO_INTERRUPT"}


@pytest.mark.asyncio
async def test_barge_in_triggered_by_server_interrupted_event() -> None:
    session = LiveVoiceSession(workspace_id=uuid4(), user_id=uuid4())

    await session.enqueue_model_audio(b"\x00\x30" * 600)
    assert session.pending_audio_chunks_count == 1

    start_time = time.perf_counter()
    await session.handle_server_interrupted()
    elapsed_ms = (time.perf_counter() - start_time) * 1000.0

    assert elapsed_ms < 150.0
    assert session.pending_audio_chunks_count == 0

    msg = await session.get_next_outgoing_message()
    assert msg.payload == {"type": "AUDIO_INTERRUPT"}
