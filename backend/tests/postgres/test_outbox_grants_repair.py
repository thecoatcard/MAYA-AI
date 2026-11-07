"""Revision 0013 repairs the outbox grants and policy for databases migrated from old 0008."""

import asyncio
from collections.abc import AsyncIterator
from typing import Any

import pytest
from alembic import command
from alembic.config import Config
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import TestDatabase, alembic_config
from tests.support.postgres import scoped, seed_tenant, sqlstate

pytestmark = pytest.mark.postgres

OLD_POLICY = "task_outbox_events_tenant_isolation"
OLD_POLICY_SQL = (
    f"CREATE POLICY {OLD_POLICY} ON task_outbox_events "
    "USING (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)"
)
EXPECTED_TABLE_GRANTS = {("INSERT",), ("SELECT",)}
EXPECTED_COLUMN_GRANTS = {("dispatched_at",), ("status",)}
INSERT = text(
    "INSERT INTO task_outbox_events (workspace_id, task_id, event_type) "
    "VALUES (:workspace, :task, 'task.created')"
)
COUNT = text("SELECT count(*) FROM task_outbox_events")


@pytest.fixture
async def engines(fresh_database: TestDatabase) -> AsyncIterator[tuple[AsyncEngine, AsyncEngine]]:
    admin = create_async_engine(fresh_database.admin_url, poolclass=NullPool)
    app = create_async_engine(fresh_database.app_url, poolclass=NullPool)
    try:
        yield admin, app
    finally:
        await admin.dispose()
        await app.dispose()


async def migrate(function: Any, config: Config, revision: str) -> None:
    await asyncio.to_thread(function, config, revision)


async def state(admin: AsyncEngine) -> dict[str, Any]:
    async with admin.connect() as connection:
        policies = (
            await connection.execute(
                text(
                    "SELECT policyname, qual, with_check FROM pg_policies "
                    "WHERE tablename = 'task_outbox_events' ORDER BY 1"
                )
            )
        ).all()
        table = {
            tuple(row)
            for row in await connection.execute(
                text(
                    "SELECT privilege_type FROM information_schema.role_table_grants "
                    "WHERE table_name = 'task_outbox_events' AND grantee = 'maya_app'"
                )
            )
        }
        columns = {
            tuple(row)
            for row in await connection.execute(
                text(
                    "SELECT column_name FROM information_schema.column_privileges "
                    "WHERE table_name = 'task_outbox_events' AND grantee = 'maya_app' "
                    "AND privilege_type = 'UPDATE'"
                )
            )
        }
        flags = (
            await connection.execute(
                text(
                    "SELECT relrowsecurity, relforcerowsecurity, pg_get_userbyid(relowner) "
                    "FROM pg_class WHERE relname = 'task_outbox_events'"
                )
            )
        ).one()
        privileged = (
            await connection.execute(
                text("SELECT rolbypassrls OR rolsuper FROM pg_roles WHERE rolname = 'maya_app'")
            )
        ).scalar_one()
    return {
        "policies": [tuple(row) for row in policies],
        "table": table,
        "columns": columns,
        "flags": tuple(flags),
        "app_privileged": privileged,
    }


def assert_repaired(snapshot: dict[str, Any]) -> None:
    assert [row[0] for row in snapshot["policies"]] == ["ws_isolation"]
    _, using, with_check = snapshot["policies"][0]
    assert "app.workspace_id" in using
    assert using == with_check
    assert snapshot["table"] == EXPECTED_TABLE_GRANTS
    assert snapshot["columns"] == EXPECTED_COLUMN_GRANTS
    relrowsecurity, forced, owner = snapshot["flags"]
    assert (relrowsecurity, forced) == (True, True)
    assert owner != "maya_app"
    assert snapshot["app_privileged"] is False


