"""Run locks: only one runner executes a given schedule step at a time.

Redis ``SET NX EX`` when Redis answers; an in-process lock otherwise (a single backend process is
then the only runner anyway). The lock has a TTL, so a crashed runner never blocks forever.
"""

from __future__ import annotations

import logging
import secrets
import time
from collections.abc import Callable
from typing import Any, Protocol

logger = logging.getLogger("maya.schedules")


class RunLock(Protocol):
    async def acquire(self, key: str, ttl_seconds: int) -> str | None:
        """A token when the lock is taken, ``None`` when someone else holds it."""
        ...

    async def release(self, key: str, token: str) -> None: ...


class LocalRunLock:
    def __init__(self, clock: Callable[[], float] = time.monotonic) -> None:
        self._held: dict[str, tuple[str, float]] = {}
        self._clock = clock

    async def acquire(self, key: str, ttl_seconds: int) -> str | None:
        now = self._clock()
        held = self._held.get(key)
        if held is not None and held[1] > now:
            return None
        token = secrets.token_hex(8)
        self._held[key] = (token, now + ttl_seconds)
        return token

    async def release(self, key: str, token: str) -> None:
        held = self._held.get(key)
        if held is not None and held[0] == token:
            del self._held[key]


_RELEASE = (
    "if redis.call('get', KEYS[1]) == ARGV[1] then "
    "return redis.call('del', KEYS[1]) else return 0 end"
)


class RedisRunLock:
    """Distributed lock with a local fallback while Redis is unreachable."""

    def __init__(self, redis: Any, fallback: LocalRunLock | None = None) -> None:
        self._redis = redis
        self._fallback = fallback or LocalRunLock()
        self._fell_back: dict[str, str] = {}

    async def acquire(self, key: str, ttl_seconds: int) -> str | None:
        token = secrets.token_hex(8)
        try:
            taken = await self._redis.set(
                f"maya:schedules:lock:{key}", token, nx=True, ex=ttl_seconds
            )
        except Exception:  # noqa: BLE001 - Redis down: the in-process lock still serialises us
            logger.warning("schedule_lock_redis_unavailable")
            local = await self._fallback.acquire(key, ttl_seconds)
            if local is not None:
                self._fell_back[key] = local
            return local
        return token if taken else None

    async def release(self, key: str, token: str) -> None:
        if self._fell_back.get(key) == token:
            del self._fell_back[key]
            await self._fallback.release(key, token)
            return
        try:
            await self._redis.eval(_RELEASE, 1, f"maya:schedules:lock:{key}", token)
        except Exception:  # noqa: BLE001 - the TTL frees it
            logger.warning("schedule_lock_release_failed")
