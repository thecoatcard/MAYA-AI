"""Integration tests for Voice WebSocket route WS /api/v1/voice/live."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient
from starlette.websockets import WebSocketDisconnect

from app.auth.models import Principal
from app.main import create_app
from tests.support.settings import make_settings


def test_voice_websocket_unauthenticated_rejects() -> None:
    with patch("app.main.Resources") as factory:
        resources = factory.return_value
        resources.check = AsyncMock(return_value=None)
        resources.close = AsyncMock()
        resources.verify_runtime_role = AsyncMock(return_value=None)

        auth_service = AsyncMock()
        auth_service.warm_up = AsyncMock()
        auth_service.aclose = AsyncMock()
        # Invalid token rejection
        auth_service.authenticate = AsyncMock(side_effect=Exception("Invalid token"))

        app = create_app(make_settings(), auth_service=auth_service)
        with TestClient(app) as client:
            with pytest.raises(WebSocketDisconnect):
                # When policy violation occurs, websocket_connect raises or closes with code 1008
                with client.websocket_connect("/api/v1/voice/live?token=invalid"):
                    pass


def test_voice_websocket_authenticated_audio_and_control_flow() -> None:
    ws_id = uuid4()
    u_id = uuid4()
    session_uuid = uuid4()
    principal = Principal(user_id=u_id, workspace_id=ws_id, role="user", session_id=session_uuid)

    with patch("app.main.Resources") as factory:
        resources = factory.return_value
        resources.check = AsyncMock(return_value=None)
        resources.close = AsyncMock()
        resources.verify_runtime_role = AsyncMock(return_value=None)
        resources.redis = None

        auth_service = AsyncMock()
        auth_service.warm_up = AsyncMock()
        auth_service.aclose = AsyncMock()
        auth_service.authenticate = AsyncMock(return_value=principal)

        app = create_app(make_settings(), auth_service=auth_service)
        with TestClient(app) as client:
            with client.websocket_connect("/api/v1/voice/live?token=valid-jwt") as websocket:
                # Send 16 kHz PCM chunk
                dummy_mic_frame = b"\x00\x10" * 1600
                websocket.send_bytes(dummy_mic_frame)

                # Send VAD Speech Start
                websocket.send_json({"type": "VAD_SPEECH_START"})

                # Send Client Interrupt
                websocket.send_json({"type": "CLIENT_INTERRUPT"})

                # Server should reply with AUDIO_INTERRUPT frame
                response = websocket.receive_json()
                assert response.get("type") == "AUDIO_INTERRUPT"
