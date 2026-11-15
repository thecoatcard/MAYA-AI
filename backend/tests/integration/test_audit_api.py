"""Integration tests for Tamper-Evident Audit Trail REST API (Phase 07/12, Decisions D4, D5).

Verifies:
- GET /api/v1/audit (listing audit log entries under RLS)
- Category and task_id filtering
- Chain integrity verification computed from real rows (valid chain, then a forged row)
- Strict multi-tenant isolation between Alice and Bob
"""

from __future__ import annotations

from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.auth.postgres import PostgresAuthStore
from app.core.config import Settings
from app.db.tenant import tenant_session
from app.main import create_app
from app.services.audit_service import record_audit
from tests.support.auth import build_harness


@pytest.fixture
def test_settings(migrated_database: Any) -> Settings:
    from tests.support.settings import make_settings

    return make_settings(
        database_url=migrated_database.app_url.render_as_string(hide_password=False),
        jwt_signing_key="j" * 32,
        otp_hmac_key="o" * 32,
    )


@pytest.mark.asyncio
async def test_audit_api_listing_and_isolation(
    app_engine: Any,
    test_settings: Settings,
) -> None:
    harness = build_harness(PostgresAuthStore(app_engine))
    app = create_app(test_settings, auth_service=harness.service)
    app.state.auth_service = harness.service
    app.state.settings = test_settings
    from types import SimpleNamespace

    app.state.resources = SimpleNamespace(engine=app_engine, settings=test_settings)

    cmd_a = await harness.register_and_verify()
    tokens_a = await harness.login(cmd_a)
    claims_a = harness.tokens.decode_access(tokens_a.access_token)

    cmd_b = await harness.register_and_verify()
    tokens_b = await harness.login(cmd_b)

    task_uuid = uuid4()
    # Seed Alice's audit entries through the real hash-chained writer under Alice's workspace
    async with tenant_session(app_engine, claims_a.workspace_id, claims_a.user_id) as session:
        for tool, server, level, ms in (
            ("web_search", "maya-tools", "READ", 120),
            ("create_report", "doc_agent", "WRITE", 250),
        ):
            await record_audit(
                session,
                workspace_id=claims_a.workspace_id,
                user_ref=cmd_a.email,
                task_id=task_uuid,
                server_name=server,
                tool_name=tool,
                permission_level=level,
                status="success",
                duration_ms=ms,
            )
        await record_audit(
            session,
            workspace_id=claims_a.workspace_id,
            user_ref=cmd_a.email,
            action="approval.approved",
            status="denied",
        )
        await session.commit()

    async with AsyncClient(
        transport=ASGITransport(app=app),
        base_url="http://test",
    ) as client:
        # 1. Alice lists audit trail
        resp = await client.get(
            "/api/v1/audit",
            headers={"Authorization": f"Bearer {tokens_a.access_token}"},
        )
        assert resp.status_code == 200, resp.text
        data = resp.json()
        assert data["total"] == 3
        assert data["chain_valid"] is True
        assert data["chain_length"] == 3
        assert len(data["items"]) == 3
        assert data["items"][0]["tool_name"] == "approval.approved"  # newest first

        # 2. Filter by category
        tool_resp = await client.get(
            "/api/v1/audit?category=tool",
            headers={"Authorization": f"Bearer {tokens_a.access_token}"},
        )
        assert tool_resp.status_code == 200
        tool_data = tool_resp.json()
        assert all(it["category"] == "tool" for it in tool_data["items"])
        # Filtering happens before pagination: total and page agree even with a tiny limit
        assert tool_data["total"] == 2
        paged = await client.get(
            "/api/v1/audit?category=tool&limit=1",
            headers={"Authorization": f"Bearer {tokens_a.access_token}"},
        )
        assert paged.json()["total"] == 2
        assert len(paged.json()["items"]) == 1
        denied = await client.get(
            "/api/v1/audit?status=denied",
            headers={"Authorization": f"Bearer {tokens_a.access_token}"},
        )
        assert [i["category"] for i in denied.json()["items"]] == ["approval"]

        # 3. Tenant Isolation: Bob cannot see Alice's audit events
        bob_resp = await client.get(
            "/api/v1/audit",
            headers={"Authorization": f"Bearer {tokens_b.access_token}"},
        )
        assert bob_resp.status_code == 200
        bob_data = bob_resp.json()
        assert bob_data["total"] == 0
        assert len(bob_data["items"]) == 0

    # 4. A forged row (bad hash, inserted with the runtime role's INSERT grant) breaks the chain
    async with tenant_session(app_engine, claims_a.workspace_id, claims_a.user_id) as session:
        await session.execute(
            text(
                "INSERT INTO audit_log (workspace_id, user_ref, status, prev_hash, row_hash) "
                "VALUES (:ws, 'forger', 'success', :p, :r)"
            ),
            {"ws": claims_a.workspace_id, "p": bytes(32), "r": bytes([1]) * 32},
        )
        await session.commit()
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        forged = await client.get(
            "/api/v1/audit", headers={"Authorization": f"Bearer {tokens_a.access_token}"}
        )
    assert forged.json()["chain_valid"] is False
    assert forged.json()["chain_first_bad_id"] is not None
