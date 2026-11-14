"""Unit tests for GeminiLiveBridge model configuration (fake provider, no simulator)."""

from uuid import uuid4

import pytest

from app.gateway.registry import build_default_registry
from app.voice.connection import build_models
from app.voice.gemini_live import GeminiLiveBridge, LiveUnavailable
from app.voice.tools import VoiceExecutionContext, VoiceToolRegistry
from tests.support import fake_live as fl


def _bridge(live: fl.FakeLive | None, **kwargs: object) -> GeminiLiveBridge:
    return GeminiLiveBridge(
        api_key=None,
        system_instruction="Test assistant",
        tool_registry=VoiceToolRegistry(),
        context=VoiceExecutionContext(workspace_id=uuid4(), user_id=uuid4()),
        connector=live.connector if live else None,
        **kwargs,  # type: ignore[arg-type]
    )


@pytest.mark.asyncio
async def test_gemini_live_bridge_defaults_to_the_live_alias_model() -> None:
    live = fl.FakeLive()
    bridge = _bridge(live)
    assert bridge._model == "gemini-3.8-live"
    assert build_default_registry().get("live").model_id == bridge._model
    assert build_models(None, None)[0] == "gemini-3.8-live"

    await bridge.connect()
    assert live.connects[0][0] == "gemini-3.8-live"
    live.session.push_turn(fl.in_tx("Hello Maya"), fl.out_tx("Hi"), fl.audio_msg())
    events = bridge.receive_events().__aiter__()
    kinds = [(await events.__anext__())["type"] for _ in range(3)]
    assert kinds == ["USER_TRANSCRIPT", "MODEL_TRANSCRIPT", "MODEL_AUDIO"]
    await bridge.close()


@pytest.mark.asyncio
async def test_gemini_live_bridge_supports_extended_thinking_model() -> None:
    assert build_models(None, None) == ["gemini-3.8-live", "gemini-3.8-live-extended-thinking"]
    live = fl.FakeLive()
    bridge = GeminiLiveBridge(
        api_key=None,
        system_instruction="Test assistant",
        tool_registry=VoiceToolRegistry(),
        context=VoiceExecutionContext(workspace_id=uuid4(), user_id=uuid4()),
        model="gemini-3.8-live-extended-thinking",
        connector=live.connector,
    )
    assert bridge._model == "gemini-3.8-live-extended-thinking"
    await bridge.connect()
    assert live.connects[0][0] == "gemini-3.8-live-extended-thinking"
    await bridge.close()


@pytest.mark.asyncio
async def test_without_connector_and_key_there_is_no_simulation() -> None:
    with pytest.raises(LiveUnavailable):
        await _bridge(None).connect()
