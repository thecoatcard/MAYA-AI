"""Integration tests for the Voice WebSocket route WS /api/v1/voice/live (fake Live provider)."""

from tests.support import fake_live as fl
from tests.support.voice_app import FakePersistence, recv, recv_until, voice_client


def test_voice_websocket_unauthenticated_rejects_with_close_code() -> None:
    # Updated: the socket is now accepted first so the client sees close code 4401 instead of an
    # opaque pre-accept HTTP 403 (audit H6).
    with voice_client(authenticate_fails=True) as client:
        with client.websocket_connect("/api/v1/voice/live?token=invalid") as websocket:
            assert recv(websocket)["code"] == "unauthenticated"
            assert recv(websocket) == {"type": "_close", "code": 4401}


def test_voice_websocket_authenticated_audio_and_control_flow() -> None:
    live = fl.FakeLive()
    with voice_client(live=live, persistence=FakePersistence()) as client:
        with client.websocket_connect("/api/v1/voice/live?token=valid-jwt") as websocket:
            assert recv(websocket)["type"] == "READY"
            websocket.send_bytes(b"\x00\x10" * 1600)  # 16 kHz PCM chunk
            websocket.send_json({"type": "VAD_SPEECH_START"})
            websocket.send_json({"type": "CLIENT_INTERRUPT"})
            frame = recv_until(websocket, "AUDIO_INTERRUPT")
            assert frame["epoch"] == 1
        assert any("audio" in item for item in live.session.realtime)
