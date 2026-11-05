"""Shared fixtures for tests that need the local PostgreSQL service.

Configuration (all optional; without it the PostgreSQL tests are SKIPPED, which is not a pass):

* ``MAYA_TEST_POSTGRES_ADMIN_URL`` and ``MAYA_TEST_POSTGRES_APP_URL``: asyncpg URLs of the
  migration (administrator) role and the runtime role, or
* ``MAYA_TEST_ENV_FILE``: a dotenv file such as the root ``.env.example`` that holds
  ``MAYA_MIGRATION_DATABASE_URL`` and ``MAYA_DATABASE_URL``.

Set ``MAYA_REQUIRE_POSTGRES_TESTS=1`` to turn a missing configuration into a failure.
Every test database is created with a random ``maya_test_`` name, only on a loopback host, and
dropped afterwards. The persistent development database is never touched.
"""

import asyncio
import os
import re
import secrets
from collections.abc import AsyncIterator, Iterator
from dataclasses import dataclass
from pathlib import Path

import pytest
from alembic import command
from alembic.config import Config
from dotenv import dotenv_values
from sqlalchemy import text
from sqlalchemy.engine import URL, make_url
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

BACKEND_DIR = Path(__file__).resolve().parent.parent
LOOPBACK_HOSTS = {"127.0.0.1", "localhost", "::1"}
TEST_DATABASE_NAME = re.compile(r"maya_test_[0-9a-f]{12}")


@dataclass(frozen=True)
class PostgresTarget:
    admin_url: URL
    app_url: URL


@dataclass(frozen=True)
class TestDatabase:
    __test__ = False

    name: str
    admin_url: URL
    app_url: URL


def _load_target() -> PostgresTarget | None:
    admin = os.environ.get("MAYA_TEST_POSTGRES_ADMIN_URL")
    app = os.environ.get("MAYA_TEST_POSTGRES_APP_URL")
    env_file = os.environ.get("MAYA_TEST_ENV_FILE")
    if (admin is None or app is None) and env_file:
        values = dotenv_values(env_file)
        admin = admin or values.get("MAYA_MIGRATION_DATABASE_URL")
        app = app or values.get("MAYA_DATABASE_URL")
    if not admin or not app:
        return None
    return PostgresTarget(make_url(admin), make_url(app))


@pytest.fixture(scope="session")
def postgres_target() -> PostgresTarget:
    target = _load_target()
    if target is None:
        message = "Local PostgreSQL test targets are not configured"
        if os.environ.get("MAYA_REQUIRE_POSTGRES_TESTS") == "1":
            pytest.fail(message)
        pytest.skip(message)
    for url in (target.admin_url, target.app_url):
        if url.host not in LOOPBACK_HOSTS:
            pytest.fail("PostgreSQL tests may only target a loopback host")
    return target


async def _administer(target: PostgresTarget, statement: str) -> None:
    engine = create_async_engine(
        target.admin_url.set(database="postgres"),
        isolation_level="AUTOCOMMIT",
        poolclass=NullPool,
    )
    try:
        async with engine.connect() as connection:
            await connection.execute(text(statement))
    finally:
        await engine.dispose()


def create_database(target: PostgresTarget) -> TestDatabase:
    name = f"maya_test_{secrets.token_hex(6)}"
    assert TEST_DATABASE_NAME.fullmatch(name)
    asyncio.run(_administer(target, f'CREATE DATABASE "{name}"'))
    return TestDatabase(
        name, target.admin_url.set(database=name), target.app_url.set(database=name)
    )


def drop_database(target: PostgresTarget, database: TestDatabase) -> None:
    # Never drop anything that this harness did not create itself.
    assert TEST_DATABASE_NAME.fullmatch(database.name)
    asyncio.run(_administer(target, f'DROP DATABASE IF EXISTS "{database.name}" WITH (FORCE)'))


def alembic_config(database: TestDatabase) -> Config:
    config = Config(str(BACKEND_DIR / "alembic.ini"))
    config.attributes["migration_url"] = database.admin_url.render_as_string(hide_password=False)
    return config


@pytest.fixture(scope="session")
def migrated_database(postgres_target: PostgresTarget) -> Iterator[TestDatabase]:
    database = create_database(postgres_target)
    try:
        command.upgrade(alembic_config(database), "head")
        yield database
    finally:
        drop_database(postgres_target, database)


@pytest.fixture
def fresh_database(postgres_target: PostgresTarget) -> Iterator[TestDatabase]:
    """An empty database for tests that migrate up and down themselves."""
    database = create_database(postgres_target)
    try:
        yield database
    finally:
        drop_database(postgres_target, database)


@pytest.fixture
async def admin_engine(migrated_database: TestDatabase) -> AsyncIterator[AsyncEngine]:
    """Superuser engine: bypasses row-level security. Use it for seeding and inspection only."""
    engine = create_async_engine(migrated_database.admin_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()


@pytest.fixture
async def app_engine(migrated_database: TestDatabase) -> AsyncIterator[AsyncEngine]:
    """Engine for the restricted runtime role: what the API really uses."""
    engine = create_async_engine(migrated_database.app_url, poolclass=NullPool)
    try:
        yield engine
    finally:
        await engine.dispose()
