"""Voice WebSocket protocol v2 end to end with a fake Live provider (no network)."""

from typing import Any

import pytest

from app.voice.protocol import ConnectionLimiter
from tests.support import fake_live as fl
from tests.support.voice_app import FakePersistence, make_principal, recv, recv_until, voice_client

URL = "/api/v1/voice/live?token=valid-jwt&protocol=2"
STALE = b"\xaa" * 8
NEW = b"\xbb" * 8


def echo_script(session: fl.FakeLiveSession, kind: str, kwargs: dict[str, Any]) -> None:
    text = kwargs.get("text")
    if text is None:
        return
    if text == "tool":
        session.push_turn(fl.tool_call("approve_action", {"approval_id": "a"}, "c-approve"))
    elif text == "task":
        session.push_turn(
            fl.tool_call(
                "submit_task",
                {"instruction": "research solar energy trends", "deliverable": "none"},
                "c-task",
            )
        )
    elif text == "late":
        session.push_turn(
            fl.audio_msg(STALE),
            fl.turn_complete(),
            fl.audio_msg(NEW),
            fl.out_tx("new"),
            fl.turn_complete(),
        )
    elif text == "die":
        session.drop()
    elif text == "speak":
        session.push_turn(fl.audio_msg(STALE))
    else:
        session.push_turn(fl.audio_msg(), fl.out_tx("Hi "), fl.out_tx("there"), fl.turn_complete())


def finished(ws: Any, role: str) -> dict[str, Any]:
    while True:
        frame = recv_until(ws, "TRANSCRIPT")
        if frame["final"] and frame["role"] == role:
            return frame


def test_unauthenticated_gets_close_code_4401_not_handshake_failure() -> None:
    with voice_client(authenticate_fails=True) as client:
        with client.websocket_connect("/api/v1/voice/live?token=bad") as ws:
            assert recv(ws)["code"] == "unauthenticated"
            assert recv(ws) == {"type": "_close", "code": 4401}


def test_forbidden_origin_closes_4403() -> None:
    with voice_client(live=fl.FakeLive()) as client:
        with client.websocket_connect(URL, headers={"origin": "https://evil.example"}) as ws:
            assert recv(ws)["code"] == "forbidden_origin"
            assert recv(ws)["code"] == 4403


def test_hello_first_frame_authenticates_and_reports_session() -> None:
    persistence = FakePersistence()
    with voice_client(live=fl.FakeLive(on_send=echo_script), persistence=persistence) as client:
        with client.websocket_connect(
            "/api/v1/voice/live", headers={"origin": "http://localhost:3000"}
        ) as ws:
            ws.send_json({"type": "HELLO", "protocol": 2, "token": "valid-jwt"})
            ready = recv(ws)
            assert ready["type"] == "READY" and ready["protocol"] == 2
            assert ready["session_id"] == str(persistence.created[0])
            assert ready["input"]["rate"] == 16000 and ready["output"]["rate"] == 24000
            assert recv(ws) == {"type": "TURN_STATE", "turn_id": None, "state": "listening"}


def test_missing_token_and_no_hello_is_4401() -> None:
    with voice_client(live=fl.FakeLive()) as client:
        with client.websocket_connect("/api/v1/voice/live") as ws:
            ws.send_json({"type": "NOT_HELLO"})
            assert recv(ws)["code"] == "unauthenticated"


def test_no_key_and_no_connector_is_live_unavailable_never_a_simulator() -> None:
    with voice_client() as client:  # default settings have no usable Gemini key
        with client.websocket_connect(URL) as ws:
            frame = recv(ws)
            assert frame == {"type": "LIVE_UNAVAILABLE", "reason": "no_key", "session_id": None}
            assert recv(ws) == {"type": "_close", "code": 4503}


def test_connect_failure_is_live_unavailable() -> None:
    with voice_client(live=fl.FakeLive(fail_with=OSError("down"))) as client:
        with client.websocket_connect(URL) as ws:
            assert recv(ws)["reason"] == "connect_failed"
            assert recv(ws)["code"] == 4503


