"""Voice Session Resumption and Reconnection Manager.

Manages session resumption tokens and transparent parallel rotation at the 10-minute
mark to prevent audio drops before the 15-minute hard limit.
"""

from __future__ import annotations

import logging
import time
from typing import TYPE_CHECKING
from uuid import UUID, uuid4

from app.voice.live_session import LiveVoiceSession, OutgoingMessage, OutgoingMessageType

if TYPE_CHECKING:
    from redis.asyncio import Redis

logger = logging.getLogger(__name__)

DEFAULT_RESUMPTION_PREFIX = "voice:resumption"
DEFAULT_ROTATION_THRESHOLD = 600.0  # 10 minutes
DEFAULT_EXPIRY_THRESHOLD = 900.0  # 15 minutes


class VoiceSessionManager:
    """Manages session tokens and transparent rotation for real-time voice sessions."""

    def __init__(
        self,
        redis_client: Redis | None = None,
        rotation_threshold_seconds: float = DEFAULT_ROTATION_THRESHOLD,
        expiry_threshold_seconds: float = DEFAULT_EXPIRY_THRESHOLD,
        key_prefix: str = DEFAULT_RESUMPTION_PREFIX,
    ) -> None:
        self._redis = redis_client
        self.rotation_threshold = rotation_threshold_seconds
        self.expiry_threshold = expiry_threshold_seconds
        self.key_prefix = key_prefix
        # In-memory fallback if Redis is not connected: {key: (token, expire_at)}
        self._memory_tokens: dict[str, tuple[str, float]] = {}

    def _make_key(self, workspace_id: UUID, session_id: str) -> str:
        return f"{self.key_prefix}:{workspace_id}:{session_id}"

    async def store_resumption_token(
        self,
        workspace_id: UUID,
        session_id: str,
        token: str,
        ttl_seconds: int = 900,
    ) -> None:
        """Store a session resumption token with a TTL."""
        key = self._make_key(workspace_id, session_id)
        if self._redis is not None:
            try:
                await self._redis.set(key, token, ex=ttl_seconds)
                return
            except Exception as err:
                logger.warning("redis_store_resumption_token_failed: %s", err)

        # Fallback to in-memory store
        self._memory_tokens[key] = (token, time.time() + ttl_seconds)

    async def get_resumption_token(self, workspace_id: UUID, session_id: str) -> str | None:
        """Retrieve stored resumption token if not expired."""
        key = self._make_key(workspace_id, session_id)
        if self._redis is not None:
            try:
                val = await self._redis.get(key)
                if val is not None:
                    return val.decode("utf-8") if isinstance(val, bytes) else str(val)
                return None
            except Exception as err:
                logger.warning("redis_get_resumption_token_failed: %s", err)

        # Check in-memory store
        item = self._memory_tokens.get(key)
        if item is not None:
            token, expire_at = item
            if time.time() <= expire_at:
                return token
            del self._memory_tokens[key]
        return None

    async def invalidate_resumption_token(self, workspace_id: UUID, session_id: str) -> None:
        """Invalidate and remove a resumption token."""
        key = self._make_key(workspace_id, session_id)
        if self._redis is not None:
            try:
                await self._redis.delete(key)
            except Exception as err:
                logger.warning("redis_delete_resumption_token_failed: %s", err)
        self._memory_tokens.pop(key, None)

    def should_rotate_session(
        self, session_created_at: float, current_time: float | None = None
    ) -> bool:
        """Check if session has reached the 10-minute rotation threshold before 15-minute cutoff."""
        now = current_time if current_time is not None else time.time()
        elapsed = now - session_created_at
        return elapsed >= self.rotation_threshold and not self.is_session_expired(
            session_created_at, now
        )

    def is_session_expired(
        self, session_created_at: float, current_time: float | None = None
    ) -> bool:
        """Check if session has exceeded the 15-minute hard limit."""
        now = current_time if current_time is not None else time.time()
        return (now - session_created_at) >= self.expiry_threshold

    async def rotate_session(self, old_session: LiveVoiceSession) -> LiveVoiceSession:
        """Transparently rotate an active session to a new instance preserving context."""
        new_session_id = str(uuid4())
        new_session = LiveVoiceSession(
            workspace_id=old_session.workspace_id,
            user_id=old_session.user_id,
            session_id=new_session_id,
            tool_registry=old_session.tool_registry,
            model_gateway=old_session.model_gateway,
            mock_mode=old_session.mock_mode,
        )

        # Preserve active tracked tasks
        new_session._active_task_ids = set(old_session._active_task_ids)

        # Generate and store a resumption token
        token = f"token-renewed-{new_session_id}"
        await self.store_resumption_token(
            old_session.workspace_id,
            new_session_id,
            token,
            ttl_seconds=int(self.expiry_threshold),
        )

        # Notify client of transparent renewal
        renewal_msg = OutgoingMessage(
            OutgoingMessageType.CONTROL,
            payload={"type": "SESSION_RENEWED", "expires_in": int(self.rotation_threshold)},
        )
        await new_session._outgoing_queue.put(renewal_msg)

        # Close old session
        await old_session.close()
        logger.info(
            "session_rotated_transparently old=%s new=%s",
            old_session.session_id,
            new_session_id,
        )
        return new_session
