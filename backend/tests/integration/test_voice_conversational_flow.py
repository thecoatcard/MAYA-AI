"""Integration tests for conversational AI turn and task submission over Voice WebSocket."""

from unittest.mock import AsyncMock, patch
from uuid import uuid4

from fastapi.testclient import TestClient

from app.auth.models import Principal
from app.main import create_app
from tests.support.settings import make_settings


def test_voice_conversational_turn_and_task_execution() -> None:
    ws_id = uuid4()
    u_id = uuid4()
    session_uuid = uuid4()
    principal = Principal(user_id=u_id, workspace_id=ws_id, role="user", session_id=session_uuid)

    with patch("app.main.Resources") as factory:
        resources = factory.return_value
        resources.check = AsyncMock(return_value=None)
        resources.close = AsyncMock()
        resources.verify_runtime_role = AsyncMock(return_value=None)
        resources.engine = None
        resources.redis = None

        auth_service = AsyncMock()
        auth_service.warm_up = AsyncMock()
        auth_service.aclose = AsyncMock()
        auth_service.authenticate = AsyncMock(return_value=principal)

        app = create_app(make_settings(), auth_service=auth_service)
        with TestClient(app) as client:
            with client.websocket_connect("/api/v1/voice/live?token=valid-jwt") as websocket:
                # 1. Send conversational user text
                websocket.send_json({"type": "USER_TEXT", "text": "Hello MAYA, what can you do?"})

                # Expect TRANSCRIPT_USER frame
                resp1 = websocket.receive_json()
                assert resp1.get("type") == "TRANSCRIPT_USER"
                assert "Hello MAYA" in resp1.get("text", "")

                # Expect TRANSCRIPT_MODEL frame
                resp2 = websocket.receive_json()
                assert resp2.get("type") == "TRANSCRIPT_MODEL"
                assert "MAYA" in resp2.get("text", "") or "assist" in resp2.get("text", "")

                # Expect audio bytes frame
                audio_bytes = websocket.receive_bytes()
                assert len(audio_bytes) > 0

                # 2. Send instruction triggering submit_task background tool
                websocket.send_json(
                    {"type": "USER_TEXT", "text": "Please research enterprise pricing models"}
                )

                # Read user transcript
                resp_tx = websocket.receive_json()
                assert resp_tx.get("type") == "TRANSCRIPT_USER"

                # Read tool execution task status frame
                resp_task = websocket.receive_json()
                assert resp_task.get("type") == "TASK_STATUS"
                assert resp_task.get("status") == "pending"
                assert resp_task.get("task_id") is not None

                # Read model reply transcript acknowledging the task
                resp_model = websocket.receive_json()
                assert resp_model.get("type") == "TRANSCRIPT_MODEL"
                assert "task" in resp_model.get("text", "").lower()
