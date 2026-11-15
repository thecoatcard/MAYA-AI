"""Distributed leases for bounding active chat streams per user."""

from dataclasses import dataclass
from typing import Protocol
from uuid import UUID, uuid4

from redis.asyncio import Redis
from redis.exceptions import RedisError


class ChatStreamLimiterUnavailable(RuntimeError):
    """Redis could not check or release a chat stream lease."""

    def __init__(self) -> None:
        super().__init__("Chat stream limiter unavailable")


@dataclass(frozen=True, slots=True)
class ChatStreamLease:
    key: str
    member: str


class ChatStreamLimiter(Protocol):
    async def acquire(
        self,
        workspace_id: UUID,
        user_id: UUID,
        *,
        limit: int,
        lease_seconds: int,
    ) -> ChatStreamLease | None: ...

    async def renew(self, lease: ChatStreamLease, *, lease_seconds: int) -> bool: ...

    async def release(self, lease: ChatStreamLease) -> None: ...


class RedisChatStreamLimiter:
    _ACQUIRE = """
local time = redis.call('TIME')
local now = time[1] * 1000 + math.floor(time[2] / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZCARD', KEYS[1]) >= tonumber(ARGV[1]) then
    return 0
end
redis.call('ZADD', KEYS[1], now + tonumber(ARGV[2]), ARGV[3])
redis.call('PEXPIRE', KEYS[1], ARGV[2])
return 1
"""
    _RELEASE = """
redis.call('ZREM', KEYS[1], ARGV[1])
if redis.call('ZCARD', KEYS[1]) == 0 then
    redis.call('DEL', KEYS[1])
end
return 1
"""
    _RENEW = """
local time = redis.call('TIME')
local now = time[1] * 1000 + math.floor(time[2] / 1000)
redis.call('ZREMRANGEBYSCORE', KEYS[1], '-inf', now)
if redis.call('ZSCORE', KEYS[1], ARGV[1]) == false then
    return 0
end
redis.call('ZADD', KEYS[1], 'XX', now + tonumber(ARGV[2]), ARGV[1])
redis.call('PEXPIRE', KEYS[1], ARGV[2])
return 1
"""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def acquire(
        self,
        workspace_id: UUID,
        user_id: UUID,
        *,
        limit: int,
        lease_seconds: int,
    ) -> ChatStreamLease | None:
        if limit < 1 or lease_seconds < 1:
            raise ValueError("A positive stream limit and lease duration are required")
        key = f"maya:chat:streams:{workspace_id}:{user_id}"
        member = uuid4().hex
        try:
            acquired = await self._redis.eval(
                self._ACQUIRE, 1, key, limit, lease_seconds * 1000, member
            )
        except RedisError:
            raise ChatStreamLimiterUnavailable from None
        return ChatStreamLease(key, member) if int(acquired) == 1 else None

    async def renew(self, lease: ChatStreamLease, *, lease_seconds: int) -> bool:
        if lease_seconds < 1:
            raise ValueError("A positive stream lease duration is required")
        try:
            renewed = await self._redis.eval(
                self._RENEW, 1, lease.key, lease.member, lease_seconds * 1000
            )
        except RedisError:
            raise ChatStreamLimiterUnavailable from None
        return int(renewed) == 1

    async def release(self, lease: ChatStreamLease) -> None:
        try:
            await self._redis.eval(self._RELEASE, 1, lease.key, lease.member)
        except RedisError:
            raise ChatStreamLimiterUnavailable from None
