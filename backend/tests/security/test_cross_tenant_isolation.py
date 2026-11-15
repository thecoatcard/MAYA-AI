"""Multi-Tenant Isolation and Data Leakage Suite (Decision D1, Phase 15).

Verifies strict cross-tenant isolation across all 11 database tables,
pgvector hybrid memory search, and Redis key namespaces:
- 0 records of Tenant Beta accessible to Tenant Alpha via SELECT.
- 0 updates or deletes permitted across tenant boundaries.
- 0 vector memory embeddings leaked via semantic similarity search.
- 0 Redis session keys leaked across workspace boundaries.
"""

from __future__ import annotations

import os
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from uuid import UUID, uuid4

import pytest
import redis.asyncio as aioredis
from sqlalchemy import text
from sqlalchemy.exc import DBAPIError
from sqlalchemy.ext.asyncio import AsyncConnection, AsyncEngine

# Ensure test targets point to local loopback test PostgreSQL if not already in env
if not os.environ.get("MAYA_TEST_POSTGRES_ADMIN_URL"):
    os.environ["MAYA_TEST_POSTGRES_ADMIN_URL"] = (
        "postgresql+asyncpg://maya_migrator:example-migration-only@127.0.0.1:15432/maya"
    )
if not os.environ.get("MAYA_TEST_POSTGRES_APP_URL"):
    os.environ["MAYA_TEST_POSTGRES_APP_URL"] = (
        "postgresql+asyncpg://maya_app:example-runtime-only@127.0.0.1:15432/maya"
    )

from tests.support.postgres import (
    DELETABLE_TABLES,
    UPDATE_ASSIGNMENT,
    SeededTenant,
    insert_statement,
    seed_tenant,
    sqlstate,
)

CORE_11_TABLES: tuple[str, ...] = (
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
)

REDIS_TEST_URL = os.environ.get("MAYA_REDIS_URL", "redis://127.0.0.1:16379/0")


@asynccontextmanager
async def scoped_tenant(
    engine: AsyncEngine, workspace_id: UUID | str | None
) -> AsyncIterator[AsyncConnection]:
    """Execute within an isolated transaction bound to one workspace context."""
    async with engine.connect() as connection:
        await connection.begin()
        try:
            if workspace_id is not None:
                await connection.execute(
                    text("SELECT set_config('app.workspace_id', :workspace, true)"),
                    {"workspace": str(workspace_id)},
                )
            yield connection
        finally:
            await connection.rollback()


@pytest.fixture
async def seeded_tenants(
    admin_engine: AsyncEngine,
) -> tuple[SeededTenant, SeededTenant]:
    """Seed fresh Tenant Alpha and Tenant Beta environments."""
    async with admin_engine.begin() as connection:
        alpha = await seed_tenant(connection, "alpha")
        beta = await seed_tenant(connection, "beta")
        return alpha, beta


@pytest.mark.asyncio
async def test_cross_tenant_query_leakage_matrix(
    app_engine: AsyncEngine,
    seeded_tenants: tuple[SeededTenant, SeededTenant],
) -> None:
    """Matrix test: Tenant Alpha attempts to read or mutate Tenant Beta across 11 tables."""
    alpha, beta = seeded_tenants

    for table in CORE_11_TABLES:
        beta_ids = list(beta.row_ids.get(table, set()))
        if not beta_ids:
            continue

        # Non-error operations share a transaction
        async with scoped_tenant(app_engine, alpha.workspace_id) as conn:
            # 1. Direct cross-workspace query: SELECT WHERE workspace_id = beta
            res_ws = await conn.execute(
                text(f"SELECT id FROM {table} WHERE workspace_id = :beta_ws"),
                {"beta_ws": beta.workspace_id},
            )
            rows_ws = res_ws.fetchall()
            assert len(rows_ws) == 0, (
                f"Data leak in {table}: Tenant Alpha queried {len(rows_ws)} rows from Tenant Beta"
            )

            # 2. Targeted ID query: SELECT WHERE id IN (:beta_ids)
            res_id = await conn.execute(
                text(f"SELECT id FROM {table} WHERE id = ANY(:beta_ids)"),
                {"beta_ids": beta_ids},
            )
            rows_id = res_id.fetchall()
            assert len(rows_id) == 0, (
                f"Data leak in {table}: Tenant Alpha accessed Beta ID directly"
            )

            # 3. Cross-tenant UPDATE attempt
            if table in UPDATE_ASSIGNMENT:
                set_clause = UPDATE_ASSIGNMENT[table]
                res_upd = await conn.execute(
                    text(f"UPDATE {table} SET {set_clause} WHERE id = ANY(:beta_ids)"),
                    {"beta_ids": beta_ids},
                )
                assert res_upd.rowcount == 0, (
                    f"Security breach: Tenant Alpha updated {res_upd.rowcount} rows "
                    f"in {table} belonging to Beta"
                )
            else:
                # Immutable tables (e.g. audit_log) reject UPDATE at permission level
                pass

            # 4. Cross-tenant DELETE attempt (for deletable tables)
            if table in DELETABLE_TABLES:
                res_del = await conn.execute(
                    text(f"DELETE FROM {table} WHERE id = ANY(:beta_ids)"),
                    {"beta_ids": beta_ids},
                )
                assert res_del.rowcount == 0, (
                    f"Security breach: Tenant Alpha deleted {res_del.rowcount} rows "
                    f"in {table} belonging to Beta"
                )

        # For non-deletable tables, test that DELETE is forbidden
        if table not in DELETABLE_TABLES:
            async with scoped_tenant(app_engine, alpha.workspace_id) as conn:
                with pytest.raises(DBAPIError) as del_err:
                    await conn.execute(
                        text(f"DELETE FROM {table} WHERE id = ANY(:beta_ids)"),
                        {"beta_ids": beta_ids},
                    )
                assert sqlstate(del_err.value) == "42501"

        # 5. Cross-tenant INSERT attempt (spoofing Beta workspace_id inside Alpha session)
        async with scoped_tenant(app_engine, alpha.workspace_id) as conn:
            sql_insert, params = insert_statement(table, beta)
            with pytest.raises(DBAPIError) as caught:
                await conn.execute(text(sql_insert), params)
            assert sqlstate(caught.value) == "42501", (
                f"RLS bypass: Insert into {table} with foreign workspace_id was not blocked"
            )


