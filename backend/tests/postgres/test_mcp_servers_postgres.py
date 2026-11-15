"""PostgreSQL integration tests for McpServer model, repository, and RLS tenant isolation."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.db.repositories.mcp_servers import McpServerRepository
from app.db.tenant import tenant_session
from tests.support.postgres import SeededTenant

pytestmark = pytest.mark.postgres

Tenants = tuple[SeededTenant, SeededTenant]


@pytest.fixture
def tenants(fresh_tenants: Tenants) -> Tenants:
    return fresh_tenants


async def test_mcp_server_crud_in_tenant_session(
    app_engine: AsyncEngine,
    tenants: Tenants,
) -> None:
    tenant_1, _ = tenants

    async with tenant_session(app_engine, tenant_1.workspace_id) as session:
        repo = McpServerRepository(session)

        # 1. Create server
        server = await repo.create_server(
            workspace_id=tenant_1.workspace_id,
            user_id=tenant_1.user_id,
            name="github",
            transport="stdio",
            command="github-mcp-server",
            args=["--port", "8080"],
            pinned_version="1.0.4",
            schema_hash="abc123hash",
            tools_manifest=[{"name": "get_issue"}, {"name": "create_pull_request"}],
        )
        await session.commit()

        server_id = server.id
        assert server.name == "github"
        assert server.health_status == "healthy"
        assert server.tool_count == 2
        assert server.is_active is True

    # 2. Retrieve server
    async with tenant_session(app_engine, tenant_1.workspace_id) as session:
        repo = McpServerRepository(session)
        fetched = await repo.get_server_by_id(tenant_1.workspace_id, server_id)
        assert fetched is not None
        assert fetched.name == "github"
        assert len(fetched.tools_manifest) == 2

        # 3. Update health status
        updated = await repo.update_health_status(
            workspace_id=tenant_1.workspace_id,
            server_id=server_id,
            health_status="degraded",
            consecutive_failures=3,
        )
        assert updated is True
        await session.commit()

    async with tenant_session(app_engine, tenant_1.workspace_id) as session:
        repo = McpServerRepository(session)
        refetched = await repo.get_server_by_id(tenant_1.workspace_id, server_id)
        assert refetched is not None
        assert refetched.health_status == "degraded"
        assert refetched.consecutive_failures == 3

        # 4. List servers (tenant_1 already has 1 seeded server + 1 created server = 2)
        servers = await repo.list_servers_for_workspace(tenant_1.workspace_id, is_active=True)
        assert len(servers) == 2
        assert any(s.id == server_id for s in servers)

        # 5. Delete server
        deleted = await repo.delete_server(tenant_1.workspace_id, server_id)
        assert deleted is True
        await session.commit()

    async with tenant_session(app_engine, tenant_1.workspace_id) as session:
        repo = McpServerRepository(session)
        final_check = await repo.get_server_by_id(tenant_1.workspace_id, server_id)
        assert final_check is None
        remaining = await repo.list_servers_for_workspace(tenant_1.workspace_id, is_active=True)
        assert len(remaining) == 1


async def test_mcp_server_rls_tenant_isolation(
    app_engine: AsyncEngine,
    tenants: Tenants,
) -> None:
    tenant_1, tenant_2 = tenants

    # Tenant 1 creates server
    async with tenant_session(app_engine, tenant_1.workspace_id) as session:
        repo_1 = McpServerRepository(session)
        s1 = await repo_1.create_server(
            workspace_id=tenant_1.workspace_id,
            user_id=tenant_1.user_id,
            name="filesystem",
            transport="stdio",
            command="mcp-server-filesystem",
            schema_hash="hash_t1",
        )
        await session.commit()
        s1_id = s1.id

    # Tenant 2 tries to read Tenant 1's server -> must return None
    async with tenant_session(app_engine, tenant_2.workspace_id) as session:
        repo_2 = McpServerRepository(session)
        t2_view = await repo_2.get_server_by_id(tenant_2.workspace_id, s1_id)
        assert t2_view is None

        # Tenant 2 listing -> sees only Tenant 2's seeded server, never Tenant 1's server
        t2_list = await repo_2.list_servers_for_workspace(tenant_2.workspace_id)
        assert len(t2_list) == 1
        assert s1_id not in [s.id for s in t2_list]

        # Tenant 2 direct query by ID in raw SQL -> 0 rows
        res = await session.execute(
            text("SELECT count(*) FROM mcp_servers WHERE id = :id"),
            {"id": s1_id},
        )
        assert res.scalar_one() == 0

        # Tenant 2 attempt to delete Tenant 1's server -> 0 affected
        deleted = await repo_2.delete_server(tenant_2.workspace_id, s1_id)
        assert deleted is False
