"""Integration tests for MemoryLifecycleManager."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from sqlalchemy.ext.asyncio import AsyncSession

from app.db.repositories.memory import MemoryRepository
from app.memory.lifecycle import MemoryLifecycleManager
from app.memory.working_memory import WorkingMemoryManager


@pytest.mark.asyncio
async def test_lifecycle_archive_stale_memories() -> None:
    ws_id = uuid4()
    mock_session = MagicMock(spec=AsyncSession)
    mock_repo = MagicMock(spec=MemoryRepository)
    mock_repo.mark_expired = AsyncMock(return_value=7)

    manager = MemoryLifecycleManager(repository=mock_repo)
    count = await manager.archive_stale_memories(
        session_or_conn=mock_session,
        workspace_id=ws_id,
        threshold_days=180,
    )

    assert count == 7
    mock_repo.mark_expired.assert_awaited_once_with(
        workspace_id=ws_id,
        threshold_days=180,
    )


@pytest.mark.asyncio
async def test_lifecycle_purge_user_memories_gdpr() -> None:
    ws_id = uuid4()
    user_id = uuid4()
    sess_1, sess_2 = uuid4(), uuid4()

    mock_session = MagicMock(spec=AsyncSession)
    # Mock user sessions query execution
    fake_result = MagicMock()
    fake_result.scalars.return_value.all.return_value = [sess_1, sess_2]
    mock_session.execute = AsyncMock(return_value=fake_result)

    mock_repo = MagicMock(spec=MemoryRepository)
    mock_repo.purge_user_memories = AsyncMock(return_value=12)

    mock_working_memory = MagicMock(spec=WorkingMemoryManager)
    mock_working_memory.clear_session = AsyncMock(return_value=True)

    manager = MemoryLifecycleManager(
        repository=mock_repo,
        working_memory=mock_working_memory,
    )

    purged_count = await manager.purge_user_memories(
        session_or_conn=mock_session,
        workspace_id=ws_id,
        user_id=user_id,
        scope="all",
    )

    assert purged_count == 12
    mock_repo.purge_user_memories.assert_awaited_once_with(
        workspace_id=ws_id,
        user_id=user_id,
        category=None,
    )
    # Verify working memory was cleared for each session of user
    assert mock_working_memory.clear_session.await_count == 2
    mock_working_memory.clear_session.assert_any_await(
        workspace_id=ws_id,
        session_id=sess_1,
    )
    mock_working_memory.clear_session.assert_any_await(
        workspace_id=ws_id,
        session_id=sess_2,
    )
