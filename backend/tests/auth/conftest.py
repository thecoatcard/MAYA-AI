from collections.abc import AsyncIterator

import pytest
from sqlalchemy.ext.asyncio import create_async_engine
from sqlalchemy.pool import NullPool

from app.auth.postgres import PostgresAuthStore
from app.auth.store import AuthStore
from tests.conftest import TestDatabase
from tests.support.auth import Harness, build_harness
from tests.support.fake_store import FakeAuthStore


@pytest.fixture(params=["fake", pytest.param("postgres", marks=pytest.mark.postgres)])
def store_kind(request: pytest.FixtureRequest) -> str:
    return str(request.param)


@pytest.fixture
def store_database(store_kind: str, request: pytest.FixtureRequest) -> TestDatabase | None:
    """Resolved synchronously (it creates and migrates a database) and only when needed."""
    if store_kind == "fake":
        return None
    database: TestDatabase = request.getfixturevalue("migrated_database")
    return database


@pytest.fixture
async def auth_store(store_database: TestDatabase | None) -> AsyncIterator[AuthStore]:
    """Every test using this fixture runs against the in-memory store and the real database."""
    if store_database is None:
        yield FakeAuthStore()
        return
    engine = create_async_engine(store_database.app_url, poolclass=NullPool)
    try:
        yield PostgresAuthStore(engine)
    finally:
        await engine.dispose()


@pytest.fixture
def harness(auth_store: AuthStore) -> Harness:
    return build_harness(auth_store)