def test_two_turns_are_persisted_per_turn_in_order() -> None:
    persistence = FakePersistence()
    with voice_client(live=fl.FakeLive(on_send=echo_script), persistence=persistence) as client:
        with client.websocket_connect(URL) as ws:
            ready = recv(ws)
            sid = ready["session_id"]
            ws.send_json({"type": "USER_TEXT", "text": "hello"})
            user = finished(ws, "user")
            assert user["source"] == "typed" and user["text"] == "hello"
            frames: list[dict[str, Any]] = []
            reply = recv_until(ws, "TRANSCRIPT", frames)
            while not (reply["final"] and reply["role"] == "assistant"):
                reply = recv_until(ws, "TRANSCRIPT", frames)
            assert reply["text"] == "Hi there"
            persisted = recv_until(ws, "MESSAGE_PERSISTED")
            assert persisted["turn_id"] in (user["turn_id"], reply["turn_id"])
            states = [f["state"] for f in frames if f["type"] == "TURN_STATE"]
            assert "speaking" in states
            # Second turn proves the receive loop survives turn_complete.
            ws.send_json({"type": "USER_TEXT", "text": "again"})
            assert finished(ws, "assistant")["text"] == "Hi there"
    roles = [(s["role"], s["content"], s["modality"]) for s in persistence.saved]
    assert roles == [
        ("user", "hello", "text"),
        ("assistant", "Hi there", "audio"),
        ("user", "again", "text"),
        ("assistant", "Hi there", "audio"),
    ]
    assert {str(s["session_id"]) for s in persistence.saved} == {sid}
    assert persistence.saved[0]["metadata"]["source"] == "typed"


def test_spoken_turn_uses_interim_then_final_in_one_turn() -> None:
    def script(session: fl.FakeLiveSession, kind: str, kwargs: dict[str, Any]) -> None:
        if "audio_stream_end" in kwargs:
            session.push_turn(
                fl.in_tx("what is "), fl.in_tx("up"), fl.out_tx("All good"), fl.turn_complete()
            )

    persistence = FakePersistence()
    with voice_client(live=fl.FakeLive(on_send=script), persistence=persistence) as client:
        with client.websocket_connect(URL) as ws:
            recv(ws)
            ws.send_bytes(b"\x00\x01" * 1600)
            ws.send_json({"type": "VAD_SPEECH_STOP"})
            frames: list[dict[str, Any]] = []
            final = recv_until(ws, "TRANSCRIPT", frames)
            while not (final["final"] and final["role"] == "assistant"):
                final = recv_until(ws, "TRANSCRIPT", frames)
            user_frames = [f for f in frames if f["type"] == "TRANSCRIPT" and f["role"] == "user"]
            assert [f["delta"] for f in user_frames[:2]] == ["what is ", "up"]
            assert user_frames[-1]["final"] and user_frames[-1]["text"] == "what is up"
            assert len({f["turn_id"] for f in user_frames}) == 1
    assert persistence.saved[0]["modality"] == "audio"
    assert persistence.saved[0]["content"] == "what is up"


def test_refused_approval_tool_call_does_not_kill_the_session() -> None:
    live = fl.FakeLive(on_send=echo_script)
    with voice_client(live=live, persistence=FakePersistence()) as client:
        with client.websocket_connect(URL) as ws:
            recv(ws)
            ws.send_json({"type": "USER_TEXT", "text": "tool"})
            err = recv_until(ws, "ERROR")
            assert err["code"] == "tool_refused" and err["fatal"] is False
            ws.send_json({"type": "USER_TEXT", "text": "still there?"})
            assert finished(ws, "assistant")["text"] == "Hi there"
        assert live.session.tool_responses[0].response["error"] == "approval_prohibited"


def test_task_submission_without_database_is_honest_error_not_fabricated_task() -> None:
    live = fl.FakeLive(on_send=echo_script)
    with voice_client(live=live, persistence=FakePersistence()) as client:
        with client.websocket_connect(URL) as ws:
            recv(ws)
            ws.send_json({"type": "USER_TEXT", "text": "task"})
            frames: list[dict[str, Any]] = []
            err = recv_until(ws, "ERROR", frames)
            assert err["code"] == "tasks_unavailable"
            assert not any(f["type"] == "TASK_STATUS" for f in frames)
        assert "task_id" not in live.session.tool_responses[0].response


def test_stale_audio_is_dropped_after_barge_in_with_epoch() -> None:
    persistence = FakePersistence()
    with voice_client(live=fl.FakeLive(on_send=echo_script), persistence=persistence) as client:
        with client.websocket_connect(URL) as ws:
            recv(ws)
            ws.send_json({"type": "USER_TEXT", "text": "speak"})
            assert recv_until(ws, "_audio")["data"] == STALE
            ws.send_json({"type": "CLIENT_INTERRUPT"})
            assert recv_until(ws, "AUDIO_INTERRUPT")["epoch"] == 1
            ws.send_json({"type": "USER_TEXT", "text": "late"})
            frames: list[dict[str, Any]] = []
            reply = recv_until(ws, "TRANSCRIPT", frames)
            while not (reply["final"] and reply["role"] == "assistant"):
                reply = recv_until(ws, "TRANSCRIPT", frames)
            audio = [f["data"] for f in frames if f["type"] == "_audio"]
            assert STALE not in audio
            assert audio == [NEW]


