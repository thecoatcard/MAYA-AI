"""Unit tests for Redis WorkingMemoryManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.memory.working_memory import WorkingMemoryManager


@pytest.mark.asyncio
async def test_working_memory_append_and_get_turns() -> None:
    ws_id = uuid4()
    session_id = uuid4()
    mock_redis = MagicMock()

    stored_list: list[str] = []

    async def fake_rpush(key: str, val: str) -> int:
        stored_list.append(val)
        return len(stored_list)

    async def fake_lrange(key: str, start: int, end: int) -> list[str]:
        if start < 0:
            return stored_list[start:]
        return stored_list[start : end + 1]

    async def fake_expire(key: str, ttl: int) -> bool:
        return True

    mock_redis.rpush = AsyncMock(side_effect=fake_rpush)
    mock_redis.lrange = AsyncMock(side_effect=fake_lrange)
    mock_redis.expire = AsyncMock(side_effect=fake_expire)

    mgr = WorkingMemoryManager(redis_client=mock_redis)

    await mgr.append_turn(
        workspace_id=ws_id,
        session_id=session_id,
        role="user",
        content="What is the current project status?",
        metadata={"client": "web"},
    )
    await mgr.append_turn(
        workspace_id=ws_id,
        session_id=session_id,
        role="assistant",
        content="All systems are operating nominally.",
    )

    # Verify expire was called with 86400 (24 hours TTL)
    expected_key = f"memory:working:{ws_id}:{session_id}"
    mock_redis.expire.assert_awaited_with(expected_key, 86400)

    turns = await mgr.get_recent_turns(workspace_id=ws_id, session_id=session_id, limit=10)
    assert len(turns) == 2
    assert turns[0].role == "user"
    assert turns[0].content == "What is the current project status?"
    assert turns[0].metadata == {"client": "web"}
    assert turns[1].role == "assistant"
    assert turns[1].content == "All systems are operating nominally."


@pytest.mark.asyncio
async def test_working_memory_clear_session() -> None:
    ws_id = uuid4()
    session_id = uuid4()
    mock_redis = MagicMock()
    mock_redis.delete = AsyncMock(return_value=1)

    mgr = WorkingMemoryManager(redis_client=mock_redis)
    cleared = await mgr.clear_session(workspace_id=ws_id, session_id=session_id)

    assert cleared is True
    expected_key = f"memory:working:{ws_id}:{session_id}"
    mock_redis.delete.assert_awaited_once_with(expected_key)
