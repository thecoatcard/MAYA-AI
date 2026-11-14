"""Conversational turns and task submission over the Voice WebSocket (fake Live provider).

Updated: these used to pass through a built-in simulator that fabricated replies and tasks when
no key was configured. They now script a fake provider session instead (audit C2).
"""

from typing import Any

from tests.support import fake_live as fl
from tests.support.voice_app import FakePersistence, recv, recv_until, voice_client


def script(session: fl.FakeLiveSession, kind: str, kwargs: dict[str, Any]) -> None:
    text = kwargs.get("text", "")
    if "research" in text:
        session.push_turn(fl.tool_call("submit_task", {"intent": text}, "call-r"))
    else:
        session.push_turn(fl.audio_msg(), fl.out_tx("I can help."), fl.turn_complete())


def test_voice_conversational_turn_and_task_execution() -> None:
    live = fl.FakeLive(on_send=script)
    with voice_client(live=live, persistence=FakePersistence()) as client:
        with client.websocket_connect("/api/v1/voice/live?token=valid-jwt&protocol=2") as ws:
            recv(ws)
            ws.send_json({"type": "USER_TEXT", "text": "Hello MAYA, what can you do?"})
            assert len(recv_until(ws, "_audio")["data"]) > 0
            assert recv_until(ws, "TURN_STATE")["state"] in ("speaking", "listening")

            # Without a task database the tool reports an honest error; no task id is invented.
            ws.send_json({"type": "USER_TEXT", "text": "Please research enterprise pricing"})
            err = recv_until(ws, "ERROR")
            assert err["code"] == "tasks_unavailable"
        assert "task_id" not in live.session.tool_responses[0].response