def test_mic_audio_is_forwarded_while_model_speaks() -> None:
    live = fl.FakeLive(on_send=echo_script)
    with voice_client(live=live, persistence=FakePersistence()) as client:
        with client.websocket_connect(URL) as ws:
            recv(ws)
            ws.send_json({"type": "USER_TEXT", "text": "speak"})
            recv_until(ws, "_audio")
            ws.send_bytes(b"\x05\x06" * 1600)
            ws.send_json({"type": "VAD_SPEECH_START"})
            recv_until(ws, "AUDIO_INTERRUPT")
        blobs = [r["audio"] for r in live.session.realtime if "audio" in r]
        assert blobs and blobs[0].mime_type == "audio/pcm;rate=16000"


def test_provider_death_sends_fatal_error_and_close_code() -> None:
    live = fl.FakeLive(on_send=echo_script)
    with voice_client(live=live, persistence=FakePersistence()) as client:
        with client.websocket_connect(URL) as ws:
            recv(ws)
            ws.send_json({"type": "USER_TEXT", "text": "die"})
            err = recv_until(ws, "ERROR")
            assert err["fatal"] is True
            assert recv_until(ws, "_close")["code"] == 4503


def test_resume_existing_session_seeds_history_and_unknown_is_4404() -> None:
    from uuid import uuid4

    sid = uuid4()
    persistence = FakePersistence({sid: [("user", "earlier"), ("assistant", "reply")]})
    live = fl.FakeLive(on_send=echo_script)
    with voice_client(live=live, persistence=persistence) as client:
        with client.websocket_connect(f"{URL}&session_id={sid}") as ws:
            ready = recv(ws)
            assert ready["session_id"] == str(sid) and ready["title"] == "Existing"
        seeded = live.session.client_content[0]["turns"]
        assert [t.parts[0].text for t in seeded] == ["earlier", "reply"]
        assert persistence.created == []
        with client.websocket_connect(f"{URL}&session_id={uuid4()}") as ws:
            assert recv(ws)["code"] == "session_not_found"
            assert recv(ws)["code"] == 4404


def test_persist_failure_is_reported_not_swallowed() -> None:
    persistence = FakePersistence()
    persistence.fail_save = True
    with voice_client(live=fl.FakeLive(on_send=echo_script), persistence=persistence) as client:
        with client.websocket_connect(URL) as ws:
            recv(ws)
            ws.send_json({"type": "USER_TEXT", "text": "hello"})
            assert recv_until(ws, "ERROR")["code"] == "persist_failed"


def test_per_user_socket_limit_4429() -> None:
    principal = make_principal()
    with voice_client(
        live=fl.FakeLive(on_send=echo_script),
        persistence=FakePersistence(),
        principal=principal,
        voice_limiter=ConnectionLimiter(1),
    ) as client:
        with client.websocket_connect(URL) as first:
            recv(first)
            with client.websocket_connect(URL) as second:
                assert recv(second)["code"] == "too_many_sessions"
                assert recv(second)["code"] == 4429


def test_idle_timeout_ends_with_error_and_normal_close() -> None:
    with voice_client(
        live=fl.FakeLive(on_send=echo_script), persistence=FakePersistence(), voice_idle_timeout=0.3
    ) as client:
        with client.websocket_connect(URL) as ws:
            recv(ws)
            assert recv_until(ws, "ERROR")["code"] == "idle_timeout"
            assert recv_until(ws, "_close")["code"] == 1000


def test_v1_client_still_gets_legacy_frames() -> None:
    live = fl.FakeLive(on_send=echo_script)
    with voice_client(live=live, persistence=FakePersistence()) as client:
        with client.websocket_connect("/api/v1/voice/live?token=valid-jwt") as ws:
            recv(ws)
            ws.send_json({"type": "USER_TEXT", "text": "hello"})
            seen: list[dict[str, Any]] = []
            recv_until(ws, "TRANSCRIPT_MODEL", seen)
            assert any(f["type"] == "TRANSCRIPT_USER" and f["text"] == "hello" for f in seen)


@pytest.mark.parametrize("origin", ["http://localhost:3000", "http://127.0.0.1:5173", None])
def test_loopback_and_missing_origin_allowed(origin: str | None) -> None:
    headers = {"origin": origin} if origin else {}
    live = fl.FakeLive(on_send=echo_script)
    with voice_client(live=live, persistence=FakePersistence()) as client:
        with client.websocket_connect(URL, headers=headers) as ws:
            assert recv(ws)["type"] == "READY"
