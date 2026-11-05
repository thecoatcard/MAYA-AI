"""tenant_transaction and the startup role guard against a real PostgreSQL database."""

import asyncio
from uuid import uuid4

import pytest
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine

from app.db.tenant import UnsafeDatabaseRole, tenant_transaction, verify_runtime_role
from tests.conftest import TestDatabase
from tests.support.postgres import SeededTenant

pytestmark = pytest.mark.postgres

Tenants = tuple[SeededTenant, SeededTenant]


@pytest.fixture
def tenants(fresh_tenants: Tenants) -> Tenants:
    """This module commits rows, so it works on its own workspaces."""
    return fresh_tenants


async def session_ids(engine: AsyncEngine, workspace_id: object) -> set[object]:
    async with tenant_transaction(engine, workspace_id) as connection:  # type: ignore[arg-type]
        return {row[0] for row in await connection.execute(text("SELECT id FROM sessions"))}


async def test_queries_see_only_the_context_workspace(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    first, second = tenants
    assert await session_ids(app_engine, first.workspace_id) == first.row_ids["sessions"]
    assert await session_ids(app_engine, second.workspace_id) == second.row_ids["sessions"]


async def test_no_context_means_no_rows_and_no_writes(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    assert await session_ids(app_engine, None) == set()
    with pytest.raises(DBAPIError):
        async with tenant_transaction(app_engine, None) as connection:
            await connection.execute(
                text("INSERT INTO sessions (workspace_id, user_id) VALUES (:w, :u)"),
                {"w": tenants[0].workspace_id, "u": tenants[0].user_id},
            )


async def test_context_does_not_leak_through_a_reused_pooled_connection(
    migrated_database: TestDatabase, tenants: Tenants
) -> None:
    first, _ = tenants
    engine = create_async_engine(migrated_database.app_url, pool_size=1, max_overflow=0)
    try:
        backend_pids: set[int] = set()
        for outcome in ("commit", "rollback", "exception"):
            try:
                async with tenant_transaction(engine, first.workspace_id) as connection:
                    backend_pids.add(
                        (await connection.execute(text("SELECT pg_backend_pid()"))).scalar_one()
                    )
                    assert await session_ids_on(connection) == first.row_ids["sessions"]
                    if outcome == "rollback":
                        await connection.rollback()
                    if outcome == "exception":
                        raise RuntimeError("boom")
            except RuntimeError:
                assert outcome == "exception"
            async with tenant_transaction(engine, None) as connection:
                backend_pids.add(
                    (await connection.execute(text("SELECT pg_backend_pid()"))).scalar_one()
                )
                assert await session_ids_on(connection) == set()
                setting = await connection.execute(
                    text("SELECT current_setting('app.workspace_id', true)")
                )
                assert setting.scalar_one() in (None, "")
        assert len(backend_pids) == 1  # the same physical connection served every block
    finally:
        await engine.dispose()


async def session_ids_on(connection: object) -> set[object]:
    result = await connection.execute(text("SELECT id FROM sessions"))  # type: ignore[attr-defined]
    return {row[0] for row in result}


async def test_concurrent_requests_from_two_tenants_never_see_each_other(
    migrated_database: TestDatabase, tenants: Tenants
) -> None:
    engine = create_async_engine(migrated_database.app_url, pool_size=3, max_overflow=0)

    async def worker(index: int) -> None:
        tenant = tenants[index % 2]
        for _ in range(5):
            async with tenant_transaction(engine, tenant.workspace_id) as connection:
                await connection.execute(text("SELECT pg_sleep(0.005)"))
                rows = {row[0] for row in await connection.execute(text("SELECT id FROM sessions"))}
                assert rows == tenant.row_ids["sessions"]

    try:
        await asyncio.gather(*(worker(index) for index in range(24)))
    finally:
        await engine.dispose()


async def test_success_commits_and_an_exception_rolls_back(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    first, _ = tenants
    kept, discarded = uuid4(), uuid4()
    insert = text("INSERT INTO sessions (id, workspace_id, user_id) VALUES (:id, :w, :u)")
    async with tenant_transaction(app_engine, first.workspace_id) as connection:
        await connection.execute(insert, {"id": kept, "w": first.workspace_id, "u": first.user_id})
    with pytest.raises(RuntimeError):
        async with tenant_transaction(app_engine, first.workspace_id) as connection:
            await connection.execute(
                insert, {"id": discarded, "w": first.workspace_id, "u": first.user_id}
            )
            raise RuntimeError("boom")
    visible = await session_ids(app_engine, first.workspace_id)
    assert kept in visible
    assert discarded not in visible


async def test_a_different_workspace_cannot_use_the_row_that_was_committed(
    app_engine: AsyncEngine, tenants: Tenants
) -> None:
    first, second = tenants
    assert first.row_ids["sessions"] <= await session_ids(app_engine, first.workspace_id)
    assert (await session_ids(app_engine, second.workspace_id)).isdisjoint(
        await session_ids(app_engine, first.workspace_id)
    )


async def test_the_runtime_role_passes_the_startup_guard(app_engine: AsyncEngine) -> None:
    await verify_runtime_role(app_engine)


async def test_the_administrator_role_fails_the_startup_guard(admin_engine: AsyncEngine) -> None:
    with pytest.raises(UnsafeDatabaseRole):
        await verify_runtime_role(admin_engine)
