"""Database Persistence Service for Voice Sessions (Phase 12 / 14).

Ensures voice sessions are persisted to PostgreSQL sessions table (channel='voice')
and spoken transcript turns are recorded into messages table (modality='audio')
with full multi-tenant Row-Level Security (RLS) enforcement.
"""

from __future__ import annotations

import logging
from typing import Any
from uuid import UUID, uuid4

from app.db.repositories.chat import ChatRepository
from app.db.tenant import tenant_session

logger = logging.getLogger(__name__)


class VoicePersistenceService:
    """Manages session creation and message turn persistence for Voice interactions."""

    def __init__(self, db_engine: Any) -> None:
        self.engine = db_engine

    async def get_or_create_voice_session(
        self,
        workspace_id: UUID,
        user_id: UUID,
        session_id: UUID | None = None,
        title: str = "Voice Conversation",
    ) -> UUID:
        """Ensure a PostgreSQL session row exists with channel='voice'."""
        if self.engine is None:
            # Fallback for environments without database engine
            return session_id or uuid4()

        try:
            async with tenant_session(self.engine, workspace_id, user_id) as session:
                connection = await session.connection()
                repo = ChatRepository(connection, workspace_id, user_id)

                if session_id is not None:
                    existing = await repo.get_session(session_id)
                    if existing is not None:
                        return existing.id

                created = await repo.create_session(title=title, channel="voice")
                await session.commit()
                logger.info(
                    "voice_session_persisted workspace_id=%s user_id=%s session_id=%s",
                    workspace_id,
                    user_id,
                    created.id,
                )
                return created.id
        except Exception as err:
            logger.warning("voice_session_persistence_failed: %s", err)
            return session_id or uuid4()

    async def save_turn(
        self,
        workspace_id: UUID,
        user_id: UUID,
        session_id: UUID,
        sender: str,  # 'user' | 'orchestrator'
        content: str,
        metadata: dict[str, Any] | None = None,
    ) -> UUID:
        """Persist a completed speech transcript turn to messages table."""
        if not content or not content.strip() or self.engine is None:
            return uuid4()

        try:
            async with tenant_session(self.engine, workspace_id, user_id) as session:
                connection = await session.connection()
                repo = ChatRepository(connection, workspace_id, user_id)

                msg = await repo.add_message(
                    session_id=session_id,
                    sender=sender,  # type: ignore[arg-type]
                    content=content.strip(),
                    modality="audio",
                    metadata=metadata or {},
                )
                await session.commit()
                logger.info(
                    "voice_message_persisted session_id=%s message_id=%s sender=%s",
                    session_id,
                    msg.id,
                    sender,
                )
                return msg.id
        except Exception as err:
            logger.warning("voice_turn_persistence_failed: %s", err)
            return uuid4()
