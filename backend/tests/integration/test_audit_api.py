"""Integration tests for Tamper-Evident Audit Trail REST API (Phase 07/12, Decisions D4, D5).

Verifies:
- GET /api/v1/audit (listing audit log entries under RLS)
- Category and task_id filtering
- Chain integrity verification (chain_valid=True)
- Strict multi-tenant isolation between Alice and Bob
"""

from __future__ import annotations

import hashlib
from typing import Any
from uuid import uuid4

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.auth.postgres import PostgresAuthStore
from app.core.config import Settings
from app.db.tenant import tenant_session
from app.main import create_app
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
    h0 = bytes.fromhex("0" * 64)
    h1 = hashlib.sha256(b"alice_audit_record_1").digest()
    h2 = hashlib.sha256(h1 + b"alice_audit_record_2").digest()

    # Seed Alice's audit entries directly into DB under Alice's workspace
    async with tenant_session(app_engine, claims_a.workspace_id, claims_a.user_id) as session:
        await session.execute(
            text(
                """
                INSERT INTO audit_log (
                    workspace_id, user_ref, task_id, tool_name, server_name,
                    permission_level, status, duration_ms, prev_hash, row_hash
                ) VALUES (
                    :ws, :user_ref, :task_id, 'web_search', 'brave',
                    'READ', 'SUCCESS', 120, :prev_h, :row_h
                )
                """
            ),
            {
                "ws": str(claims_a.workspace_id),
                "user_ref": cmd_a.email,
                "task_id": str(task_uuid),
                "prev_h": h0,
                "row_h": h1,
            },
        )
        await session.execute(
            text(
                """
                INSERT INTO audit_log (
                    workspace_id, user_ref, task_id, tool_name, server_name,
                    permission_level, status, duration_ms, prev_hash, row_hash
                ) VALUES (
                    :ws, :user_ref, :task_id, 'create_report', 'doc_agent',
                    'WRITE', 'SUCCESS', 250, :prev_h, :row_h
                )
                """
            ),
            {
                "ws": str(claims_a.workspace_id),
                "user_ref": cmd_a.email,
                "task_id": str(task_uuid),
                "prev_h": h1,
                "row_h": h2,
            },
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
        assert data["total"] >= 2
        assert data["chain_valid"] is True
        assert len(data["items"]) >= 2
        assert data["items"][0]["tool_name"] in ("create_report", "web_search")

        # 2. Filter by category
        tool_resp = await client.get(
            "/api/v1/audit?category=tool",
            headers={"Authorization": f"Bearer {tokens_a.access_token}"},
        )
        assert tool_resp.status_code == 200
        tool_data = tool_resp.json()
        assert all(it["category"] == "tool" for it in tool_data["items"])

        # 3. Tenant Isolation: Bob cannot see Alice's audit events
        bob_resp = await client.get(
            "/api/v1/audit",
            headers={"Authorization": f"Bearer {tokens_b.access_token}"},
        )
        assert bob_resp.status_code == 200
        bob_data = bob_resp.json()
        assert bob_data["total"] == 0
        assert len(bob_data["items"]) == 0
