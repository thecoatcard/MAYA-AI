"""Pending sign-up state (the one-time code issued before an account exists).

The Redis implementation is the production one: state survives a restart and is shared by all
workers, and the key expires with the code. The in-memory implementation is the test double
(and a single-process fallback for tools that have no Redis).
"""

from dataclasses import dataclass
from datetime import datetime
from typing import Protocol
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

from app.auth.errors import ServiceUnavailable
from app.core.security_otp import OTP_LIFETIME, identifier_digest


@dataclass
class PendingSignup:
    challenge_id: UUID
    email: str
    code_hmac: bytes
    created_at: datetime
    expires_at: datetime
    attempts: int = 0


class PendingSignupStore(Protocol):
    async def get(self, email: str) -> PendingSignup | None: ...

    async def put(self, pending: PendingSignup) -> None: ...

    async def record_attempt(self, email: str) -> None:
        """Count one guess against the pending code (no-op if it is gone)."""
        ...

    async def delete(self, email: str) -> None: ...


class InMemoryPendingSignups:
    def __init__(self) -> None:
        self._items: dict[str, PendingSignup] = {}

    async def get(self, email: str) -> PendingSignup | None:
        return self._items.get(email)

    async def put(self, pending: PendingSignup) -> None:
        self._items[pending.email] = pending

    async def record_attempt(self, email: str) -> None:
        item = self._items.get(email)
        if item is not None:
            item.attempts += 1

    async def delete(self, email: str) -> None:
        self._items.pop(email, None)


class RedisPendingSignups:
    def __init__(self, redis: Redis, otp_key: bytes) -> None:
        self._redis = redis
        self._otp_key = otp_key

    def _name(self, email: str) -> str:
        return f"maya:signup:{identifier_digest(self._otp_key, 'signup-pending', email)}"

    async def get(self, email: str) -> PendingSignup | None:
        try:
            raw = await self._redis.hgetall(self._name(email))
        except RedisError:
            raise ServiceUnavailable from None
        if not raw:
            return None
        values = {
            (k.decode() if isinstance(k, bytes) else k): (v.decode() if isinstance(v, bytes) else v)
            for k, v in raw.items()
        }
        try:
            return PendingSignup(
                challenge_id=UUID(values["challenge_id"]),
                email=email,
                code_hmac=bytes.fromhex(values["code_hmac"]),
                created_at=datetime.fromisoformat(values["created_at"]),
                expires_at=datetime.fromisoformat(values["expires_at"]),
                attempts=int(values.get("attempts", "0")),
            )
        except (KeyError, ValueError):
            return None

    async def put(self, pending: PendingSignup) -> None:
        name = self._name(pending.email)
        mapping = {
            "challenge_id": str(pending.challenge_id),
            "code_hmac": pending.code_hmac.hex(),
            "created_at": pending.created_at.isoformat(),
            "expires_at": pending.expires_at.isoformat(),
            "attempts": str(pending.attempts),
        }
        try:
            async with self._redis.pipeline(transaction=True) as pipeline:
                pipeline.delete(name)
                pipeline.hset(name, mapping=mapping)  # type: ignore[arg-type]
                pipeline.expire(name, int(OTP_LIFETIME.total_seconds()))
                await pipeline.execute()
        except RedisError:
            raise ServiceUnavailable from None

    async def record_attempt(self, email: str) -> None:
        try:
            name = self._name(email)
            if await self._redis.exists(name):
                await self._redis.hincrby(name, "attempts", 1)
        except RedisError:
            raise ServiceUnavailable from None

    async def delete(self, email: str) -> None:
        try:
            await self._redis.delete(self._name(email))
        except RedisError:
            raise ServiceUnavailable from None
