"""Helpers to drive the voice WebSocket with a fake Live provider and fake persistence."""

from __future__ import annotations

import json
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any
from unittest.mock import AsyncMock, patch
from uuid import UUID, uuid4

from fastapi.testclient import TestClient
from starlette.testclient import WebSocketTestSession

from app.auth.models import Principal
from app.main import create_app
from app.voice.persistence import (
    SessionInfo,
    SessionNotOwned,
    VoicePersistenceError,
)
from tests.support.fake_live import FakeLive
from tests.support.settings import make_settings


class FakePersistence:
    """In-memory stand-in for VoicePersistenceService (records every saved turn in order)."""

    engine = None
    enabled = True

    def __init__(self, owned: dict[UUID, list[tuple[str, str]]] | None = None) -> None:
        self.owned = owned if owned is not None else {}
        self.saved: list[dict[str, Any]] = []
        self.created: list[UUID] = []
        self.fail_save = False

    async def get_session(self, workspace_id: UUID, user_id: UUID, session_id: UUID) -> SessionInfo:
        if session_id not in self.owned:
            raise SessionNotOwned
        return SessionInfo(session_id, "Existing", False)

    async def create_session(
        self, workspace_id: UUID, user_id: UUID, title: str = "Voice conversation"
    ) -> SessionInfo:
        sid = uuid4()
        self.owned[sid] = []
        self.created.append(sid)
        return SessionInfo(sid, title, True)

    async def save_turn(
        self, workspace_id: UUID, user_id: UUID, session_id: UUID, **kw: Any
    ) -> UUID:
        if self.fail_save:
            raise VoicePersistenceError("boom")
        self.saved.append({"session_id": session_id, **kw})
        return uuid4()

    async def load_seed(
        self, workspace_id: UUID, user_id: UUID, session_id: UUID, limit: int
    ) -> list[tuple[str, str]]:
        return list(self.owned.get(session_id, []))[-limit:]


def make_principal() -> Principal:
    return Principal(user_id=uuid4(), workspace_id=uuid4(), role="user", session_id=uuid4())


@contextmanager
def voice_client(
    *,
    live: FakeLive | None = None,
    persistence: FakePersistence | None = None,
    principal: Principal | None = None,
    authenticate_fails: bool = False,
    **state: Any,
) -> Iterator[TestClient]:
    principal = principal or make_principal()
    with patch("app.main.Resources") as factory:
        resources = factory.return_value
        resources.check = AsyncMock(return_value=None)
        resources.close = AsyncMock()
        resources.verify_runtime_role = AsyncMock(return_value=None)
        resources.engine = None
        resources.redis = None
        resources.registry = None
        resources.model_gateway = None
        resources.subtask_queue = None
        auth_service = AsyncMock()
        auth_service.warm_up = AsyncMock()
        auth_service.aclose = AsyncMock()
        if authenticate_fails:
            auth_service.authenticate = AsyncMock(side_effect=Exception("bad token"))
        else:
            auth_service.authenticate = AsyncMock(return_value=principal)
        app = create_app(make_settings(), auth_service=auth_service)
        if live is not None:
            app.state.voice_live_connector = live.connector
        if persistence is not None:
            app.state.voice_persistence = persistence
        for key, value in state.items():
            setattr(app.state, key, value)
        with TestClient(app) as client:
            yield client


def recv(ws: WebSocketTestSession) -> dict[str, Any]:
    """Next frame: JSON frames as dicts, binary as {"type": "_audio", "data": bytes}."""
    message = ws.receive()
    if message["type"] == "websocket.close":
        return {"type": "_close", "code": message.get("code")}
    if message.get("bytes") is not None:
        return {"type": "_audio", "data": message["bytes"]}
    payload: dict[str, Any] = json.loads(message["text"])
    return payload


def recv_until(
    ws: WebSocketTestSession, kind: str, collect: list[dict[str, Any]] | None = None
) -> dict[str, Any]:
    for _ in range(200):
        frame = recv(ws)
        if collect is not None:
            collect.append(frame)
        if frame["type"] == kind:
            return frame
        if frame["type"] == "_close":
            raise AssertionError(f"closed ({frame['code']}) before {kind}")
    raise AssertionError(f"no {kind} frame")
