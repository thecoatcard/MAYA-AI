"""Alembic environment: online, asyncpg, one transaction per revision.

Migrations run with the migration role, never the runtime role. The URL comes from, in order:
``config.attributes["migration_url"]`` (tests), ``-x env_file=<path>``, or the process
environment variable ``MAYA_MIGRATION_DATABASE_URL``.
"""

import asyncio

from alembic import context
from sqlalchemy import pool
from sqlalchemy.engine import Connection
from sqlalchemy.ext.asyncio import create_async_engine

from app.core.config import MigrationSettings

config = context.config


def migration_url() -> str:
    override = config.attributes.get("migration_url")
    if isinstance(override, str):
        return override
    env_file = context.get_x_argument(as_dictionary=True).get("env_file")
    return MigrationSettings(_env_file=env_file).migration_database_url.get_secret_value()


def run_migrations(connection: Connection) -> None:
    context.configure(connection=connection, target_metadata=None, transaction_per_migration=True)
    with context.begin_transaction():
        context.run_migrations()


async def run_async_migrations() -> None:
    engine = create_async_engine(migration_url(), poolclass=pool.NullPool, hide_parameters=True)
    try:
        async with engine.connect() as connection:
            await connection.run_sync(run_migrations)
    finally:
        await engine.dispose()


if context.is_offline_mode():
    raise RuntimeError("Offline SQL generation is not supported; run against a database")
asyncio.run(run_async_migrations())
