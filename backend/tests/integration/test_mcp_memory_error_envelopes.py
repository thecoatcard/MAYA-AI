"""MCP 'http' alias and memory without a model key answer with the standard JSON envelope."""

from types import SimpleNamespace
from typing import Any
from unittest.mock import MagicMock
from uuid import uuid4

import pytest
from fastapi.testclient import TestClient

from app.api.v1 import mcp_admin
from app.auth.dependencies import get_principal, get_tenant_database
from app.auth.models import Principal
from app.db.models import McpServer
from app.main import create_app
from tests.support.settings import make_settings


@pytest.fixture
def client() -> TestClient:
    app = create_app(make_settings())
    principal = Principal(user_id=uuid4(), workspace_id=uuid4(), role="admin", session_id=uuid4())

    async def override_principal() -> Principal:
        return principal

    async def override_tenant() -> Any:
        return MagicMock()

    app.dependency_overrides[get_principal] = override_principal
    app.dependency_overrides[get_tenant_database] = override_tenant
    # A gateway whose provider client is missing, exactly as with no Gemini key configured.
    app.state.resources = SimpleNamespace(model_gateway=SimpleNamespace(client=None))
    return TestClient(app)


def test_memory_search_and_ingest_without_a_key_are_a_503_envelope(client: TestClient) -> None:
    search = client.post("/api/v1/memory/search", json={"query": "anything"})
    ingest = client.post(
        "/api/v1/memory/items", json={"category": "episodic", "content": "remember this"}
    )
    for response in (search, ingest):
        assert response.status_code == 503
        assert response.headers["content-type"].startswith("application/json")
        assert response.json() == {"error": "model_unavailable"}


def test_register_http_transport_is_stored_as_streamable_http(
    client: TestClient, monkeypatch: pytest.MonkeyPatch
) -> None:
    stored: dict[str, Any] = {}

    class Repo:
        def __init__(self, session: object = None) -> None:
            pass

        async def create_server(self, **kwargs: Any) -> McpServer:
            stored.update(kwargs)
            return McpServer(
                id=uuid4(),
                is_active=True,
                health_status="unknown",
                tool_count=0,
                consecutive_failures=0,
                **{k: v for k, v in kwargs.items() if k in {"workspace_id", "user_id", "name"}},
                transport=kwargs["transport"],
                url=kwargs["url"],
            )

    class Db:
        def __init__(self) -> None:
            self.engine = MagicMock()

        def transaction(self) -> Any:
            from contextlib import asynccontextmanager

            @asynccontextmanager
            async def _tx() -> Any:
                yield MagicMock()

            return _tx()

    async def tenant() -> Any:
        return Db()

    client.app.dependency_overrides[get_tenant_database] = tenant  # type: ignore[attr-defined]
    monkeypatch.setattr(mcp_admin, "McpServerRepository", Repo)
    monkeypatch.setattr(mcp_admin, "AsyncSession", lambda conn: MagicMock())
    body = {"name": "docs", "transport": "http", "url": "https://mcp.example.com/mcp"}
    response = client.post("/api/v1/mcp/servers", json=body)
    assert response.status_code == 201
    assert stored["transport"] == "streamable_http"
    assert response.json()["transport"] == "streamable_http"


def test_an_unknown_transport_is_a_clean_422(client: TestClient) -> None:
    response = client.post("/api/v1/mcp/servers", json={"name": "x", "transport": "pigeon"})
    assert response.status_code == 422
    assert response.json()["error"] == "validation_error"
    assert "transport" in response.json()["fields"]
