"""Persistence of chat sessions and messages.

``ChatRepository`` wraps one connection that is already inside ``tenant_transaction(engine,
workspace_id)``; the caller owns the transaction (commit, rollback, and what to shield from
cancellation). Row-level security confines every statement to the context workspace. On top of
that, every statement here also names the workspace and, for sessions and for the messages of a
session, the owning user, so a repository built for the wrong tenant or user sees nothing and
writes nothing instead of relying on one layer alone.

All SQL is a literal ``text()`` with bind parameters; no value is ever formatted into a
statement. Other users' sessions are indistinguishable from sessions that do not exist.
"""

import base64
import json
import re
from collections.abc import Callable, Mapping
from dataclasses import dataclass
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, Final, Literal
from uuid import UUID, uuid4

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

Channel = Literal["web", "voice", "desktop"]
Sender = Literal["user", "orchestrator", "agent", "system"]
Modality = Literal["text", "audio", "multimodal"]

MAX_TITLE_CHARS: Final = 255
MAX_LIST_LIMIT: Final = 100
MAX_RECENT_LIMIT: Final = 500

_SESSIONS: Final = "sessions"
_MESSAGES: Final = "messages"
_MAX_CURSOR_CHARS: Final = 512
# NUL cannot be stored in text or JSONB and a lone surrogate cannot be encoded as UTF-8; either
# would fail the whole transaction (and with it a reply that was already generated and billed).
_UNSTORABLE = re.compile(r"[\x00\ud800-\udfff]")

