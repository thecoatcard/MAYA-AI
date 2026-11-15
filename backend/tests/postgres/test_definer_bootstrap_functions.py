"""Revision 0014: cross-tenant SECURITY DEFINER functions that expose no rows."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any
from uuid import UUID

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import TestDatabase, alembic_config
from tests.support.postgres import scoped, seed_tenant, sqlstate

pytestmark = pytest.mark.postgres

ANY_ACCOUNT = text("SELECT public.auth_any_account_exists()")
OPEN_WORK = text("SELECT public.list_workspaces_with_open_work()")
SIGNATURES = ("auth_any_account_exists", "list_workspaces_with_open_work")


@pytest.fixture
async def engines(fresh_database: TestDatabase) -> AsyncIterator[tuple[AsyncEngine, AsyncEngine]]:
    await asyncio.to_thread(command.upgrade, alembic_config(fresh_database), "head")
    admin = create_async_engine(fresh_database.admin_url, poolclass=NullPool)
    app = create_async_engine(fresh_database.app_url, poolclass=NullPool)
    try:
        yield admin, app
    finally:
        await admin.dispose()
        await app.dispose()


async def open_workspaces(app: AsyncEngine) -> set[UUID]:
    async with scoped(app, None) as connection:
        return {row[0] for row in await connection.execute(OPEN_WORK)}


async def test_any_account_exists_is_false_when_empty_then_true_without_tenant_context(
    engines: tuple[AsyncEngine, AsyncEngine],
) -> None:
    admin, app = engines
    async with scoped(app, None) as connection:
        assert (await connection.execute(ANY_ACCOUNT)).scalar() is False
    async with admin.begin() as connection:
        seeded = await seed_tenant(connection, "a")
    async with scoped(app, None) as connection:
        assert (await connection.execute(ANY_ACCOUNT)).scalar() is True
        # The function does not widen direct access: users stay invisible without a tenant.
        assert (await connection.execute(text("SELECT count(*) FROM users"))).scalar() == 0
    async with scoped(app, seeded.workspace_id) as connection:
        assert (await connection.execute(text("SELECT count(*) FROM users"))).scalar() == 2


async def test_open_work_lists_only_workspaces_with_open_work(
    engines: tuple[AsyncEngine, AsyncEngine],
) -> None:
    admin, app = engines
    assert await open_workspaces(app) == set()
    async with admin.begin() as connection:
        tenants = {name: await seed_tenant(connection, name) for name in "abcde"}
        # Everything finished for all five, then open one kind of work per tenant (except "a").
        await connection.execute(
            text("UPDATE tasks SET status = 'completed', plan_status = 'completed'")
        )
        await connection.execute(text("UPDATE subtasks SET status = 'completed'"))
        await connection.execute(
            text("UPDATE subtasks SET status = 'in_progress' WHERE workspace_id = :w"),
            {"w": tenants["b"].workspace_id},
        )
        await connection.execute(
            text(
                "INSERT INTO task_outbox_events (workspace_id, task_id, event_type) "
                "VALUES (:w, :t, 'task.created')"
            ),
            {"w": tenants["c"].workspace_id, "t": tenants["c"].task_id},
        )
        await connection.execute(
            text("UPDATE tasks SET status = 'awaiting_approval' WHERE workspace_id = :w"),
            {"w": tenants["d"].workspace_id},
        )
        await connection.execute(
            text("UPDATE tasks SET plan_status = 'executing' WHERE workspace_id = :w"),
            {"w": tenants["e"].workspace_id},
        )
    expected = {tenants[name].workspace_id for name in "bcde"}
    assert await open_workspaces(app) == expected
    # Dispatched outbox events no longer count.
    async with admin.begin() as connection:
        await connection.execute(text("UPDATE task_outbox_events SET status = 'dispatched'"))
    assert await open_workspaces(app) == expected - {tenants["c"].workspace_id}


async def test_both_functions_are_owned_by_the_definer_and_refuse_a_role_without_execute(
    engines: tuple[AsyncEngine, AsyncEngine], fresh_database: TestDatabase
) -> None:
    admin, _ = engines
    async with admin.connect() as connection:
        rows = (
            await connection.execute(
                text(
                    "SELECT proname, pg_get_userbyid(proowner), prosecdef, proconfig::text, "
                    "has_function_privilege('public', oid, 'EXECUTE'), "
                    "has_function_privilege('maya_app', oid, 'EXECUTE') "
                    "FROM pg_proc WHERE proname = ANY(:names) ORDER BY 1"
                ),
                {"names": list(SIGNATURES)},
            )
        ).all()
        assert [tuple(r) for r in rows] == [
            (
                name,
                "maya_auth_definer",
                True,
                '{"search_path=pg_catalog, pg_temp"}',
                False,
                True,
            )
            for name in SIGNATURES
        ]
        await connection.execute(text("DROP ROLE IF EXISTS maya_no_execute_probe"))
        await connection.execute(text("CREATE ROLE maya_no_execute_probe LOGIN PASSWORD 'p'"))
        await connection.commit()
    try:
        async with admin.connect() as connection:
            await connection.execute(text("SET ROLE maya_no_execute_probe"))
            for statement in (ANY_ACCOUNT, OPEN_WORK):
                with pytest.raises(DBAPIError) as denied:
                    await connection.execute(statement)
                assert sqlstate(denied.value) == "42501"
                await connection.rollback()
                await connection.execute(text("SET ROLE maya_no_execute_probe"))
    finally:
        async with admin.connect() as connection:
            await connection.execute(text("DROP ROLE IF EXISTS maya_no_execute_probe"))
            await connection.commit()


async def test_0014_downgrade_and_upgrade_round_trip(
    engines: tuple[AsyncEngine, AsyncEngine], fresh_database: TestDatabase
) -> None:
    admin, app = engines
    config = alembic_config(fresh_database)

    async def function_names() -> list[Any]:
        async with admin.connect() as connection:
            return [
                row[0]
                for row in await connection.execute(
                    text("SELECT proname FROM pg_proc WHERE proname = ANY(:n) ORDER BY 1"),
                    {"n": list(SIGNATURES)},
                )
            ]

    assert await function_names() == sorted(SIGNATURES)
    await asyncio.to_thread(command.downgrade, config, "0013")
    assert await function_names() == []
    async with admin.connect() as connection:
        left = (
            await connection.execute(
                text(
                    "SELECT count(*) FROM pg_policies WHERE policyname = 'definer_open_work_lookup'"
                )
            )
        ).scalar()
    assert left == 0
    await asyncio.to_thread(command.upgrade, config, "head")
    assert await function_names() == sorted(SIGNATURES)
    async with scoped(app, None) as connection:
        assert (await connection.execute(ANY_ACCOUNT)).scalar() is False
    # Re-running the upgrade body over an already migrated database is idempotent.
    await asyncio.to_thread(command.downgrade, config, "0013")
    await asyncio.to_thread(command.upgrade, config, "head")
