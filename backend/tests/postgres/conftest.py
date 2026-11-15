import asyncio

import pytest
from sqlalchemy.ext.asyncio import AsyncEngine, create_async_engine
from sqlalchemy.pool import NullPool

from tests.conftest import TestDatabase
from tests.support.postgres import SeededTenant, seed_tenant


@pytest.fixture(scope="session")
def tenants(migrated_database: TestDatabase) -> tuple[SeededTenant, SeededTenant]:
    """Two seeded workspaces shared by the whole session.

    Use them only in tests that read, or that write inside a transaction they roll back. A test
    that commits rows must use ``fresh_tenants`` so the shared rows stay exactly as seeded.
    """

    async def seed() -> tuple[SeededTenant, SeededTenant]:
        engine = create_async_engine(migrated_database.admin_url, poolclass=NullPool)
        try:
            async with engine.begin() as connection:
                return await seed_tenant(connection, "a"), await seed_tenant(connection, "b")
        finally:
            await engine.dispose()

    return asyncio.run(seed())


@pytest.fixture
async def fresh_tenants(admin_engine: AsyncEngine) -> tuple[SeededTenant, SeededTenant]:
    """Two new workspaces for one test that may commit rows."""
    async with admin_engine.begin() as connection:
        return await seed_tenant(connection, "a"), await seed_tenant(connection, "b")