INSERT_SESSION = text(
    "INSERT INTO sessions (workspace_id, user_id, title, channel, created_at) "
    "VALUES (:workspace_id, :user_id, :title, :channel, clock_timestamp()) "
    "RETURNING id, workspace_id, user_id, title, channel, status, created_at"
)
GET_SESSION = text(
    "SELECT id, workspace_id, user_id, title, channel, status, created_at FROM sessions "
    "WHERE id = :session_id AND workspace_id = :workspace_id AND user_id = :user_id"
)
LOCK_SESSION = text(
    "SELECT id FROM sessions "
    "WHERE id = :session_id AND workspace_id = :workspace_id AND user_id = :user_id "
    "FOR UPDATE"
)
DELETE_SESSION = text(
    "DELETE FROM sessions "
    "WHERE id = :session_id AND workspace_id = :workspace_id AND user_id = :user_id "
    "RETURNING id"
)
LIST_SESSIONS_FIRST = text(
    "SELECT id, workspace_id, user_id, title, channel, status, created_at FROM sessions "
    "WHERE workspace_id = :workspace_id AND user_id = :user_id "
    "ORDER BY created_at DESC, id DESC LIMIT :fetch"
)
LIST_SESSIONS_AFTER = text(
    "SELECT id, workspace_id, user_id, title, channel, status, created_at FROM sessions "
    "WHERE workspace_id = :workspace_id AND user_id = :user_id "
    "AND (created_at, id) < (CAST(:after_created_at AS timestamptz), CAST(:after_id AS uuid)) "
    "ORDER BY created_at DESC, id DESC LIMIT :fetch"
)
# The session is looked up in the same statement, so a message can only be written into a session
# of this workspace that belongs to this user; zero rows means there was no such session.
INSERT_MESSAGE = text(
    "INSERT INTO messages (id, workspace_id, session_id, sender, modality, content, metadata, "
    "created_at) "
    "SELECT CAST(:message_id AS uuid), s.workspace_id, s.id, CAST(:sender AS varchar), "
    "CAST(:modality AS varchar), CAST(:content AS text), CAST(:metadata AS jsonb), "
    "clock_timestamp() "
    "FROM sessions AS s "
    "WHERE s.id = :session_id AND s.workspace_id = :workspace_id AND s.user_id = :user_id "
    "RETURNING id, workspace_id, session_id, sender, modality, content, metadata, created_at"
)
GET_MESSAGE = text(
    "SELECT m.id, m.workspace_id, m.session_id, m.sender, m.modality, m.content, m.metadata, "
    "m.created_at FROM messages AS m "
    "JOIN sessions AS s ON s.workspace_id = m.workspace_id AND s.id = m.session_id "
    "WHERE m.id = :message_id AND m.workspace_id = :workspace_id AND s.user_id = :user_id"
)
# The conversation is the user's and the assistant's turns; agent and system rows are not part of
# it (Phase 05 and later may write them), so the list queries leave them out.
LIST_MESSAGES_FIRST = text(
    "SELECT m.id, m.workspace_id, m.session_id, m.sender, m.modality, m.content, m.metadata, "
    "m.created_at FROM messages AS m "
    "JOIN sessions AS s ON s.workspace_id = m.workspace_id AND s.id = m.session_id "
    "WHERE m.workspace_id = :workspace_id AND m.session_id = :session_id "
    "AND s.user_id = :user_id AND m.sender IN ('user', 'orchestrator') "
    "ORDER BY m.created_at ASC, m.id ASC LIMIT :fetch"
)
LIST_MESSAGES_AFTER = text(
    "SELECT m.id, m.workspace_id, m.session_id, m.sender, m.modality, m.content, m.metadata, "
    "m.created_at FROM messages AS m "
    "JOIN sessions AS s ON s.workspace_id = m.workspace_id AND s.id = m.session_id "
    "WHERE m.workspace_id = :workspace_id AND m.session_id = :session_id "
    "AND s.user_id = :user_id AND m.sender IN ('user', 'orchestrator') "
    "AND (m.created_at, m.id) > (CAST(:after_created_at AS timestamptz), CAST(:after_id AS uuid)) "
    "ORDER BY m.created_at ASC, m.id ASC LIMIT :fetch"
)
RECENT_MESSAGES = text(
    "SELECT m.id, m.workspace_id, m.session_id, m.sender, m.modality, m.content, m.metadata, "
    "m.created_at FROM messages AS m "
    "JOIN sessions AS s ON s.workspace_id = m.workspace_id AND s.id = m.session_id "
    "WHERE m.workspace_id = :workspace_id AND m.session_id = :session_id "
    "AND s.user_id = :user_id AND m.sender IN ('user', 'orchestrator') "
    "ORDER BY m.created_at DESC, m.id DESC LIMIT :fetch"
)
SET_CHAT_USER_CONTEXT = text(
    "SELECT set_config('app.user_id', :user_id, true) WHERE CAST(:workspace_id AS text) IS NOT NULL"
)
GET_IDEMPOTENCY = text(
    "SELECT state, request_hash, session_id, user_message_id, assistant_message_id, "
    "finish_reason, error_code FROM chat_idempotency "
    "WHERE workspace_id = :workspace_id AND user_id = :user_id AND key_digest = :key_digest"
)
INSERT_IDEMPOTENCY = text(
    "INSERT INTO chat_idempotency "
    "(workspace_id, user_id, key_digest, request_hash, state, session_id, user_message_id, "
    "assistant_message_id, finish_reason, error_code, created_at, updated_at) "
    "VALUES (:workspace_id, :user_id, :key_digest, :request_hash, :state, :session_id, "
    ":user_message_id, :assistant_message_id, :finish_reason, :error_code, "
    "clock_timestamp(), clock_timestamp()) "
    "ON CONFLICT (workspace_id, user_id, key_digest) DO NOTHING RETURNING key_digest"
)
SAVE_IDEMPOTENCY = text(
    "UPDATE chat_idempotency SET state = :state, session_id = CAST(:session_id AS UUID), "
    "finish_reason = :finish_reason, error_code = :error_code, updated_at = clock_timestamp() "
    "WHERE workspace_id = :workspace_id AND user_id = :user_id AND key_digest = :key_digest "
    "AND request_hash = :request_hash AND user_message_id = :user_message_id "
    "AND assistant_message_id = :assistant_message_id AND state = 'in_progress' "
    "AND (session_id IS NULL OR CAST(:session_id AS UUID) IS NULL "
    "OR session_id = CAST(:session_id AS UUID)) "
    "RETURNING key_digest"
)


