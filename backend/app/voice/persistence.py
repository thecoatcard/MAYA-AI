"""Persistence for voice turns in the shared conversation tables (Phase 12 / 14).

Voice and text chat are one conversation: a voice connection attaches to a chat ``sessions`` row
(channel ``web``) and each completed turn is one ``messages`` row (``modality`` ``audio`` for spoken
turns, ``text`` for typed ones). Failures raise ``VoicePersistenceError`` or ``SessionNotOwned``;
nothing is swallowed and no random id is ever returned in place of a real one.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any
from uuid import UUID

from app.db.repositories.chat import ChatRepository, SessionNotFound
from app.db.tenant import tenant_session

logger = logging.getLogger(__name__)

DEFAULT_TITLE = "Voice conversation"


class VoicePersistenceError(Exception):
    """A database operation for a voice turn or session failed."""


class SessionNotOwned(Exception):
    """The requested session does not exist or is not this user's."""


@dataclass(frozen=True)
class SessionInfo:
    id: UUID
    title: str | None
    created: bool


class VoicePersistenceService:
    """Session lookup/creation, per-turn saves and history seeding for voice."""

    def __init__(self, db_engine: Any) -> None:
        self.engine = db_engine

    @property
    def enabled(self) -> bool:
        return self.engine is not None

    async def get_session(self, workspace_id: UUID, user_id: UUID, session_id: UUID) -> SessionInfo:
        """An existing session the user owns. Raises ``SessionNotOwned``."""
        if self.engine is None:
            return SessionInfo(session_id, None, False)
        try:
            async with tenant_session(self.engine, workspace_id, user_id) as session:
                repo = ChatRepository(await session.connection(), workspace_id, user_id)
                record = await repo.get_session(session_id)
        except Exception as err:
            raise VoicePersistenceError("session lookup failed") from err
        if record is None:
            raise SessionNotOwned
        return SessionInfo(record.id, record.title, False)

    async def create_session(
        self, workspace_id: UUID, user_id: UUID, title: str = DEFAULT_TITLE
    ) -> SessionInfo:
        """A new web-channel conversation. Without a database the id is process-local."""
        if self.engine is None:
            from uuid import uuid4

            return SessionInfo(uuid4(), title, True)
        try:
            async with tenant_session(self.engine, workspace_id, user_id) as session:
                repo = ChatRepository(await session.connection(), workspace_id, user_id)
                created = await repo.create_session(title=title, channel="web")
                await session.commit()
        except Exception as err:
            raise VoicePersistenceError("session creation failed") from err
        return SessionInfo(created.id, created.title, True)

    async def save_turn(
        self,
        workspace_id: UUID,
        user_id: UUID,
        session_id: UUID,
        *,
        role: str,  # "user" | "assistant"
        content: str,
        modality: str,  # "audio" | "text"
        metadata: dict[str, Any] | None = None,
    ) -> UUID | None:
        """Persist one completed turn; returns its message id (None when no database).

        Takes the session row lock first so ordering against concurrent REST chat is serial.
        """
        text = content.strip()
        if not text or self.engine is None:
            return None
        sender = "user" if role == "user" else "orchestrator"
        try:
            async with tenant_session(self.engine, workspace_id, user_id) as session:
                repo = ChatRepository(await session.connection(), workspace_id, user_id)
                await repo.lock_session(session_id)
                message = await repo.add_message(
                    session_id=session_id,
                    sender=sender,  # type: ignore[arg-type]
                    content=text,
                    modality=modality,  # type: ignore[arg-type]
                    metadata=metadata or {},
                )
                await session.commit()
        except SessionNotFound as err:
            raise VoicePersistenceError("session not found while saving turn") from err
        except Exception as err:
            raise VoicePersistenceError("turn save failed") from err
        return message.id

    async def load_seed(
        self, workspace_id: UUID, user_id: UUID, session_id: UUID, limit: int
    ) -> list[tuple[str, str]]:
        """The last ``limit`` turns as ``(role, text)``, oldest first, for seeding the model."""
        if self.engine is None:
            return []
        try:
            async with tenant_session(self.engine, workspace_id, user_id) as session:
                repo = ChatRepository(await session.connection(), workspace_id, user_id)
                records = await repo.recent_messages(session_id, limit)
        except Exception as err:
            raise VoicePersistenceError("history load failed") from err
        return [(r.role, r.content) for r in records if r.sender in ("user", "orchestrator")]
