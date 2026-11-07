"""Integration tests for MCP Admin API endpoints (Phase 08)."""

from __future__ import annotations

from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from starlette.testclient import TestClient

from app.auth.dependencies import get_principal, get_tenant_database
from app.auth.models import Principal
from app.db.models import McpServer
from app.main import create_app
from app.mcp.normalizer import compute_server_schema_hash
from tests.support.settings import make_settings


@pytest.fixture
def test_principal() -> Principal:
    return Principal(
        user_id=uuid4(),
        workspace_id=uuid4(),
        role="admin",
        session_id=uuid4(),
    )


def test_mcp_admin_api_lifecycle(test_principal: Principal) -> None:
    app = create_app(make_settings())

    # In-memory mock storage for tenant
    stored_servers: dict[str, McpServer] = {}

    class MockRepo:
        def __init__(self, session: object = None) -> None:
            pass

        async def list_servers_for_workspace(
            self, workspace_id: object, is_active: bool | None = None
        ) -> list[McpServer]:
            return list(stored_servers.values())

        async def create_server(
            self,
            workspace_id: object,
            user_id: object,
            name: str,
            transport: str,
            command: str | None = None,
            args: list[object] | None = None,
            url: str | None = None,
            pinned_version: str | None = None,
            schema_hash: str | None = None,
            env_vars_encrypted: bytes | None = None,
            key_id: str | None = None,
            tools_manifest: list[object] | None = None,
        ) -> McpServer:
            srv_id = uuid4()
            srv = McpServer(
                id=srv_id,
                workspace_id=workspace_id,
                user_id=user_id,
                name=name,
                transport=transport,
                command=command,
                args=args or [],
                url=url,
                pinned_version=pinned_version,
                schema_hash=schema_hash,
                is_active=True,
                health_status="healthy",
                tool_count=len(tools_manifest) if tools_manifest else 0,
                tools_manifest=tools_manifest or [],
                consecutive_failures=0,
            )
            stored_servers[str(srv_id)] = srv
            return srv

        async def get_server_by_id(
            self, workspace_id: object, server_id: object
        ) -> McpServer | None:
            return stored_servers.get(str(server_id))

        async def delete_server(self, workspace_id: object, server_id: object) -> bool:
            return stored_servers.pop(str(server_id), None) is not None

    async def override_principal() -> Principal:
        return test_principal

    async def override_tenant_db() -> object:
        mock_db = MagicMock()
        mock_db.session = MagicMock()
        return mock_db

    app.dependency_overrides[get_principal] = override_principal
    app.dependency_overrides[get_tenant_database] = override_tenant_db

    client = TestClient(app)

    # 1. Register an MCP server
    raw_tools = [
        {
            "name": "web_search",
            "description": "Search the web",
            "inputSchema": {"type": "object", "properties": {"query": {"type": "string"}}},
        }
    ]
    computed_hash = compute_server_schema_hash(raw_tools)

    with pytest.MonkeyPatch.context() as mp:
        from app.api.v1 import mcp_admin

        mp.setattr(mcp_admin, "McpServerRepository", MockRepo)

        # POST /api/v1/mcp/servers
        create_resp = client.post(
            "/api/v1/mcp/servers",
            json={
                "name": "brave-search",
                "transport": "http",
                "url": "https://api.search.brave.com/res/v1/web/search",
                "pinned_version": "1.0.0",
                "schema_hash": computed_hash,
                "tools_manifest": raw_tools,
            },
        )
        assert create_resp.status_code == 201
        created_data = create_resp.json()
        srv_id = created_data["id"]
        assert created_data["name"] == "brave-search"
        assert created_data["tool_count"] == 1
        assert created_data["health_status"] == "healthy"

        # GET /api/v1/mcp/servers
        list_resp = client.get("/api/v1/mcp/servers")
        assert list_resp.status_code == 200
        servers_list = list_resp.json()
        assert len(servers_list) == 1
        assert servers_list[0]["id"] == srv_id

        # GET /api/v1/mcp/servers/{server_id}/health
        health_resp = client.get(f"/api/v1/mcp/servers/{srv_id}/health")
        assert health_resp.status_code == 200
        health_data = health_resp.json()
        assert health_data["health_status"] == "healthy"
        assert health_data["consecutive_failures"] == 0

        # DELETE /api/v1/mcp/servers/{server_id}
        del_resp = client.delete(f"/api/v1/mcp/servers/{srv_id}")
        assert del_resp.status_code == 204

        # Confirm deleted
        get_deleted_health = client.get(f"/api/v1/mcp/servers/{srv_id}/health")
        assert get_deleted_health.status_code == 404
