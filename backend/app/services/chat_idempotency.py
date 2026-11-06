"""Idempotency of ``POST /api/v1/chat/message`` (ADR-0008, ``docs/api/CHAT.md``).

A request is identified by ``(workspace, user, Idempotency-Key)``. The first request claims the key
atomically in Redis (``SET NX``) and records a hash of ``session_id`` and ``content``. Everything
that follows is decided from that record:

* a different hash is a different request: ``idempotency_key_reused``;
* the same hash while the first request is still running: ``request_in_progress``;
* the same hash after it ended: the stored result is replayed from the database, with no model call.

The record never holds message content, only identifiers, a keyed hash, a state and a code. A
claim that is still ``in_progress`` expires after fifteen minutes (a crashed request must not block
its key for a day, and fifteen minutes outlasts the slowest gateway retry-and-fallback sequence
before the first chunk); the service refreshes it while a long reply streams. Terminal states are
kept 24 hours. A write only replaces a record that belongs to the same attempt, so a request whose
claim expired cannot overwrite the claim or the result of the request that took the key over.

Every Redis failure raises ``IdempotencyUnavailable``. Callers fail closed: without the record the
service could not promise that a retry does not call (and bill) the model a second time.
"""

import hashlib
import hmac
import json
import logging
import re
from dataclasses import dataclass
from typing import Any, Final, Literal, Protocol, cast
from uuid import UUID

from redis.asyncio import Redis
from redis.exceptions import RedisError

logger = logging.getLogger("maya")

KEY_PATTERN: Final = re.compile(r"[A-Za-z0-9_-]{8,128}")
IN_PROGRESS_TTL_SECONDS: Final = 15 * 60
TERMINAL_TTL_SECONDS: Final = 24 * 60 * 60

_KEY_PREFIX: Final = "maya:chat:idem"
_RECORD_VERSION: Final = 1
_CLAIM_ATTEMPTS: Final = 3
_HASH_PATTERN: Final = re.compile(r"[0-9a-f]{64}")
_FAILURE_CODES: Final = frozenset(
    {
        "content_blocked",
        "reply_deadline_exceeded",
        "stream_interrupted",
        "model_unavailable",
        "internal_error",
    }
)

State = Literal["in_progress", "completed", "interrupted", "failed"]
_STATES: Final = frozenset({"in_progress", "completed", "interrupted", "failed"})


class IdempotencyUnavailable(Exception):
    """Redis could not be reached, or the stored record is unreadable. Callers fail closed."""

    def __init__(self) -> None:
        super().__init__("Idempotency store unavailable")


@dataclass(frozen=True, slots=True)
class IdempotencyScope:
    """Whose key it is. The same key text from another user or workspace is a different key."""

    workspace_id: UUID
    user_id: UUID
    key: str

    @property
    def redis_key(self) -> str:
        return f"{_KEY_PREFIX}:{self.workspace_id}:{self.user_id}:{self.key}"


@dataclass(frozen=True, slots=True)
class IdempotencyRecord:
    """The state of one request. ``session_id`` is ``None`` only while a request that asked the
    server to create a session has not yet reported the new id. ``finish_reason`` is set for
    ``completed`` and ``interrupted``, ``error_code`` for ``failed``."""

    state: State
    request_hash: str
    session_id: UUID | None
    user_message_id: UUID
    assistant_message_id: UUID
    finish_reason: str | None = None
    error_code: str | None = None

    def to_json(self) -> str:
        return json.dumps(
            {
                "v": _RECORD_VERSION,
                "state": self.state,
                "hash": self.request_hash,
                "session_id": None if self.session_id is None else str(self.session_id),
                "user_message_id": str(self.user_message_id),
                "assistant_message_id": str(self.assistant_message_id),
                "finish_reason": self.finish_reason,
                "error_code": self.error_code,
            },
            separators=(",", ":"),
        )

    @classmethod
    def from_json(cls, raw: str | bytes) -> "IdempotencyRecord":
        """Parse a stored record. Raises ``ValueError`` for anything this module did not write."""
        try:
            body = json.loads(raw)
            if not isinstance(body, dict) or body.get("v") != _RECORD_VERSION:
                raise ValueError("unsupported record")
            fields = cast(dict[str, Any], body)
            state = fields["state"]
            request_digest = fields["hash"]
            if state not in _STATES or not isinstance(request_digest, str):
                raise ValueError("invalid record")
            if _HASH_PATTERN.fullmatch(request_digest) is None:
                raise ValueError("invalid record")
            session = fields["session_id"]
            finish_reason = fields["finish_reason"]
            error_code = fields["error_code"]
            if finish_reason is not None and not isinstance(finish_reason, str):
                raise ValueError("invalid record")
            if error_code is not None and error_code not in _FAILURE_CODES:
                raise ValueError("invalid record")
            return cls(
                state=state,
                request_hash=request_digest,
                session_id=None if session is None else UUID(session),
                user_message_id=UUID(fields["user_message_id"]),
                assistant_message_id=UUID(fields["assistant_message_id"]),
                finish_reason=finish_reason,
                error_code=error_code,
            )
        except (KeyError, TypeError, AttributeError) as error:
            raise ValueError("invalid record") from error


