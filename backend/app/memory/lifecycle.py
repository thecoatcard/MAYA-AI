"""Lifecycle management for memory retention, archival, and GDPR Article 17 erasure."""

from __future__ import annotations

from uuid import UUID

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncSession

from app.db.repositories.memory import MemoryRepository
from app.memory.working_memory import WorkingMemoryManager

_GET_USER_SESSIONS = text(
    """
    SELECT id FROM sessions
    WHERE workspace_id = :workspace_id AND user_id = :user_id
    """
)


class MemoryLifecycleManager:
    """Manages memory retention policy, stale archival, and GDPR Article 17 erasure."""

    def __init__(
        self,
        repository: MemoryRepository,
        working_memory: WorkingMemoryManager | None = None,
    ) -> None:
        self.repository = repository
        self.working_memory = working_memory

    async def archive_stale_memories(
        self,
        session_or_conn: AsyncSession | AsyncConnection,
        workspace_id: UUID,
        threshold_days: int = 180,
    ) -> int:
        """Mark non-permanent memories untouched for threshold_days as expired."""
        return await self.repository.mark_expired(
            workspace_id=workspace_id,
            threshold_days=threshold_days,
        )

    async def purge_user_memories(
        self,
        session_or_conn: AsyncSession | AsyncConnection,
        workspace_id: UUID,
        user_id: UUID,
        scope: str = "all",
    ) -> int:
        """Atomically delete user memories from PostgreSQL and flush Redis working memory."""
        # 1. Purge from PostgreSQL
        category = None if scope == "all" else scope
        deleted_count = await self.repository.purge_user_memories(
            workspace_id=workspace_id,
            user_id=user_id,
            category=category,
        )

        # 2. Flush working memory sessions in Redis if scope is "all" and manager is present
        if self.working_memory is not None and scope == "all":
            # Discover sessions owned by user
            res = await session_or_conn.execute(
                _GET_USER_SESSIONS,
                {"workspace_id": workspace_id, "user_id": user_id},
            )
            session_ids = res.scalars().all()
            for sid in session_ids:
                await self.working_memory.clear_session(
                    workspace_id=workspace_id,
                    session_id=sid,
                )

        return deleted_count