async def assert_app_can_use_outbox(admin: AsyncEngine, app: AsyncEngine) -> None:
    async with admin.begin() as connection:
        first = await seed_tenant(connection, "a")
        second = await seed_tenant(connection, "b")
    async with app.connect() as connection, connection.begin():
        await connection.execute(
            text("SELECT set_config('app.workspace_id', :w, true)"), {"w": str(first.workspace_id)}
        )
        await connection.execute(INSERT, {"workspace": first.workspace_id, "task": first.task_id})
        assert (await connection.execute(COUNT)).scalar() == 1
        claimed = await connection.execute(
            text(
                "SELECT id FROM task_outbox_events WHERE status = 'pending' "
                "ORDER BY created_at FOR UPDATE SKIP LOCKED"
            )
        )
        await connection.execute(
            text(
                "UPDATE task_outbox_events SET status = 'dispatched', dispatched_at = now() "
                "WHERE id = :id"
            ),
            {"id": claimed.scalar_one()},
        )
    async with scoped(app, first.workspace_id) as connection:
        assert (await connection.execute(COUNT)).scalar() == 1
        # WITH CHECK refuses a write that targets another workspace.
        with pytest.raises(DBAPIError) as refused:
            await connection.execute(
                INSERT, {"workspace": second.workspace_id, "task": second.task_id}
            )
        assert sqlstate(refused.value) == "42501"
    async with scoped(app, None) as connection:
        assert (await connection.execute(COUNT)).scalar() == 0
    async with scoped(app, second.workspace_id) as connection:
        assert (await connection.execute(COUNT)).scalar() == 0
    # Least privilege: no DELETE and no update of other columns.
    async with scoped(app, first.workspace_id) as connection:
        with pytest.raises(DBAPIError) as denied:
            await connection.execute(text("DELETE FROM task_outbox_events"))
        assert sqlstate(denied.value) == "42501"
    async with scoped(app, first.workspace_id) as connection:
        with pytest.raises(DBAPIError) as denied:
            await connection.execute(text("UPDATE task_outbox_events SET event_type = 'x'"))
        assert sqlstate(denied.value) == "42501"


async def test_fresh_head_lets_the_runtime_role_use_the_outbox_inside_a_tenant(
    engines: tuple[AsyncEngine, AsyncEngine], fresh_database: TestDatabase
) -> None:
    admin, app = engines
    await migrate(command.upgrade, alembic_config(fresh_database), "head")
    assert_repaired(await state(admin))
    await assert_app_can_use_outbox(admin, app)


async def test_database_migrated_from_the_committed_0008_is_repaired_by_0013(
    engines: tuple[AsyncEngine, AsyncEngine], fresh_database: TestDatabase
) -> None:
    """Deterministic equivalent of the committed 0008: migrate to 0012, then put the outbox
    table back into the state the committed 0008 created (no grants, the old policy name)."""
    admin, app = engines
    config = alembic_config(fresh_database)
    await migrate(command.upgrade, config, "0012")
    async with admin.begin() as connection:
        await connection.execute(text("REVOKE ALL PRIVILEGES ON task_outbox_events FROM maya_app"))
        await connection.execute(text("DROP POLICY IF EXISTS ws_isolation ON task_outbox_events"))
        await connection.execute(text(OLD_POLICY_SQL))

    broken = await state(admin)
    assert broken["table"] == set()
    assert broken["columns"] == set()
    assert [row[0] for row in broken["policies"]] == [OLD_POLICY]
    async with admin.begin() as connection:
        seeded = await seed_tenant(connection, "a")
    async with scoped(app, seeded.workspace_id) as connection:
        with pytest.raises(DBAPIError) as denied:
            await connection.execute(
                INSERT, {"workspace": seeded.workspace_id, "task": seeded.task_id}
            )
        assert sqlstate(denied.value) == "42501"

    await migrate(command.upgrade, config, "0013")
    assert_repaired(await state(admin))
    await assert_app_can_use_outbox(admin, app)


async def test_0013_is_idempotent_on_the_patched_state_with_wide_grants_and_old_policy(
    engines: tuple[AsyncEngine, AsyncEngine], fresh_database: TestDatabase
) -> None:
    """The owner's database: grants applied by hand, but the old policy name."""
    admin, app = engines
    config = alembic_config(fresh_database)
    await migrate(command.upgrade, config, "0012")
    async with admin.begin() as connection:
        await connection.execute(text("DROP POLICY IF EXISTS ws_isolation ON task_outbox_events"))
        await connection.execute(text(OLD_POLICY_SQL))
        await connection.execute(
            text("GRANT SELECT, INSERT, UPDATE ON task_outbox_events TO maya_app")
        )

    await migrate(command.upgrade, config, "0013")
    repaired = await state(admin)
    assert_repaired(repaired)  # also proves the stray wider UPDATE grant was removed

    # Re-applying the revision body over the repaired state changes nothing.
    await migrate(command.downgrade, config, "0012")
    await migrate(command.upgrade, config, "0013")
    assert await state(admin) == repaired
    await assert_app_can_use_outbox(admin, app)


async def test_0013_downgrade_and_upgrade_round_trip(
    engines: tuple[AsyncEngine, AsyncEngine], fresh_database: TestDatabase
) -> None:
    admin, _ = engines
    config = alembic_config(fresh_database)
    await migrate(command.upgrade, config, "head")
    repaired = await state(admin)
    assert_repaired(repaired)

    await migrate(command.downgrade, config, "0012")
    reverted = await state(admin)
    assert reverted["table"] == set()
    assert reverted["columns"] == set()
    assert [row[0] for row in reverted["policies"]] == [OLD_POLICY]
    assert reverted["flags"][:2] == (True, True)

    await migrate(command.upgrade, config, "0013")
    assert await state(admin) == repaired
