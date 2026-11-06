"""Offline contract tests for the PostgreSQL chat-idempotency repository."""

import asyncio
from dataclasses import replace
from typing import Any, cast
from uuid import UUID, uuid4

from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.repositories.chat import ChatIdempotencyRecord, ChatRepository

WORKSPACE_ID = UUID("bc7e6358-f254-4ea9-8be5-70f0abf18a14")
USER_ID = UUID("7a94129b-3125-4981-9af7-1b73cfaa5205")
OTHER_USER_ID = UUID("d5d80c03-4697-4415-b909-a665f92017b6")
KEY_DIGEST = "0123456789abcdef" * 4
REQUEST_HASH = "fedcba9876543210" * 4


class _Result:
    def __init__(self, row: Any = None) -> None:
        self._row = row

    def first(self) -> Any:
        return self._row


class _Row:
    def __init__(self, **values: Any) -> None:
        self.__dict__.update(values)


class _IdempotencyConnection:
    """Execute only the repository's fixed idempotency statements, atomically in memory."""

    def __init__(self) -> None:
        self.rows: dict[tuple[UUID, UUID, str], ChatIdempotencyRecord] = {}

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> _Result:
        sql = str(statement)
        if sql.startswith("SELECT set_config('app.user_id'"):
            return _Result()
        key = (parameters["workspace_id"], parameters["user_id"], parameters["key_digest"])
        if sql.startswith("INSERT INTO chat_idempotency"):
            if key in self.rows:
                return _Result()
            self.rows[key] = ChatIdempotencyRecord(
                state=parameters["state"],
                request_hash=parameters["request_hash"],
                session_id=parameters["session_id"],
                user_message_id=parameters["user_message_id"],
                assistant_message_id=parameters["assistant_message_id"],
                finish_reason=parameters["finish_reason"],
                error_code=parameters["error_code"],
            )
            return _Result(_Row(key_digest=key[2]))
        if sql.startswith("SELECT state"):
            record = self.rows.get(key)
            return _Result(
                None
                if record is None
                else _Row(
                    state=record.state,
                    request_hash=record.request_hash,
                    session_id=record.session_id,
                    user_message_id=record.user_message_id,
                    assistant_message_id=record.assistant_message_id,
                    finish_reason=record.finish_reason,
                    error_code=record.error_code,
                )
            )
        if sql.startswith("UPDATE chat_idempotency"):
            current = self.rows.get(key)
            if (
                current is None
                or current.state != "in_progress"
                or current.request_hash != parameters["request_hash"]
                or current.user_message_id != parameters["user_message_id"]
                or current.assistant_message_id != parameters["assistant_message_id"]
                or (
                    current.session_id is not None
                    and parameters["session_id"] is not None
                    and current.session_id != parameters["session_id"]
                )
            ):
                return _Result()
            self.rows[key] = replace(
                current,
                state=parameters["state"],
                session_id=parameters["session_id"],
                finish_reason=parameters["finish_reason"],
                error_code=parameters["error_code"],
            )
            return _Result(_Row(key_digest=key[2]))
        raise AssertionError(f"Unexpected repository statement: {sql}")


def _record() -> ChatIdempotencyRecord:
    return ChatIdempotencyRecord(
        state="in_progress",
        request_hash=REQUEST_HASH,
        session_id=None,
        user_message_id=uuid4(),
        assistant_message_id=uuid4(),
        finish_reason=None,
        error_code=None,
    )


def _repository(connection: _IdempotencyConnection, user_id: UUID = USER_ID) -> ChatRepository:
    return ChatRepository(cast(AsyncConnection, connection), WORKSPACE_ID, user_id)


async def test_simultaneous_database_claims_have_exactly_one_winner() -> None:
    connection = _IdempotencyConnection()
    first, second = _record(), _record()

    results = await asyncio.gather(
        _repository(connection).claim_idempotency(KEY_DIGEST, first),
        _repository(connection).claim_idempotency(KEY_DIGEST, second),
    )

    assert results.count(None) == 1
    winner = connection.rows[(WORKSPACE_ID, USER_ID, KEY_DIGEST)]
    assert winner in (first, second)
    assert next(item for item in results if item is not None) == winner


async def test_claim_digest_isolated_by_workspace_and_user() -> None:
    connection = _IdempotencyConnection()
    record = _record()
    assert await _repository(connection).claim_idempotency(KEY_DIGEST, record) is None
    other_user = _repository(connection, OTHER_USER_ID)
    assert await other_user.get_idempotency(KEY_DIGEST) is None
    assert await other_user.claim_idempotency(KEY_DIGEST, _record()) is None
    assert len(connection.rows) == 2
    assert await _repository(connection).get_idempotency(KEY_DIGEST) == record


async def test_compare_and_set_rejects_lost_claim_and_terminal_overwrite() -> None:
    connection = _IdempotencyConnection()
    repository = _repository(connection)
    claim = _record()
    assert await repository.claim_idempotency(KEY_DIGEST, claim) is None

    stale = replace(claim, assistant_message_id=uuid4(), state="completed", finish_reason="stop")
    assert not await repository.save_idempotency(KEY_DIGEST, stale)

    completed = replace(claim, state="completed", finish_reason="stop")
    assert await repository.save_idempotency(KEY_DIGEST, completed)
    assert not await repository.save_idempotency(
        KEY_DIGEST, replace(claim, state="in_progress", session_id=uuid4())
    )
    assert await repository.get_idempotency(KEY_DIGEST) == completed


async def test_durable_terminal_record_survives_cache_loss_or_expiry() -> None:
    connection = _IdempotencyConnection()
    repository = _repository(connection)
    claim = _record()
    assert await repository.claim_idempotency(KEY_DIGEST, claim) is None
    terminal = replace(
        claim,
        state="completed",
        session_id=uuid4(),
        finish_reason="stop",
    )
    assert await repository.save_idempotency(KEY_DIGEST, terminal)

    # Redis is deliberately absent here; repeated lookup still returns the durable replay record.
    assert await repository.get_idempotency(KEY_DIGEST) == terminal
