"""Working memory manager for active session state in Redis.

Maintains short-term conversational turns with 24-hour TTL (86400s),
turn append, and rapid retrieval.
"""

from __future__ import annotations

import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4


@dataclass
class ConversationTurn:
    """A single dialogue turn within working memory."""

    role: str
    content: str
    turn_id: str | None = None
    timestamp: str | None = None
    metadata: dict[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        return {
            "role": self.role,
            "content": self.content,
            "turn_id": self.turn_id,
            "timestamp": self.timestamp,
            "metadata": self.metadata or {},
        }

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> ConversationTurn:
        return cls(
            role=data.get("role", "user"),
            content=data.get("content", ""),
            turn_id=data.get("turn_id"),
            timestamp=data.get("timestamp"),
            metadata=data.get("metadata", {}),
        )


class WorkingMemoryManager:
    """Manages active conversation turns in Redis with a 24-hour TTL."""

    def __init__(self, redis_client: Any) -> None:
        self.redis = redis_client

    def session_key(self, workspace_id: UUID, session_id: UUID) -> str:
        return f"memory:working:{workspace_id}:{session_id}"

    async def append_turn(
        self,
        workspace_id: UUID,
        session_id: UUID,
        role: str,
        content: str,
        metadata: dict[str, Any] | None = None,
        turn_id: str | None = None,
        ttl_seconds: int = 86400,
    ) -> None:
        """Append a conversational turn to Redis and refresh 24h TTL."""
        key = self.session_key(workspace_id, session_id)
        now_str = datetime.now(UTC).isoformat()
        turn = ConversationTurn(
            role=role,
            content=content,
            turn_id=turn_id or str(uuid4()),
            timestamp=now_str,
            metadata=metadata or {},
        )
        payload = json.dumps(turn.to_dict())
        await self.redis.rpush(key, payload)
        await self.redis.expire(key, ttl_seconds)

    async def get_recent_turns(
        self,
        workspace_id: UUID,
        session_id: UUID,
        limit: int = 20,
    ) -> list[ConversationTurn]:
        """Fetch the most recent turns for a session from Redis."""
        key = self.session_key(workspace_id, session_id)
        start = -limit if limit > 0 else 0
        raw_items = await self.redis.lrange(key, start, -1)
        turns: list[ConversationTurn] = []

        for item in raw_items:
            try:
                text_item = item if isinstance(item, str) else item.decode("utf-8")
                parsed = json.loads(text_item)
                if isinstance(parsed, dict):
                    turns.append(ConversationTurn.from_dict(parsed))
            except Exception:
                continue

        return turns

    async def clear_session(
        self,
        workspace_id: UUID,
        session_id: UUID,
    ) -> bool:
        """Remove working memory session state from Redis."""
        key = self.session_key(workspace_id, session_id)
        res = await self.redis.delete(key)
        return bool(res > 0)
