"""Offline tests for PostgreSQL auth query ordering."""

from datetime import UTC, datetime
from types import SimpleNamespace
from uuid import uuid4

import pytest

from app.auth.postgres import PostgresAuthTransaction


class Result:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def first(self) -> object | None:
        return self._row


class ResetWonConnection:
    """Model a reset winning the lock before the transaction rechecks the password version."""

    def __init__(self) -> None:
        self.calls: list[str] = []

    async def execute(self, statement: object, parameters: object = None) -> Result:
        sql = statement.text  # type: ignore[attr-defined]
        self.calls.append(sql)
        if "FOR UPDATE" in sql.upper():
            return Result(SimpleNamespace(id=uuid4()))
        if sql.lstrip().upper().startswith("SELECT") and "PASSWORD_CHANGED_AT" in sql.upper():
            return Result(None)
        return Result(None)


@pytest.mark.asyncio
async def test_session_creation_locks_then_rechecks_before_inserting() -> None:
    connection = ResetWonConnection()
    transaction = PostgresAuthTransaction(connection)  # type: ignore[arg-type]

    opened = await transaction.create_session(
        session_id=uuid4(),
        workspace_id=uuid4(),
        user_id=uuid4(),
        refresh_jti=uuid4(),
        refresh_expires_at=datetime.now(UTC),
        absolute_expires_at=datetime.now(UTC),
        expected_password_changed_at=datetime.now(UTC),
        now=datetime.now(UTC),
    )

    assert not opened
    assert len(connection.calls) == 2
    assert "FOR UPDATE" in connection.calls[0].upper()
    assert "PASSWORD_CHANGED_AT" in connection.calls[1].upper()
