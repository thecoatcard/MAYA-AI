"""Row-level security as the restricted runtime role, against a real PostgreSQL database."""

import asyncio
from uuid import uuid4

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import TestDatabase, alembic_config
from tests.support.postgres import (
    ALL_PROTECTED_TABLES,
    DELETABLE_TABLES,
    TENANT_TABLES,
    UPDATE_ASSIGNMENT,
    SeededTenant,
    insert_statement,
    scoped,
    seed_tenant,
    sqlstate,
)

pytestmark = pytest.mark.postgres

Tenants = tuple[SeededTenant, SeededTenant]
MOVABLE_TABLES = DELETABLE_TABLES  # the auth tables allow no UPDATE of workspace_id at all


async def visible_ids(engine: AsyncEngine, workspace: object, table: str) -> set[object]:
    async with scoped(engine, workspace) as connection:  # type: ignore[arg-type]
        rows = await connection.execute(text(f"SELECT id FROM {table}"))
        return {row[0] for row in rows}


@pytest.mark.parametrize("table", ALL_PROTECTED_TABLES)
@pytest.mark.parametrize("setting", ["unset", "empty", "unrelated"])
async def test_default_deny_returns_no_rows(
    app_engine: AsyncEngine, tenants: Tenants, table: str, setting: str
) -> None:
    workspace = {"unset": None, "empty": "", "unrelated": uuid4()}[setting]
    assert await visible_ids(app_engine, workspace, table) == set()


@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_default_deny_blocks_writes_without_context(
    app_engine: AsyncEngine, tenants: Tenants, table: str
) -> None:
    sql, parameters = insert_statement(table, tenants[0])
    async with scoped(app_engine, None) as connection:
        with pytest.raises(DBAPIError) as caught:
            await connection.execute(text(sql), parameters)
    assert sqlstate(caught.value) == "42501"
    assert "row-level security" in str(caught.value.orig)


@pytest.mark.parametrize(
    "value", ["not-a-uuid", "00000000-0000-0000-0000-00000000000g", "1 OR 1=1"]
)
async def test_malformed_context_fails_closed(
    app_engine: AsyncEngine, tenants: Tenants, value: str
) -> None:
    async with scoped(app_engine, value) as connection:
        with pytest.raises(DBAPIError) as caught:
            await connection.execute(text("SELECT id FROM sessions"))
    assert sqlstate(caught.value) == "22P02"


@pytest.mark.parametrize("table", ALL_PROTECTED_TABLES)
async def test_each_workspace_sees_exactly_its_own_rows(
    app_engine: AsyncEngine, tenants: Tenants, table: str
) -> None:
    first, second = tenants
    assert await visible_ids(app_engine, first.workspace_id, table) == first.row_ids[table]
    assert await visible_ids(app_engine, second.workspace_id, table) == second.row_ids[table]
    assert first.row_ids[table].isdisjoint(second.row_ids[table])


@pytest.mark.parametrize("table", ALL_PROTECTED_TABLES)
async def test_a_foreign_row_cannot_be_read_even_by_its_id(
    app_engine: AsyncEngine, tenants: Tenants, table: str
) -> None:
    first, second = tenants
    foreign = next(iter(second.row_ids[table]))
    async with scoped(app_engine, first.workspace_id) as connection:
        rows = await connection.execute(
            text(f"SELECT id FROM {table} WHERE id = :id"),
            {"id": foreign},
        )
        assert rows.all() == []


@pytest.mark.parametrize("table", UPDATE_ASSIGNMENT)
async def test_updates_only_touch_the_own_workspace(
    app_engine: AsyncEngine, tenants: Tenants, table: str
) -> None:
    first, second = tenants
    statement = text(f"UPDATE {table} SET {UPDATE_ASSIGNMENT[table]} WHERE id = :id")
    async with scoped(app_engine, first.workspace_id) as connection:
        foreign = await connection.execute(statement, {"id": next(iter(second.row_ids[table]))})
        assert foreign.rowcount == 0
        own = await connection.execute(statement, {"id": next(iter(first.row_ids[table]))})
        assert own.rowcount == 1
        everything = await connection.execute(
            text(f"UPDATE {table} SET {UPDATE_ASSIGNMENT[table]}")
        )
        assert everything.rowcount == len(first.row_ids[table])


@pytest.mark.parametrize("table", DELETABLE_TABLES)
async def test_deletes_only_touch_the_own_workspace(
    app_engine: AsyncEngine, tenants: Tenants, table: str
) -> None:
    first, second = tenants
    async with scoped(app_engine, first.workspace_id) as connection:
        foreign = await connection.execute(
            text(f"DELETE FROM {table} WHERE id = :id"), {"id": next(iter(second.row_ids[table]))}
        )
        assert foreign.rowcount == 0
        everything = await connection.execute(text(f"DELETE FROM {table}"))
        assert everything.rowcount == len(first.row_ids[table])
    assert await visible_ids(app_engine, second.workspace_id, table) == second.row_ids[table]