class SessionNotFound(Exception):
    """No such session for this user: it does not exist, or it belongs to another user or
    workspace. The two cases are deliberately not distinguishable."""

    def __init__(self) -> None:
        super().__init__("Session not found")


class InvalidCursor(ValueError):
    """The pagination cursor is malformed, was altered, or belongs to a different list."""

    def __init__(self) -> None:
        super().__init__("Invalid cursor")


@dataclass(frozen=True, slots=True)
class ChatSessionRecord:
    id: UUID
    workspace_id: UUID
    user_id: UUID
    title: str | None
    channel: str
    status: str
    created_at: datetime


@dataclass(frozen=True, slots=True)
class ChatMessageRecord:
    """A stored message. ``metadata`` is the JSON object stored with it.

    ``role``, ``finish_reason`` and ``model_id`` are the API view of the record, so
    ``MessageOut.model_validate(record)`` works directly.
    """

    id: UUID
    workspace_id: UUID
    session_id: UUID
    sender: str
    modality: str
    content: str
    metadata: dict[str, Any]
    created_at: datetime

    @property
    def role(self) -> Literal["user", "assistant"]:
        """``user`` for sender ``user``, ``assistant`` for ``orchestrator``.

        Raises ``ValueError`` for ``agent`` and ``system`` messages, which have no chat role.
        """
        if self.sender == "user":
            return "user"
        if self.sender == "orchestrator":
            return "assistant"
        raise ValueError("This message has no chat role")

    @property
    def finish_reason(self) -> str | None:
        value = self.metadata.get("finish_reason")
        return value if isinstance(value, str) else None

    @property
    def model_id(self) -> str | None:
        value = self.metadata.get("model_id")
        return value if isinstance(value, str) else None


@dataclass(frozen=True, slots=True)
class ChatIdempotencyRecord:
    """Durable, content-free idempotency state for one scoped key digest."""

    state: str
    request_hash: str
    session_id: UUID | None
    user_message_id: UUID
    assistant_message_id: UUID
    finish_reason: str | None
    error_code: str | None


@dataclass(frozen=True, slots=True)
class RecordPage[T]:
    """One page of records; ``next_cursor`` is ``None`` on the last page."""

    items: tuple[T, ...]
    next_cursor: str | None


def _storable(value: str) -> str:
    return _UNSTORABLE.sub("\N{REPLACEMENT CHARACTER}", value)


def _json_default(value: object) -> str:
    if isinstance(value, Decimal):
        return format(value, "f")
    if isinstance(value, UUID):
        return str(value)
    if isinstance(value, datetime):
        return value.isoformat()
    raise TypeError("Metadata must be JSON-serializable")


def _encode_cursor(kind: str, scope: UUID | None, created_at: datetime, row_id: UUID) -> str:
    payload = json.dumps(
        {
            "k": kind,
            "s": None if scope is None else str(scope),
            "t": created_at.astimezone(UTC).isoformat(),
            "i": str(row_id),
        },
        separators=(",", ":"),
    )
    return base64.urlsafe_b64encode(payload.encode("ascii")).decode("ascii").rstrip("=")


def _decode_cursor(cursor: str, kind: str, scope: UUID | None) -> tuple[datetime, UUID]:
    """The position a cursor stands for. Anything that is not exactly what ``_encode_cursor``
    produces for this list (``kind``, and ``scope`` for the messages of one session) is
    rejected, including other spellings of a valid position."""
    try:
        if not cursor or len(cursor) > _MAX_CURSOR_CHARS or not cursor.isascii():
            raise InvalidCursor
        raw = base64.urlsafe_b64decode(cursor + "=" * (-len(cursor) % 4))
        body = json.loads(raw.decode("ascii"))
        if not isinstance(body, dict) or set(body) != {"k", "s", "t", "i"}:
            raise InvalidCursor
        if body["k"] != kind or body["s"] != (None if scope is None else str(scope)):
            raise InvalidCursor
        if not isinstance(body["t"], str) or not isinstance(body["i"], str):
            raise InvalidCursor
        created_at = datetime.fromisoformat(body["t"])
        row_id = UUID(body["i"])
        if created_at.tzinfo is None:
            raise InvalidCursor
        created_at = created_at.astimezone(UTC)
    except InvalidCursor:
        raise
    except (ValueError, TypeError, OverflowError):
        raise InvalidCursor from None
    if _encode_cursor(kind, scope, created_at, row_id) != cursor:
        raise InvalidCursor
    return created_at, row_id


