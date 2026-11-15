"""Offline tests for the PostgreSQL checkpointer's immutable owner binding."""

from __future__ import annotations

import asyncio
import sys
from uuid import uuid4

import pytest
from pydantic import SecretStr

import app.agents.checkpointer as checkpointer_module
from app.core.config import Settings


def make_settings() -> Settings:
    return Settings(
        database_url=SecretStr("postgresql+asyncpg://maya_app:placeholder@localhost:5432/maya"),
        redis_url=SecretStr("redis://localhost:6379/0"),
        jwt_signing_key=SecretStr("0" * 32),
        otp_hmac_key=SecretStr("1" * 32),
    )


def test_event_loop_policy_is_installed_before_a_loop_starts(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class OtherPolicy:
        pass

    class SelectorPolicy:
        pass

    current_policy: list[object] = [OtherPolicy()]
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(asyncio, "WindowsSelectorEventLoopPolicy", SelectorPolicy)
    monkeypatch.setattr(asyncio, "get_event_loop_policy", lambda: current_policy[0])
    monkeypatch.setattr(
        asyncio,
        "set_event_loop_policy",
        lambda policy: current_policy.__setitem__(0, policy),
    )

    checkpointer_module.ensure_compatible_event_loop()

    assert isinstance(current_policy[0], SelectorPolicy)


def test_compatible_selector_loop_passes_runtime_validation(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SelectorLoop:
        pass

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(asyncio, "SelectorEventLoop", SelectorLoop)
    monkeypatch.setattr(asyncio, "get_running_loop", SelectorLoop)

    checkpointer_module._validate_compatible_running_loop()


@pytest.mark.asyncio
async def test_event_loop_policy_setup_rejects_a_running_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class SelectorPolicy:
        pass

    policy_changes: list[object] = []
    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(asyncio, "WindowsSelectorEventLoopPolicy", SelectorPolicy)
    monkeypatch.setattr(asyncio, "get_event_loop_policy", lambda: object())
    monkeypatch.setattr(asyncio, "set_event_loop_policy", policy_changes.append)

    with pytest.raises(RuntimeError, match="before the event loop starts"):
        checkpointer_module.ensure_compatible_event_loop()

    assert policy_changes == []


@pytest.mark.asyncio
async def test_checkpointer_rejects_an_incompatible_running_windows_loop(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    class NotSelectorLoop:
        pass

    class NoConnect:
        @classmethod
        async def connect(cls, *_args: object, **_kwargs: object) -> None:
            pytest.fail("database connection must not open with an incompatible event loop")

    monkeypatch.setattr(sys, "platform", "win32")
    monkeypatch.setattr(asyncio, "SelectorEventLoop", NotSelectorLoop)
    monkeypatch.setattr(checkpointer_module, "AsyncConnection", NoConnect)

    with pytest.raises(RuntimeError, match="requires a Windows SelectorEventLoop"):
        async with checkpointer_module.create_postgres_checkpointer(
            make_settings(), workspace_id=uuid4(), user_id=uuid4()
        ):
            pytest.fail("incompatible event loop must fail before opening the connection")


@pytest.mark.asyncio
async def test_postgres_checkpointer_sets_server_owner_and_never_runs_setup(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    workspace_id, user_id = uuid4(), uuid4()

    class FakeConnection:
        def __init__(self) -> None:
            self.executed: list[tuple[str, tuple[str, str]]] = []
            self.closed = False

        @classmethod
        async def connect(cls, *_args: object, **_kwargs: object) -> FakeConnection:
            return cls()

        async def execute(self, query: str, parameters: tuple[str, str]) -> None:
            self.executed.append((query, parameters))

        async def __aenter__(self) -> FakeConnection:
            return self

        async def __aexit__(self, *_args: object) -> None:
            self.closed = True

    class FakeSaver:
        def __init__(self, *, conn: FakeConnection) -> None:
            self.connection = conn
            self.setup_called = False

        async def setup(self) -> None:
            self.setup_called = True

    monkeypatch.setattr(checkpointer_module, "AsyncConnection", FakeConnection)
    monkeypatch.setattr(checkpointer_module, "AsyncPostgresSaver", FakeSaver)
    monkeypatch.setattr(sys, "platform", "linux")

    async with checkpointer_module.create_postgres_checkpointer(
        make_settings(), workspace_id=workspace_id, user_id=user_id
    ) as saver:
        fake_saver = saver
        assert isinstance(fake_saver, FakeSaver)
        assert len(fake_saver.connection.executed) == 1
        query, parameters = fake_saver.connection.executed[0]
        assert "set_config('app.workspace_id', %s, false)" in query
        assert "set_config('app.user_id', %s, false)" in query
        assert parameters == (str(workspace_id), str(user_id))
        assert fake_saver.setup_called is False
        connection = fake_saver.connection

    assert connection.closed is True


@pytest.mark.asyncio
async def test_postgres_checkpointer_rejects_non_uuid_owner_before_connecting(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    def unexpected_connect(*_args: object, **_kwargs: object) -> None:
        pytest.fail("database connection must not be opened for invalid owner identities")

    monkeypatch.setattr(
        checkpointer_module,
        "AsyncConnection",
        type("NoConnect", (), {"connect": unexpected_connect}),
    )

    with pytest.raises(TypeError, match="authenticated UUID identities"):
        async with checkpointer_module.create_postgres_checkpointer(
            make_settings(),
            workspace_id="client-workspace",  # type: ignore[arg-type]
            user_id=uuid4(),
        ):
            pytest.fail("invalid owner identity must fail before yielding")


def test_checkpoint_migration_defaults_owner_columns_and_forces_rls(
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    from importlib import import_module

    migration = import_module("app.db.migrations.versions.0006_checkpoint_owner_isolation")
    statements: list[str] = []
    monkeypatch.setattr(migration.op, "execute", statements.append)

    migration.upgrade()

    sql = "\n".join(statements)
    for table in ("checkpoints", "checkpoint_blobs", "checkpoint_writes"):
        assert f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS workspace_id UUID" in sql
        assert f"ALTER TABLE {table} ADD COLUMN IF NOT EXISTS user_id UUID" in sql
        assert f"ALTER TABLE {table} ENABLE ROW LEVEL SECURITY" in sql
        assert f"ALTER TABLE {table} FORCE ROW LEVEL SECURITY" in sql
        assert f"CREATE POLICY ws_isolation ON {table}" in sql
    assert "current_setting('app.workspace_id', true)" in sql
    assert "current_setting('app.user_id', true)" in sql
    assert "FOREIGN KEY (workspace_id, user_id)" in sql
    assert "REFERENCES users (workspace_id, id)" in sql
    assert "UPDATE checkpoints" not in sql
    assert (
        "GRANT SELECT, INSERT, UPDATE, DELETE ON checkpoints, checkpoint_blobs, checkpoint_writes"
        in sql
    )

    statements.clear()
    migration.downgrade()
    downgrade_sql = "\n".join(statements)
    assert "ALTER TABLE checkpoints NO FORCE ROW LEVEL SECURITY" in downgrade_sql
    assert "cannot be downgraded after checkpoint data exists" in downgrade_sql
