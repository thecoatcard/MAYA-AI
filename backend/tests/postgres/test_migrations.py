from typing import Any

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import TestDatabase, alembic_config

pytestmark = pytest.mark.postgres

PLAN_TABLES = {
    "workspaces",
    "users",
    "sessions",
    "messages",
    "tasks",
    "subtasks",
    "approval_requests",
    "compensations",
    "audit_log",
    "mcp_servers",
    "file_artifacts",
    "memory_items",
}
AUTH_TABLES = {"auth_otp_challenges", "auth_sessions"}
ALL_TABLES = PLAN_TABLES | AUTH_TABLES

SNAPSHOT_QUERIES = {
    "tables": "SELECT tablename FROM pg_tables WHERE schemaname = 'public' ORDER BY 1",
    "columns": (
        "SELECT table_name, column_name, data_type, is_nullable, column_default "
        "FROM information_schema.columns WHERE table_schema = 'public' ORDER BY 1, 2"
    ),
    "constraints": (
        "SELECT conrelid::regclass::text, conname, pg_get_constraintdef(oid) FROM pg_constraint "
        "WHERE connamespace = 'public'::regnamespace ORDER BY 1, 2"
    ),
    "indexes": (
        "SELECT tablename, indexdef FROM pg_indexes WHERE schemaname = 'public' ORDER BY 1, 2"
    ),
    "policies": (
        "SELECT tablename, policyname, roles::text, cmd, qual, with_check FROM pg_policies "
        "WHERE schemaname = 'public' ORDER BY 1, 2"
    ),
    "rls_flags": (
        "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
        "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r' ORDER BY 1"
    ),
    "table_grants": (
        "SELECT grantee, table_name, privilege_type FROM information_schema.role_table_grants "
        "WHERE table_schema = 'public' "
        "AND grantee IN ('maya_app', 'maya_auth_definer') ORDER BY 1, 2, 3"
    ),
    "column_grants": (
        "SELECT grantee, table_name, column_name, privilege_type "
        "FROM information_schema.column_privileges WHERE table_schema = 'public' "
        "AND grantee IN ('maya_app', 'maya_auth_definer') ORDER BY 1, 2, 3, 4"
    ),
    "functions": (
        "SELECT proname, prosecdef, proconfig::text, pg_get_userbyid(proowner), proacl::text "
        "FROM pg_proc WHERE pronamespace = 'public'::regnamespace "
        "AND proname LIKE 'auth%' ORDER BY 1"
    ),
}


async def snapshot(engine: AsyncEngine) -> dict[str, list[tuple[Any, ...]]]:
    """Schema snapshot; Alembic's own bookkeeping table is not part of the application schema."""
    result: dict[str, list[tuple[Any, ...]]] = {}
    async with engine.connect() as connection:
        for name, query in SNAPSHOT_QUERIES.items():
            rows = (await connection.execute(text(query))).all()
            result[name] = [tuple(row) for row in rows if "alembic_version" not in repr(tuple(row))]
    return result


async def test_head_creates_the_plan_tables_and_the_authentication_tables(
    admin_engine: AsyncEngine,
) -> None:
    async with admin_engine.connect() as connection:
        rows = await connection.execute(
            text("SELECT tablename FROM pg_tables WHERE schemaname = 'public'")
        )
        tables = {row[0] for row in rows}
    assert tables == ALL_TABLES | {"alembic_version"}
    assert len(PLAN_TABLES) == 12  # the plan's twelve tables, 11 of them tenant-scoped


async def test_every_table_has_forced_row_level_security_and_a_workspace_policy(
    admin_engine: AsyncEngine,
) -> None:
    async with admin_engine.connect() as connection:
        flags = {
            row[0]: (row[1], row[2])
            for row in await connection.execute(
                text(
                    "SELECT relname, relrowsecurity, relforcerowsecurity FROM pg_class "
                    "WHERE relnamespace = 'public'::regnamespace AND relkind = 'r'"
                )
            )
        }
        policies = {
            row[0]
            for row in await connection.execute(
                text("SELECT tablename FROM pg_policies WHERE policyname = 'ws_isolation'")
            )
        }
    protected = ALL_TABLES
    assert {table for table, flag in flags.items() if flag == (True, True)} == protected
    assert flags["alembic_version"] == (False, False)
    assert policies == protected