def _check_limit(limit: int, maximum: int) -> None:
    if isinstance(limit, bool) or not 1 <= limit <= maximum:
        raise ValueError(f"limit must be between 1 and {maximum}")


def _session(row: Any) -> ChatSessionRecord:
    return ChatSessionRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        user_id=row.user_id,
        title=row.title,
        channel=row.channel,
        status=row.status,
        created_at=row.created_at,
    )


def _message(row: Any) -> ChatMessageRecord:
    metadata = row.metadata
    if isinstance(metadata, str | bytes):  # a driver without a JSON codec returns the text
        metadata = json.loads(metadata)
    return ChatMessageRecord(
        id=row.id,
        workspace_id=row.workspace_id,
        session_id=row.session_id,
        sender=row.sender,
        modality=row.modality,
        content=row.content,
        metadata=dict(metadata),
        created_at=row.created_at,
    )


def _idempotency(row: Any) -> ChatIdempotencyRecord:
    return ChatIdempotencyRecord(
        state=row.state,
        request_hash=row.request_hash,
        session_id=row.session_id,
        user_message_id=row.user_message_id,
        assistant_message_id=row.assistant_message_id,
        finish_reason=row.finish_reason,
        error_code=row.error_code,
    )


def _page[T](rows: list[T], limit: int, cursor_of: Callable[[T], str]) -> RecordPage[T]:
    """``rows`` holds up to ``limit + 1`` records; the extra one only proves a next page exists."""
    if len(rows) <= limit:
        return RecordPage(tuple(rows), None)
    items = tuple(rows[:limit])
    return RecordPage(items, cursor_of(items[-1]))


