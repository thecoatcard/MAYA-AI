"""Fixed-window rate limiting; the window comes from the injected clock (no sleeping in tests)."""

from dataclasses import dataclass
from typing import Protocol

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.core.clock import Clock


@dataclass(frozen=True)
class Limit:
    count: int
    window_seconds: int


class RateLimiterUnavailable(RuntimeError):
    """The limiter backend failed. Callers fail closed rather than skip the limit."""

    def __init__(self) -> None:
        super().__init__("Rate limiter unavailable")


class RateLimiter(Protocol):
    async def hit(self, key: str, limit: Limit) -> bool:
        """Record one hit atomically; True if it is still within the limit."""
        ...

    async def release(self, key: str, limit: Limit) -> None:
        """Give one hit back (a login that turned out to be correct is not a failed guess)."""
        ...


class RedisRateLimiter:
    """Counters in Redis. A fixed window allows up to twice the limit across a boundary."""

    def __init__(self, redis: Redis, clock: Clock) -> None:
        self._redis = redis
        self._clock = clock

    def _name(self, key: str, limit: Limit) -> str:
        window = int(self._clock.now().timestamp()) // limit.window_seconds
        return f"maya:rl:{key}:{window}"

    async def hit(self, key: str, limit: Limit) -> bool:
        name = self._name(key, limit)
        try:
            async with self._redis.pipeline(transaction=True) as pipeline:
                pipeline.incr(name)
                pipeline.expire(name, limit.window_seconds * 2)
                count, _ = await pipeline.execute()
        except RedisError:
            raise RateLimiterUnavailable from None
        return int(count) <= limit.count

    async def release(self, key: str, limit: Limit) -> None:
        name = self._name(key, limit)
        try:
            async with self._redis.pipeline(transaction=True) as pipeline:
                pipeline.decr(name)
                pipeline.expire(name, limit.window_seconds * 2)
                value, _ = await pipeline.execute()
            if int(value) < 0:  # the window rolled over since the hit; do not go below zero
                await self._redis.incr(name)
        except RedisError:
            raise RateLimiterUnavailable from None