async def test_extensions_and_plan_indexes_exist(admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as connection:
        extensions = {
            row[0] for row in await connection.execute(text("SELECT extname FROM pg_extension"))
        }
        indexes = {
            row[0] for row in await connection.execute(text("SELECT indexname FROM pg_indexes"))
        }
    assert {"vector", "pg_trgm"} <= extensions
    assert {
        "memory_embedding_hnsw",
        "memory_tsv_gin",
        "memory_content_trgm",
        "memory_scope_idx",
        "auth_otp_one_live_challenge",
    } <= indexes


async def test_tasks_keep_the_per_workspace_idempotency_key(admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as connection:
        definitions = [
            row[0]
            for row in await connection.execute(
                text(
                    "SELECT pg_get_constraintdef(oid) FROM pg_constraint "
                    "WHERE conrelid = 'tasks'::regclass AND contype = 'u'"
                )
            )
        ]
    assert sorted(definitions) == [
        "UNIQUE (workspace_id, id)",  # target of the composite foreign keys onto tasks
        "UNIQUE (workspace_id, idempotency_key)",
    ]


@pytest.mark.parametrize(
    "values",
    [
        {"username": "UPPER", "email": "a@example.com", "role": "user"},
        {"username": "ab", "email": "a@example.com", "role": "user"},
        {"username": "has space", "email": "a@example.com", "role": "user"},
        {"username": "valid_name", "email": "Mixed@Example.com", "role": "user"},
        {"username": "valid_name", "email": "a@example.com", "role": "root"},
    ],
)
async def test_users_constraints_reject_bad_rows(
    admin_engine: AsyncEngine, values: dict[str, str]
) -> None:
    async with admin_engine.connect() as connection:
        await connection.execute(
            text(
                "INSERT INTO workspaces (id, name, slug) "
                "VALUES ('00000000-0000-0000-0000-0000000000a1', 'w', 'constraint-probe') "
                "ON CONFLICT DO NOTHING"
            )
        )
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "INSERT INTO users "
                    "(workspace_id, username, email, full_name, hashed_password, role) "
                    "VALUES ('00000000-0000-0000-0000-0000000000a1', "
                    ":username, :email, 'n', 'h', :role)"
                ),
                values,
            )
        await connection.rollback()


async def test_users_require_a_password_hash(admin_engine: AsyncEngine) -> None:
    async with admin_engine.connect() as connection:
        await connection.execute(
            text(
                "INSERT INTO workspaces (id, name, slug) "
                "VALUES ('00000000-0000-0000-0000-0000000000a2', 'w', 'null-password-probe') "
                "ON CONFLICT DO NOTHING"
            )
        )
        with pytest.raises(DBAPIError):
            await connection.execute(
                text(
                    "INSERT INTO users (workspace_id, username, email, full_name, hashed_password) "
                    "VALUES ('00000000-0000-0000-0000-0000000000a2', "
                    "'nopassword', 'n@example.com', 'n', NULL)"
                )
            )
        await connection.rollback()


async def test_one_live_challenge_per_user_and_purpose(admin_engine: AsyncEngine) -> None:
    async with admin_engine.begin() as connection:
        await connection.execute(
            text(
                "INSERT INTO workspaces (id, name, slug) "
                "VALUES ('00000000-0000-0000-0000-0000000000a3', 'w', 'challenge-probe')"
            )
        )
        await connection.execute(
            text(
                "INSERT INTO users (id, workspace_id, username, email, full_name, hashed_password) "
                "VALUES ('00000000-0000-0000-0000-0000000000b3', "
                "'00000000-0000-0000-0000-0000000000a3', 'challenger', 'c@example.com', 'c', 'h')"
            )
        )
    insert = text(
        "INSERT INTO auth_otp_challenges (workspace_id, user_id, purpose, code_hmac, expires_at) "
        "VALUES ('00000000-0000-0000-0000-0000000000a3', '00000000-0000-0000-0000-0000000000b3', "
        ":purpose, '\\x00', now() + interval '10 minutes')"
    )
    async with admin_engine.begin() as connection:
        await connection.execute(insert, {"purpose": "email_verification"})
        await connection.execute(insert, {"purpose": "password_reset"})
    async with admin_engine.connect() as connection:
        with pytest.raises(DBAPIError):
            await connection.execute(insert, {"purpose": "email_verification"})
        await connection.rollback()
        await connection.execute(
            text(
                "UPDATE auth_otp_challenges SET invalidated_at = now() "
                "WHERE purpose = 'email_verification' "
                "AND user_id = '00000000-0000-0000-0000-0000000000b3'"
            )
        )
        await connection.execute(insert, {"purpose": "email_verification"})
        await connection.commit()


async def test_down_and_up_again_restores_an_identical_schema(fresh_database: TestDatabase) -> None:
    config = alembic_config(fresh_database)
    engine = create_async_engine(fresh_database.admin_url, poolclass=NullPool)
    try:
        await run_in_thread(command.upgrade, config, "head")
        first = await snapshot(engine)
        assert {row[0] for row in first["tables"]} == ALL_TABLES

        await run_in_thread(command.downgrade, config, "base")
        emptied = await snapshot(engine)
        assert emptied["tables"] == []
        assert emptied["policies"] == []
        assert emptied["functions"] == []
        async with engine.connect() as connection:
            roles = {
                row[0]
                for row in await connection.execute(
                    text("SELECT rolname FROM pg_roles WHERE rolname = 'maya_auth_definer'")
                )
            }
        assert roles == {"maya_auth_definer"}  # cluster-level role is intentionally kept

        await run_in_thread(command.upgrade, config, "head")
        assert await snapshot(engine) == first
    finally:
        await engine.dispose()


async def test_each_revision_can_be_applied_and_reverted_one_step_at_a_time(
    fresh_database: TestDatabase,
) -> None:
    config = alembic_config(fresh_database)
    engine = create_async_engine(fresh_database.admin_url, poolclass=NullPool)
    try:
        previous = await snapshot(engine)
        for revision in ("0001", "0002", "0003", "0004"):
            await run_in_thread(command.upgrade, config, revision)
            current = await snapshot(engine)
            assert current != previous
            await run_in_thread(command.downgrade, config, "-1")
            assert await snapshot(engine) == previous
            await run_in_thread(command.upgrade, config, revision)
            previous = await snapshot(engine)
    finally:
        await engine.dispose()


async def run_in_thread(function: Any, *arguments: Any) -> None:
    import asyncio

    await asyncio.to_thread(function, *arguments)
