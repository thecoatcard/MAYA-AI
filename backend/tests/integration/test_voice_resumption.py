"""Integration tests for VoiceSessionManager and transparent session resumption."""

import time
from uuid import uuid4

import pytest

from app.voice.live_session import LiveVoiceSession, OutgoingMessageType
from app.voice.session_manager import VoiceSessionManager


@pytest.mark.asyncio
async def test_session_manager_token_storage_and_retrieval() -> None:
    manager = VoiceSessionManager()
    ws_id = uuid4()
    session_id = "test-session-123"
    token = "test-token"  # secret-scan:allow

    await manager.store_resumption_token(ws_id, session_id, token, ttl_seconds=60)
    retrieved = await manager.get_resumption_token(ws_id, session_id)
    assert retrieved == token

    await manager.invalidate_resumption_token(ws_id, session_id)
    assert await manager.get_resumption_token(ws_id, session_id) is None


def test_session_rotation_and_expiry_thresholds() -> None:
    manager = VoiceSessionManager(rotation_threshold_seconds=600, expiry_threshold_seconds=900)
    now = time.time()

    # 5 minutes old (300s): should not rotate, not expired
    assert manager.should_rotate_session(now - 300, current_time=now) is False
    assert manager.is_session_expired(now - 300, current_time=now) is False

    # 10 minutes old (600s): should rotate, not expired
    assert manager.should_rotate_session(now - 600, current_time=now) is True
    assert manager.is_session_expired(now - 600, current_time=now) is False

    # 14 minutes old (840s): should rotate, not expired
    assert manager.should_rotate_session(now - 840, current_time=now) is True
    assert manager.is_session_expired(now - 840, current_time=now) is False

    # 15 minutes old (900s): expired
    assert manager.is_session_expired(now - 900, current_time=now) is True


@pytest.mark.asyncio
async def test_transparent_session_rotation() -> None:
    manager = VoiceSessionManager()
    ws_id = uuid4()
    u_id = uuid4()
    old_session = LiveVoiceSession(workspace_id=ws_id, user_id=u_id, session_id="old-sess")

    # Queue some audio before rotation
    await old_session.enqueue_model_audio(b"\x00\x10" * 100)

    # Perform transparent rotation
    new_session = await manager.rotate_session(old_session)

    assert new_session.workspace_id == ws_id
    assert new_session.user_id == u_id
    assert new_session.session_id != old_session.session_id

    # Must notify client of session renewal
    msg = await new_session.get_next_outgoing_message()
    assert msg.msg_type == OutgoingMessageType.CONTROL
    assert msg.payload == {"type": "SESSION_RENEWED", "expires_in": 600}
