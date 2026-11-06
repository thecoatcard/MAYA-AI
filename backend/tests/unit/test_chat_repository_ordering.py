"""Offline tests for the tenant-scoped session lock used to order chat turns."""

from typing import Any, cast
from uuid import UUID

import pytest
from sqlalchemy.ext.asyncio import AsyncConnection

from app.db.repositories.chat import ChatRepository, SessionNotFound

WORKSPACE_ID = UUID("b5714b26-39e2-490f-a1e2-10e43394d4c2")
USER_ID = UUID("4b0d2384-3c22-4fb0-969e-578b08ac67da")
SESSION_ID = UUID("f0c967d2-8b10-4ddd-b94e-0b25af4dd730")


class Result:
    def __init__(self, row: object | None) -> None:
        self._row = row

    def first(self) -> object | None:
        return self._row


class RecordingConnection:
    def __init__(self, row: object | None = object()) -> None:
        self.row = row
        self.statement: Any = None
        self.parameters: dict[str, Any] | None = None

    async def execute(self, statement: Any, parameters: dict[str, Any]) -> Result:
        self.statement = statement
        self.parameters = parameters
        return Result(self.row)


async def test_lock_session_uses_a_tenant_scoped_row_lock() -> None:
    connection = RecordingConnection()
    repository = ChatRepository(cast(AsyncConnection, connection), WORKSPACE_ID, USER_ID)

    await repository.lock_session(SESSION_ID)

    assert "FOR UPDATE" in str(connection.statement).upper()
    assert connection.parameters == {
        "session_id": SESSION_ID,
        "workspace_id": WORKSPACE_ID,
        "user_id": USER_ID,
    }


async def test_lock_session_does_not_lock_or_disclose_a_missing_session() -> None:
    connection = RecordingConnection(row=None)
    repository = ChatRepository(cast(AsyncConnection, connection), WORKSPACE_ID, USER_ID)

    with pytest.raises(SessionNotFound):
        await repository.lock_session(SESSION_ID)
