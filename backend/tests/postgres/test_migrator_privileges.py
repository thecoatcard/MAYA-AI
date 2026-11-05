"""The migrations must also work for a migration role that is NOT a superuser.

The local bootstrap administrator is a superuser, which would hide any privilege problem. This
test uses a throwaway role with only CREATEROLE and ownership of a throwaway database.
"""

import asyncio
import secrets
from collections.abc import AsyncIterator

import pytest
from alembic import command
from sqlalchemy import text
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import PostgresTarget, TestDatabase, alembic_config, drop_database

pytestmark = pytest.mark.postgres

PROBE_PASSWORD = "probe-example-password"


async def administer(target: PostgresTarget, database: str, *statements: str) -> None:
    engine = create_async_engine(
        target.admin_url.set(database=database), isolation_level="AUTOCOMMIT", poolclass=NullPool
    )
    try:
        async with engine.connect() as connection:
            for statement in statements:
                await connection.execute(text(statement))
    finally:
        await engine.dispose()


@pytest.fixture
async def limited_migrator(
    postgres_target: PostgresTarget,
) -> AsyncIterator[tuple[str, TestDatabase]]:
    suffix = secrets.token_hex(6)
    role, name = f"maya_probe_{suffix}", f"maya_test_{suffix}"
    await administer(
        postgres_target,
        "postgres",
        f"CREATE ROLE {role} LOGIN PASSWORD '{PROBE_PASSWORD}' CREATEROLE NOSUPERUSER",
        f'CREATE DATABASE "{name}" OWNER {role}',
    )
    # Installing pgvector needs a superuser; the migrations only say CREATE EXTENSION IF NOT EXISTS.
    await administer(postgres_target, name, "CREATE EXTENSION vector", "CREATE EXTENSION pg_trgm")
    database = TestDatabase(
        name,
        postgres_target.admin_url.set(database=name, username=role, password=PROBE_PASSWORD),
        postgres_target.app_url.set(database=name),
    )
    try:
        yield role, database
    finally:
        await asyncio.to_thread(drop_database, postgres_target, database)
        await administer(
            postgres_target, "postgres", f"DROP OWNED BY {role}", f"DROP ROLE IF EXISTS {role}"
        )


async def version(database: TestDatabase) -> str | None:
    engine = create_async_engine(database.admin_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            row = await connection.execute(text("SELECT version_num FROM alembic_version"))
            return row.scalar_one_or_none()
    finally:
        await engine.dispose()


async def test_a_migration_role_without_the_right_to_become_the_definer_fails_clearly(
    postgres_target: PostgresTarget, limited_migrator: tuple[str, TestDatabase]
) -> None:
    _, database = limited_migrator
    # Make sure the cluster-level definer role exists, as it would after any earlier migration.
    await administer(
        postgres_target,
        "postgres",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'maya_auth_definer') "
        "THEN CREATE ROLE maya_auth_definer NOLOGIN; END IF; END $$",
    )
    config = alembic_config(database)
    with pytest.raises(Exception) as caught:
        await asyncio.to_thread(command.upgrade, config, "head")
    assert "maya_auth_definer" in str(caught.value)
    assert await version(database) == "0003"  # revision 0004 rolled back as a unit


async def test_a_migration_role_that_may_set_role_to_the_definer_can_migrate_and_roll_back(
    postgres_target: PostgresTarget, limited_migrator: tuple[str, TestDatabase]
) -> None:
    role, database = limited_migrator
    await administer(
        postgres_target,
        "postgres",
        "DO $$ BEGIN IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = 'maya_auth_definer') "
        "THEN CREATE ROLE maya_auth_definer NOLOGIN; END IF; END $$",
        f"GRANT maya_auth_definer TO {role} WITH SET TRUE, INHERIT FALSE",
    )
    config = alembic_config(database)
    await asyncio.to_thread(command.upgrade, config, "head")
    assert await version(database) == "0004"

    engine = create_async_engine(database.admin_url, poolclass=NullPool)
    try:
        async with engine.connect() as connection:
            superuser = (
                await connection.execute(
                    text("SELECT rolsuper FROM pg_roles WHERE rolname = current_user")
                )
            ).scalar_one()
            assert superuser is False  # the point of this test
            owners = (
                await connection.execute(
                    text(
                        "SELECT proname, pg_get_userbyid(proowner) FROM pg_proc "
                        "WHERE proname LIKE 'auth_find_account_by_%' ORDER BY 1"
                    )
                )
            ).all()
            flags = (
                await connection.execute(
                    text(
                        "SELECT count(*) FROM pg_class WHERE relnamespace = 'public'::regnamespace "
                        "AND relkind = 'r' AND relforcerowsecurity AND relrowsecurity"
                    )
                )
            ).scalar_one()
    finally:
        await engine.dispose()
    assert [tuple(row) for row in owners] == [
        ("auth_find_account_by_email", "maya_auth_definer"),
        ("auth_find_account_by_username", "maya_auth_definer"),
    ]
    assert flags == 14

    await asyncio.to_thread(command.downgrade, config, "base")
    assert await version(database) is None