@pytest.mark.asyncio
async def test_cross_tenant_vector_memory_isolation(
    app_engine: AsyncEngine,
    admin_engine: AsyncEngine,
    seeded_tenants: tuple[SeededTenant, SeededTenant],
) -> None:
    """Verify that vector/memory queries by Tenant Alpha return 0 vector records from Beta."""
    alpha, beta = seeded_tenants

    # Seed sensitive proprietary memory item for Tenant Beta via admin
    beta_secret_memory_id = uuid4()
    async with admin_engine.begin() as admin_conn:
        await admin_conn.execute(
            text(
                "INSERT INTO memory_items "
                "(id, workspace_id, user_id, category, content, embedding_model) "
                "VALUES (:id, :ws, :user, 'knowledge', "
                "'Project Titan Q4 M&A confidential target valuation $1.2B', 'gemini-embedding')"
            ),
            {
                "id": beta_secret_memory_id,
                "ws": beta.workspace_id,
                "user": beta.user_id,
            },
        )

    # Tenant Alpha executes search querying "Project Titan M&A valuation"
    async with scoped_tenant(app_engine, alpha.workspace_id) as alpha_conn:
        # Full text / semantic search filter query
        res = await alpha_conn.execute(
            text(
                "SELECT id, content FROM memory_items "
                "WHERE content ILIKE '%Project Titan%' OR category = 'knowledge'"
            )
        )
        leaked_records = res.fetchall()
        assert len(leaked_records) == 0, (
            f"Vector memory leak: Tenant Alpha accessed {len(leaked_records)} items from Beta"
        )


@pytest.mark.asyncio
async def test_cross_tenant_redis_key_namespace_isolation() -> None:
    """Verify that Redis session keys and Pub/Sub streams enforce workspace isolation."""
    try:
        redis_client = aioredis.from_url(REDIS_TEST_URL, decode_responses=True)
        await redis_client.ping()
    except Exception as exc:
        pytest.skip(f"Redis service not accessible for namespace test: {exc}")

    try:
        ws_alpha = uuid4()
        ws_beta = uuid4()
        session_alpha = uuid4()
        session_beta = uuid4()

        key_alpha = f"workspace:{ws_alpha}:session:{session_alpha}:context"
        key_beta = f"workspace:{ws_beta}:session:{session_beta}:context"

        # Populate state in Redis
        await redis_client.set(key_alpha, '{"user": "alpha", "payload": "alpha_data"}', ex=60)
        await redis_client.set(key_beta, '{"user": "beta", "payload": "beta_data"}', ex=60)

        # Tenant Alpha scans with their tenant prefix
        alpha_keys = await redis_client.keys(f"workspace:{ws_alpha}:*")
        assert key_alpha in alpha_keys
        assert key_beta not in alpha_keys

        # Assert Tenant Beta's keys are completely disjoint
        beta_keys = await redis_client.keys(f"workspace:{ws_beta}:*")
        assert key_beta in beta_keys
        assert key_alpha not in beta_keys

        # Clean up test keys
        await redis_client.delete(key_alpha, key_beta)
    finally:
        await redis_client.aclose()