def request_hash(
    secret: bytes, scope: IdempotencyScope, session_id: UUID | None, content: str
) -> str:
    """Keyed hash (HMAC-SHA-256) of what makes a request "the same": the session and the text.

    The workspace, the user and the key are mixed in and the key is secret, so a reader of the
    Redis record cannot test guesses of a short message offline and one table does not fit every
    user. Each part is length-prefixed, so no two different inputs produce the same byte string.
    """
    subkey = hmac.new(secret, b"maya:chat:request-hash:v1", hashlib.sha256).digest()
    mac = hmac.new(subkey, digestmod=hashlib.sha256)
    parts = (
        str(scope.workspace_id).encode("ascii"),
        str(scope.user_id).encode("ascii"),
        scope.key.encode("ascii"),
        b"" if session_id is None else str(session_id).encode("ascii"),
        content.encode("utf-8", errors="surrogatepass"),
    )
    for part in parts:
        mac.update(len(part).to_bytes(8, "big"))
        mac.update(part)
    return mac.hexdigest()


class IdempotencyStore(Protocol):
    """Where idempotency records live. ``RedisIdempotencyStore`` is the production one."""

    async def claim(
        self, scope: IdempotencyScope, record: IdempotencyRecord
    ) -> IdempotencyRecord | None:
        """Store ``record`` if the key is free and return ``None``; otherwise return the record
        already stored under the key."""
        ...

    async def save(self, scope: IdempotencyScope, record: IdempotencyRecord) -> None:
        """Write the attempt's record: fifteen more minutes if ``in_progress``, 24 hours once
        terminal. A key that now holds a different attempt's record is left alone."""
        ...

    async def release(self, scope: IdempotencyScope, record: IdempotencyRecord) -> None:
        """Delete the key if it is still this attempt's ``in_progress`` claim (best effort)."""
        ...


class RedisIdempotencyStore:
    """One JSON string per key, so the claim, the value and the expiry are a single command."""

    def __init__(self, redis: Redis) -> None:
        self._redis = redis

    async def claim(
        self, scope: IdempotencyScope, record: IdempotencyRecord
    ) -> IdempotencyRecord | None:
        name = scope.redis_key
        for _ in range(_CLAIM_ATTEMPTS):
            try:
                claimed = await self._redis.set(
                    name, record.to_json(), nx=True, ex=IN_PROGRESS_TTL_SECONDS
                )
                if claimed:
                    return None
                raw = await self._redis.get(name)
            except (RedisError, OSError):
                raise IdempotencyUnavailable from None
            if raw is None:  # it expired between the two commands; try to claim it again
                continue
            try:
                return IdempotencyRecord.from_json(raw)
            except ValueError:
                raise IdempotencyUnavailable from None
        raise IdempotencyUnavailable

    async def save(self, scope: IdempotencyScope, record: IdempotencyRecord) -> None:
        ttl = IN_PROGRESS_TTL_SECONDS if record.state == "in_progress" else TERMINAL_TTL_SECONDS
        name = scope.redis_key
        try:
            raw = await self._redis.get(name)
            if raw is not None:
                try:
                    current = IdempotencyRecord.from_json(raw)
                except ValueError:
                    raise IdempotencyUnavailable from None
                if current.assistant_message_id != record.assistant_message_id:
                    # Our claim expired and another request took the key: its record stands. (Read
                    # then write is not atomic, but the window is two commands wide and only opens
                    # after a claim already outlived fifteen minutes.)
                    logger.warning("chat_idempotency_save_skipped")
                    return
            await self._redis.set(name, record.to_json(), ex=ttl)
        except (RedisError, OSError):
            raise IdempotencyUnavailable from None

    async def release(self, scope: IdempotencyScope, record: IdempotencyRecord) -> None:
        name = scope.redis_key
        try:
            raw = await self._redis.get(name)
            if raw is None:
                return
            current = IdempotencyRecord.from_json(raw)
            if (
                current.state == "in_progress"
                and current.assistant_message_id == record.assistant_message_id
            ):
                await self._redis.delete(name)
        except (RedisError, OSError, ValueError):
            # The claim then simply expires. Nothing else can be done for a failed cleanup.
            logger.warning("chat_idempotency_release_failed")