@pytest.mark.parametrize("table", TENANT_TABLES)
async def test_inserts_must_carry_the_own_workspace(
    app_engine: AsyncEngine, tenants: Tenants, table: str
) -> None:
    first, second = tenants
    sql, own = insert_statement(table, first)
    async with scoped(app_engine, first.workspace_id) as connection:
        assert (await connection.execute(text(sql), own)).rowcount == 1
    foreign_sql, foreign = insert_statement(table, second)
    async with scoped(app_engine, first.workspace_id) as connection:
        with pytest.raises(DBAPIError) as caught:
            await connection.execute(text(foreign_sql), foreign)
    assert sqlstate(caught.value) == "42501"
    assert "row-level security" in str(caught.value.orig)


@pytest.mark.parametrize("table", MOVABLE_TABLES)
async def test_a_row_cannot_be_moved_to_another_workspace(
    app_engine: AsyncEngine, tenants: Tenants, table: str
) -> None:
    first, second = tenants
    async with scoped(app_engine, first.workspace_id) as connection:
        with pytest.raises(DBAPIError) as caught:
            await connection.execute(
                text(f"UPDATE {table} SET workspace_id = :target WHERE id = :id"),
                {"target": second.workspace_id, "id": next(iter(first.row_ids[table]))},
            )
    assert sqlstate(caught.value) == "42501"
    assert "row-level security" in str(caught.value.orig)


async def test_workspaces_can_only_be_created_for_the_context_workspace(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    first, second = tenants
    insert = text("INSERT INTO workspaces (id, name, slug) VALUES (:id, 'n', :slug)")
    created = uuid4()
    async with scoped(app_engine, created) as connection:
        result = await connection.execute(insert, {"id": created, "slug": f"ok-{created.hex[:12]}"})
        assert result.rowcount == 1
    for target in (second.workspace_id, uuid4()):
        async with scoped(app_engine, first.workspace_id) as connection:
            with pytest.raises(DBAPIError) as caught:
                await connection.execute(insert, {"id": target, "slug": f"no-{uuid4().hex[:12]}"})
        assert sqlstate(caught.value) == "42501"


async def test_workspace_names_and_slugs_are_not_listable_across_tenants(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    first, second = tenants
    async with scoped(app_engine, first.workspace_id) as connection:
        rows = (await connection.execute(text("SELECT id, name, slug FROM workspaces"))).all()
    assert [row[0] for row in rows] == [first.workspace_id]
    assert all(second.workspace_id.hex[:12] not in row[2] for row in rows)


async def test_foreign_keys_cannot_be_used_to_probe_other_workspaces(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    """Referential checks bypass row-level security, so a cross-workspace reference is rejected
    by the policy on the new row first and never reveals whether the parent exists."""
    first, second = tenants
    async with scoped(app_engine, first.workspace_id) as connection:
        with pytest.raises(DBAPIError) as caught:
            await connection.execute(
                text(
                    "INSERT INTO messages (workspace_id, session_id, sender, content) "
                    "VALUES (:ws, :session, 'user', 'probe')"
                ),
                {"ws": second.workspace_id, "session": second.session_id},
            )
    assert sqlstate(caught.value) == "42501"


async def test_context_set_by_a_previous_transaction_does_not_survive_commit(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    first, _ = tenants
    async with app_engine.connect() as connection:
        async with connection.begin():
            await connection.execute(
                text("SELECT set_config('app.workspace_id', :w, true)"),
                {"w": str(first.workspace_id)},
            )
            assert (await connection.execute(text("SELECT id FROM sessions"))).all() != []
        async with connection.begin():
            assert (await connection.execute(text("SELECT id FROM sessions"))).all() == []
            setting = await connection.execute(
                text("SELECT current_setting('app.workspace_id', true)")
            )
            assert setting.scalar_one() in (None, "")


async def test_negative_control_the_matrix_would_notice_disabled_row_level_security(
    fresh_database: TestDatabase,
) -> None:
    """Prove the checks above can fail: break one table on purpose and look for the leak."""
    await asyncio.to_thread(command.upgrade, alembic_config(fresh_database), "head")
    admin = create_async_engine(fresh_database.admin_url, poolclass=NullPool)
    app = create_async_engine(fresh_database.app_url, poolclass=NullPool)
    try:
        async with admin.begin() as connection:
            first = await seed_tenant(connection, "a")
            second = await seed_tenant(connection, "b")
        assert await visible_ids(app, first.workspace_id, "sessions") == first.row_ids["sessions"]
        async with admin.begin() as connection:
            await connection.execute(text("ALTER TABLE sessions DISABLE ROW LEVEL SECURITY"))
        leaked = await visible_ids(app, first.workspace_id, "sessions")
        assert leaked == first.row_ids["sessions"] | second.row_ids["sessions"]
        # The administrator always bypasses policies, which is why the suite uses the app role.
        async with admin.begin() as connection:
            await connection.execute(text("ALTER TABLE sessions ENABLE ROW LEVEL SECURITY"))
        every = await visible_ids(admin, first.workspace_id, "messages")
        assert every == first.row_ids["messages"] | second.row_ids["messages"]
    finally:
        await admin.dispose()
        await app.dispose()
