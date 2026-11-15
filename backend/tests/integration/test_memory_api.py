"""Integration tests for Memory REST API endpoints.

Verifies:
- POST /api/v1/memory/items (ingestion, PII scrubbing, 768-dim embeddings)
- GET /api/v1/memory/items (listing, category filtering, pagination)
- POST /api/v1/memory/search (hybrid retrieval with Defect #16 threshold and decay)
- DELETE /api/v1/memory/items/{id} (single item deletion)
- DELETE /api/v1/memory/purge (GDPR Article 17 erasure)
- Strict multi-tenant RLS isolation between Alice and Bob
"""

from __future__ import annotations

import math
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest
from httpx import ASGITransport, AsyncClient

from app.auth.postgres import PostgresAuthStore
from app.core.config import Settings
from app.gateway.schemas import EmbeddingResponse, TokenUsage
from app.main import create_app
from tests.support.auth import build_harness


def _make_vector(dim: int = 768) -> list[float]:
    raw = [0.1] * dim
    norm = math.sqrt(sum(x * x for x in raw))
    return [x / norm for x in raw]


@pytest.fixture
def test_settings(migrated_database: Any) -> Settings:
    from tests.support.settings import make_settings

    return make_settings(
        database_url=migrated_database.app_url.render_as_string(hide_password=False),
        jwt_signing_key="j" * 32,
        otp_hmac_key="o" * 32,
    )


@pytest.mark.asyncio
async def test_memory_api_full_flow(
    app_engine: Any,
    test_settings: Settings,
) -> None:
    # 1. Setup auth harness and app
    harness = build_harness(PostgresAuthStore(app_engine))
    app = create_app(test_settings, auth_service=harness.service)
    app.state.auth_service = harness.service
    app.state.settings = test_settings

    # Mock gateway returning 768-dim vector
    mock_gateway = MagicMock()
    vec = _make_vector(768)

    async def fake_embed_content(alias: str, contents: Any, **kwargs: Any) -> EmbeddingResponse:
        count = len(contents) if isinstance(contents, list) else 1
        return EmbeddingResponse(
            vectors=[vec for _ in range(count)],
            model_id="gemini-embedding-2",
            usage=TokenUsage(prompt_tokens=10, candidates_tokens=0, total_token_count=10),
            alias_used="embed",
            was_fallback=False,
        )

    mock_gateway.embed_content = AsyncMock(side_effect=fake_embed_content)

    mock_redis = MagicMock()
    mock_redis.rpush = AsyncMock(return_value=1)
    mock_redis.lrange = AsyncMock(return_value=[])
    mock_redis.expire = AsyncMock(return_value=True)
    mock_redis.delete = AsyncMock(return_value=1)

    app.state.resources = SimpleNamespace(
        engine=app_engine,
        settings=test_settings,
        model_gateway=mock_gateway,
        redis=mock_redis,
    )

    # 2. Register Tenant A (Alice) and Tenant B (Bob)
    cmd_a = await harness.register_and_verify()
    tokens_a = await harness.login(cmd_a)
    headers_a = {"Authorization": f"Bearer {tokens_a.access_token}"}

    cmd_b = await harness.register_and_verify()
    tokens_b = await harness.login(cmd_b)
    headers_b = {"Authorization": f"Bearer {tokens_b.access_token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 3. Alice ingests memory item with PII
        create_payload = {
            "category": "knowledge",
            "content": "Secret API doc with Visa 4532-0151-1283-0366 and AWS AKIAIOSFODNN7EXAMPLE.",
            "metadata": {"doc_id": "guide-01"},
            "permanent": True,
        }
        res_create = await client.post(
            "/api/v1/memory/items", json=create_payload, headers=headers_a
        )
        assert res_create.status_code == 201
        created_items = res_create.json()
        assert len(created_items) >= 1
        alice_item = created_items[0]
        alice_item_id = alice_item["id"]

        assert "[REDACTED_CREDIT_CARD]" in alice_item["content"]
        assert "[REDACTED_SECRET]" in alice_item["content"]
        assert "4532-0151-1283-0366" not in alice_item["content"]
        assert "AKIAIOSFODNN7EXAMPLE" not in alice_item["content"]
        assert alice_item["category"] == "knowledge"
        assert alice_item["permanent"] is True

        # 4. Alice lists items
        res_list = await client.get("/api/v1/memory/items?category=knowledge", headers=headers_a)
        assert res_list.status_code == 200
        alice_list = res_list.json()
        assert any(it["id"] == alice_item_id for it in alice_list)

        # 5. Bob lists items - must be empty (RLS isolation)
        res_bob_list = await client.get(
            "/api/v1/memory/items?category=knowledge", headers=headers_b
        )
        assert res_bob_list.status_code == 200
        assert len(res_bob_list.json()) == 0

        # 6. Alice searches memory
        search_payload = {
            "query": "Secret API doc guide",
            "categories": ["knowledge"],
            "limit": 5,
            "raw_threshold": 0.78,
        }
        res_search = await client.post(
            "/api/v1/memory/search", json=search_payload, headers=headers_a
        )
        assert res_search.status_code == 200
        search_data = res_search.json()
        assert search_data["total_results"] >= 1
        assert any(r["id"] == alice_item_id for r in search_data["results"])

        # 7. Bob searches same query - must return 0 results
        res_bob_search = await client.post(
            "/api/v1/memory/search", json=search_payload, headers=headers_b
        )
        assert res_bob_search.status_code == 200
        assert res_bob_search.json()["total_results"] == 0

        # 8. Bob attempts to delete Alice's item - 404 Not Found
        res_bad_del = await client.delete(
            f"/api/v1/memory/items/{alice_item_id}", headers=headers_b
        )
        assert res_bad_del.status_code == 404

        # 9. Alice deletes single item
        res_del = await client.delete(f"/api/v1/memory/items/{alice_item_id}", headers=headers_a)
        assert res_del.status_code == 200
        assert res_del.json()["deleted"] is True

        # 10. Alice purges memories (GDPR Article 17)
        res_purge = await client.request(
            "DELETE", "/api/v1/memory/purge", json={"scope": "all"}, headers=headers_a
        )
        assert res_purge.status_code == 200
        assert res_purge.json()["status"] == "purged"
