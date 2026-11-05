"""Workspace-scoped context cache manager and provider 404 recovery."""

import hashlib
import json
import logging
from typing import Any
from uuid import UUID

from redis.asyncio import Redis

logger = logging.getLogger(__name__)

CACHE_KEY_PREFIX = "maya:cache"
DEFAULT_CACHE_TTL_SECONDS = 3600  # 1 hour default provider cache TTL


def compute_content_hash(content: str | bytes | list[Any] | dict[str, Any]) -> str:
    """Compute deterministic SHA-256 hash for cacheable prompt content."""
    if isinstance(content, bytes):
        raw_bytes = content
    elif isinstance(content, str):
        raw_bytes = content.encode("utf-8")
    else:
        raw_bytes = json.dumps(content, sort_keys=True, default=str).encode("utf-8")
    return hashlib.sha256(raw_bytes).hexdigest()


class ContextCacheManager:
    """Manages workspace-isolated Gemini Context Caches backed by Redis with 404 recovery."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    def make_cache_key(self, workspace_id: UUID, model_id: str, content_hash: str) -> str:
        """Build the isolated Redis key for a workspace context cache."""
        return f"{CACHE_KEY_PREFIX}:{workspace_id}:{model_id}:{content_hash}"

    async def get_cached_content_name(
        self,
        workspace_id: UUID | None,
        model_id: str,
        content_hash: str,
    ) -> str | None:
        """Lookup an active provider cache name for this workspace and content.

        Raises:
            ValueError: If workspace_id is None (context caching is strictly tenant-scoped).
        """
        if workspace_id is None:
            raise ValueError("workspace_id is mandatory for context cache lookup")

        key = self.make_cache_key(workspace_id, model_id, content_hash)
        name = await self._redis.get(key)
        if name is None:
            return None
        return name if isinstance(name, str) else name.decode("utf-8")

    async def set_cached_content_name(
        self,
        workspace_id: UUID | None,
        model_id: str,
        content_hash: str,
        cache_name: str,
        ttl_seconds: int = DEFAULT_CACHE_TTL_SECONDS,
    ) -> None:
        """Store the provider cache resource name in Redis under the workspace scope."""
        if workspace_id is None:
            raise ValueError("workspace_id is mandatory for storing context cache")

        key = self.make_cache_key(workspace_id, model_id, content_hash)
        await self._redis.set(key, cache_name, ex=ttl_seconds)
        logger.info(
            "context_cache_stored workspace_id=%s model_id=%s ttl=%d",
            workspace_id,
            model_id,
            ttl_seconds,
        )

    async def invalidate_cache(
        self,
        workspace_id: UUID | None,
        model_id: str,
        content_hash: str,
    ) -> None:
        """Remove a cached content reference from Redis."""
        if workspace_id is None:
            raise ValueError("workspace_id is mandatory for context cache invalidation")

        key = self.make_cache_key(workspace_id, model_id, content_hash)
        await self._redis.delete(key)
        logger.info(
            "context_cache_invalidated workspace_id=%s model_id=%s",
            workspace_id,
            model_id,
        )

    async def handle_provider_cache_404(
        self,
        workspace_id: UUID | None,
        model_id: str,
        content_hash: str,
    ) -> None:
        """Handle provider-side 404 (cache expired on Google side before local TTL).

        Purges the stale Redis key so subsequent calls do not attempt the expired cache.
        """
        if workspace_id is None:
            return

        logger.warning(
            "context_cache_provider_404_purging_key workspace_id=%s model_id=%s content_hash=%s",
            workspace_id,
            model_id,
            content_hash,
        )
        await self.invalidate_cache(workspace_id, model_id, content_hash)
