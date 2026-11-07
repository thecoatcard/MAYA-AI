"""Unit tests for GeminiLiveBridge model configuration and interaction status."""

from uuid import uuid4

import pytest

from app.voice.gemini_live import GeminiLiveBridge
from app.voice.tools import VoiceExecutionContext, VoiceToolRegistry


@pytest.mark.asyncio
async def test_gemini_live_bridge_defaults_to_gemini_3_8_live() -> None:
    context = VoiceExecutionContext(workspace_id=uuid4(), user_id=uuid4())
    registry = VoiceToolRegistry()

    bridge = GeminiLiveBridge(
        api_key=None,
        system_instruction="Test assistant",
        tool_registry=registry,
        context=context,
        mock_mode=True,
    )
    assert bridge._model == "gemini-3.8-live"
    assert bridge.is_mock_mode is True

    await bridge.connect()
    # Sending text should produce mock transcript and audio
    await bridge.send_text_input("Hello Maya")
    events = []
    async for ev in bridge.receive_events():
        events.append(ev)
        if len(events) >= 3:
            break

    types = [e["type"] for e in events]
    assert "USER_TRANSCRIPT" in types
    assert "MODEL_TRANSCRIPT" in types
    assert "MODEL_AUDIO" in types
    await bridge.close()


@pytest.mark.asyncio
async def test_gemini_live_bridge_supports_extended_thinking_model() -> None:
    context = VoiceExecutionContext(workspace_id=uuid4(), user_id=uuid4())
    registry = VoiceToolRegistry()

    bridge = GeminiLiveBridge(
        api_key=None,
        system_instruction="Test assistant",
        tool_registry=registry,
        context=context,
        model="gemini-3.8-live-extended-thinking",
        mock_mode=True,
    )
    assert bridge._model == "gemini-3.8-live-extended-thinking"
    await bridge.connect()

    # Verify task submission in mock mode
    await bridge.send_text_input("Please create a task to research AI agents")
    events = []
    async for ev in bridge.receive_events():
        events.append(ev)
        if len(events) >= 4:
            break

    types = [e["type"] for e in events]
    assert "USER_TRANSCRIPT" in types
    assert "TOOL_EXECUTED" in types
    assert "MODEL_TRANSCRIPT" in types
    await bridge.close()