class ChatRepository:
    """Chat sessions and messages of one user, in one workspace, on one open transaction.

    Build it from the connection ``tenant_transaction(engine, workspace_id)`` yields, with that
    same ``workspace_id`` and the authenticated user's id. A ``workspace_id`` that differs from
    the transaction's context makes every read return nothing and every write fail.
    """

    def __init__(self, connection: AsyncConnection, workspace_id: UUID, user_id: UUID) -> None:
        self._connection = connection
        self._workspace_id = workspace_id
        self._user_id = user_id

    async def get_idempotency(self, key_digest: str) -> ChatIdempotencyRecord | None:
        """Return this user's durable record for a keyed digest, if any."""
        await self._connection.execute(
            SET_CHAT_USER_CONTEXT,
            {"user_id": str(self._user_id), "workspace_id": str(self._workspace_id)},
        )
        row = (
            await self._connection.execute(
                GET_IDEMPOTENCY,
                {
                    "workspace_id": self._workspace_id,
                    "user_id": self._user_id,
                    "key_digest": key_digest,
                },
            )
        ).first()
        return None if row is None else _idempotency(row)

    async def claim_idempotency(
        self, key_digest: str, record: ChatIdempotencyRecord
    ) -> ChatIdempotencyRecord | None:
        """Insert one permanent claim, returning the competing record if it already exists."""
        await self._connection.execute(
            SET_CHAT_USER_CONTEXT,
            {"user_id": str(self._user_id), "workspace_id": str(self._workspace_id)},
        )
        parameters = {
            "workspace_id": self._workspace_id,
            "user_id": self._user_id,
            "key_digest": key_digest,
            "request_hash": record.request_hash,
            "state": record.state,
            "session_id": record.session_id,
            "user_message_id": record.user_message_id,
            "assistant_message_id": record.assistant_message_id,
            "finish_reason": record.finish_reason,
            "error_code": record.error_code,
        }
        inserted = (await self._connection.execute(INSERT_IDEMPOTENCY, parameters)).first()
        if inserted is not None:
            return None
        return await self.get_idempotency(key_digest)

    async def save_idempotency(self, key_digest: str, record: ChatIdempotencyRecord) -> bool:
        """CAS the owned in-progress claim; terminal states cannot be overwritten."""
        await self._connection.execute(
            SET_CHAT_USER_CONTEXT,
            {"user_id": str(self._user_id), "workspace_id": str(self._workspace_id)},
        )
        row = (
            await self._connection.execute(
                SAVE_IDEMPOTENCY,
                {
                    "workspace_id": self._workspace_id,
                    "user_id": self._user_id,
                    "key_digest": key_digest,
                    "request_hash": record.request_hash,
                    "state": record.state,
                    "session_id": record.session_id,
                    "user_message_id": record.user_message_id,
                    "assistant_message_id": record.assistant_message_id,
                    "finish_reason": record.finish_reason,
                    "error_code": record.error_code,
                },
            )
        ).first()
        return row is not None

    async def create_session(
        self, title: str | None = None, channel: Channel = "web"
    ) -> ChatSessionRecord:
        """Create a session owned by the user. ``title`` is cut to 255 characters."""
        if title is not None:
            title = _storable(title)[:MAX_TITLE_CHARS]
        row = (
            await self._connection.execute(
                INSERT_SESSION,
                {
                    "workspace_id": self._workspace_id,
                    "user_id": self._user_id,
                    "title": title,
                    "channel": channel,
                },
            )
        ).one()
        return _session(row)

    async def get_session(self, session_id: UUID) -> ChatSessionRecord | None:
        """The session, or ``None`` when it does not exist or is not this user's."""
        row = (
            await self._connection.execute(
                GET_SESSION,
                {
                    "session_id": session_id,
                    "workspace_id": self._workspace_id,
                    "user_id": self._user_id,
                },
            )
        ).first()
        return None if row is None else _session(row)

    async def lock_session(self, session_id: UUID) -> None:
        """Lock this caller's session row until the current transaction ends.

        Chat uses this before reading history and inserting a user message, so concurrent starts
        have a serializable per-session order without keeping a transaction open while the model
        generates a reply.
        """
        row = (
            await self._connection.execute(
                LOCK_SESSION,
                {
                    "session_id": session_id,
                    "workspace_id": self._workspace_id,
                    "user_id": self._user_id,
                },
            )
        ).first()
        if row is None:
            raise SessionNotFound

    async def delete_session(self, session_id: UUID) -> bool:
        """Delete a session of this user.

        Returns True if the session was found and deleted; False otherwise.
        Cascades to messages and tasks via the foreign key constraint.
        """
        result = await self._connection.execute(
            DELETE_SESSION,
            {
                "session_id": session_id,
                "workspace_id": self._workspace_id,
                "user_id": self._user_id,
            },
        )
        return result.first() is not None

    async def list_sessions(
        self, limit: int = 20, cursor: str | None = None
    ) -> RecordPage[ChatSessionRecord]:
        """The user's sessions, newest first. ``limit`` is 1 to 100.

        Raises ``InvalidCursor`` for a cursor that did not come from this list.
        """
        _check_limit(limit, MAX_LIST_LIMIT)
        parameters: dict[str, Any] = {
            "workspace_id": self._workspace_id,
            "user_id": self._user_id,
            "fetch": limit + 1,
        }
        statement = LIST_SESSIONS_FIRST
        if cursor is not None:
            after_created_at, after_id = _decode_cursor(cursor, _SESSIONS, None)
            parameters |= {"after_created_at": after_created_at, "after_id": after_id}
            statement = LIST_SESSIONS_AFTER
        rows = [_session(row) for row in (await self._connection.execute(statement, parameters))]
        return _page(rows, limit, lambda r: _encode_cursor(_SESSIONS, None, r.created_at, r.id))

    async def add_message(
        self,
        session_id: UUID,
        sender: Sender,
        content: str,
        modality: Modality = "text",
        metadata: Mapping[str, Any] | None = None,
        message_id: UUID | None = None,
    ) -> ChatMessageRecord:
        """Append a message to one of the user's sessions and return it.

        ``message_id`` lets the caller use an id it has already announced (the assistant message
        is announced before its reply exists); it must be unique, so generate it server side.
        ``metadata`` must be a JSON object; ``Decimal``, ``UUID`` and ``datetime`` values are
        written as strings. NUL characters and lone surrogates in ``content`` are replaced with
        U+FFFD, because the database cannot store them. ``created_at`` is the wall-clock time of
        the insert, so messages added in one transaction keep their order.

        Raises ``SessionNotFound`` (and writes nothing) when the session does not exist or is
        not this user's.
        """
        if metadata is not None and not isinstance(metadata, Mapping):
            raise TypeError("metadata must be a JSON object")
        encoded = json.dumps(
            {} if metadata is None else dict(metadata),
            separators=(",", ":"),
            default=_json_default,
        )
        row = (
            await self._connection.execute(
                INSERT_MESSAGE,
                {
                    "message_id": uuid4() if message_id is None else message_id,
                    "session_id": session_id,
                    "workspace_id": self._workspace_id,
                    "user_id": self._user_id,
                    "sender": sender,
                    "modality": modality,
                    "content": _storable(content),
                    "metadata": encoded,
                },
            )
        ).first()
        if row is None:
            raise SessionNotFound
        return _message(row)

    async def get_message(self, message_id: UUID) -> ChatMessageRecord | None:
        """The message, or ``None`` when it does not exist or its session is not this user's.
        Unlike the list methods, this also returns ``agent`` and ``system`` messages."""
        row = (
            await self._connection.execute(
                GET_MESSAGE,
                {
                    "message_id": message_id,
                    "workspace_id": self._workspace_id,
                    "user_id": self._user_id,
                },
            )
        ).first()
        return None if row is None else _message(row)

    async def list_messages(
        self, session_id: UUID, limit: int = 50, cursor: str | None = None
    ) -> RecordPage[ChatMessageRecord]:
        """The user and assistant messages of one session, oldest first. ``limit`` is 1 to 100.

        Raises ``InvalidCursor`` for a cursor that did not come from this session's list, and
        ``SessionNotFound`` when the session does not exist or is not this user's.
        """
        _check_limit(limit, MAX_LIST_LIMIT)
        position = None if cursor is None else _decode_cursor(cursor, _MESSAGES, session_id)
        await self._require_session(session_id)
        parameters: dict[str, Any] = {
            "workspace_id": self._workspace_id,
            "user_id": self._user_id,
            "session_id": session_id,
            "fetch": limit + 1,
        }
        statement = LIST_MESSAGES_FIRST
        if position is not None:
            parameters |= {"after_created_at": position[0], "after_id": position[1]}
            statement = LIST_MESSAGES_AFTER
        rows = [_message(row) for row in (await self._connection.execute(statement, parameters))]
        return _page(
            rows, limit, lambda r: _encode_cursor(_MESSAGES, session_id, r.created_at, r.id)
        )

    async def recent_messages(self, session_id: UUID, limit: int) -> list[ChatMessageRecord]:
        """The newest ``limit`` user and assistant messages (1 to 500), oldest first: the order
        a model wants its history in.

        Raises ``SessionNotFound`` when the session does not exist or is not this user's.
        """
        _check_limit(limit, MAX_RECENT_LIMIT)
        await self._require_session(session_id)
        result = await self._connection.execute(
            RECENT_MESSAGES,
            {
                "workspace_id": self._workspace_id,
                "user_id": self._user_id,
                "session_id": session_id,
                "fetch": limit,
            },
        )
        return [_message(row) for row in reversed(result.all())]

    async def _require_session(self, session_id: UUID) -> None:
        if await self.get_session(session_id) is None:
            raise SessionNotFound
