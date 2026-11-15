"""Unit tests for Voice connection key pool failover on quota / 429."""

from contextlib import asynccontextmanager
from typing import Any
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest

from app.auth.models import Principal
from app.gateway.key_pool import KeyPool
from app.voice.connection import VoiceConnection, VoiceDeps
from app.voice.gemini_live import LiveUnavailable
from app.voice.persistence import SessionInfo
from app.voice.tools import VoiceToolRegistry
from tests.support import fake_live as fl


def _make_key(suffix: str) -> str:
    return "-".join(["test", "voice", "key", suffix])


@pytest.mark.asyncio
async def test_voice_connection_retries_next_key_on_connect_quota_error() -> None:
    k1 = _make_key("one")
    k2 = _make_key("two")
    pool = KeyPool([k1, k2], default_quota_cooldown=60.0)

    connect_keys_seen: list[str] = []

    @asynccontextmanager
    async def connector_mock(model: str, config: Any) -> Any:
        # Check current api_key in deps
        connect_keys_seen.append(deps.api_key)
        if len(connect_keys_seen) == 1:
            raise LiveUnavailable("quota")
        # Second attempt succeeds with fake live session
        session = fl.FakeLiveSession()
        session.push_turn(fl.turn_complete())
        yield session

    ws = AsyncMock()
    principal = Principal(user_id=uuid4(), workspace_id=uuid4(), role="user", session_id=uuid4())

    persistence = AsyncMock()
    persistence.enabled = True
    persistence.create_session = AsyncMock(
        return_value=SessionInfo(uuid4(), "New Voice Session", True)
    )

    session_manager = AsyncMock()
    session_manager.get_resumption_token = AsyncMock(return_value=None)

    deps = VoiceDeps(
        persistence=persistence,
        session_manager=session_manager,
        redis=None,
        model_gateway=None,
        queue=None,
        api_key=k1,
        models=["gemini-3.8-live"],
        connector=connector_mock,
        key_pool=pool,
    )

    connection = VoiceConnection(ws, principal, deps)
    ok = await connection._setup(VoiceToolRegistry())

    assert ok is True
    # First attempt with k1, failed with quota; second attempt with k2, succeeded!
    assert connect_keys_seen == [k1, k2]
    assert deps.api_key == k2
    assert connection.bridge is not None
    # Key 1 is on cooldown
    assert pool.cooling_count == 1
