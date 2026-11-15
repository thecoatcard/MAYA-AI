"""Comprehensive integration tests for Approval Center API and execution lifecycle.

Validates Decision D4 (Tool Governance) and Decision D5 (RFC 8785 Hashing & Single-Use Tokens).
"""

from __future__ import annotations

import uuid
from datetime import UTC, datetime, timedelta
from types import SimpleNamespace
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.api.v1.approvals import _derive_user_totp_secret
from app.auth.postgres import PostgresAuthStore
from app.core.config import Settings
from app.core.totp import compute_totp
from app.main import create_app
from app.mcp.approvals import compute_payload_hash
from app.mcp.tokens import verify_approval_token
from app.tasks.queue import SubtaskQueue
from tests.support.auth import build_harness
from tests.support.postgres import scoped


@pytest.fixture
def test_settings(migrated_database: Any) -> Settings:
    from tests.support.settings import make_settings

    return make_settings(
        database_url=migrated_database.app_url.render_as_string(hide_password=False),
        jwt_signing_key="j" * 32,
        otp_hmac_key="o" * 32,
    )


@pytest.mark.asyncio
async def test_approval_center_full_lifecycle(
    app_engine: Any,
    test_settings: Settings,
) -> None:
    """Test complete approval lifecycle: listing, channel enforcement, TOTP, and resumption."""
    harness = build_harness(PostgresAuthStore(app_engine))
    app = create_app(test_settings, auth_service=harness.service)
    app.state.auth_service = harness.service
    app.state.settings = test_settings
    subtask_queue = SubtaskQueue()
    app.state.resources = SimpleNamespace(
        engine=app_engine,
        settings=test_settings,
        subtask_queue=subtask_queue,
    )

    cmd = await harness.register_and_verify()
    token_pair = await harness.login(cmd)
    token = token_pair.access_token
    claims = harness.tokens.decode_access(token)
    workspace_id = claims.workspace_id
    user_id = claims.user_id
    session_id = claims.session_id
    task_id = uuid.uuid4()
    subtask_id = uuid.uuid4()

    jwt_key = test_settings.jwt_signing_key.get_secret_value()
    otp_key = test_settings.otp_hmac_key.get_secret_value()

    # 1. Seed tenant database rows
    async with scoped(app_engine, workspace_id) as conn:
        await conn.execute(
            text(
                "INSERT INTO sessions (id, workspace_id, user_id, title) "
                "VALUES (:s_id, :ws, :user, 'Test Session')"
            ),
            {"s_id": session_id, "ws": workspace_id, "user": user_id},
        )
        await conn.execute(
            text(
                "INSERT INTO tasks ("
                "  id, workspace_id, session_id, user_id, idempotency_key, title, user_instruction"
                ") VALUES (:t_id, :ws, :s_id, :user, :ikey, 'Task Title', 'Instruction')"
            ),
            {
                "t_id": task_id,
                "ws": workspace_id,
                "s_id": session_id,
                "user": user_id,
                "ikey": uuid.uuid4().hex,
            },
        )
        await conn.execute(
            text(
                "INSERT INTO subtasks ("
                "  id, workspace_id, task_id, assigned_agent, description, status, input_data"
                ") VALUES ("
                "  :st_id, :ws, :t_id, 'coding', 'Deploy code', 'awaiting_approval', "
                '  \'{"tool": "git_push"}\'::jsonb'
                ")"
            ),
            {"st_id": subtask_id, "ws": workspace_id, "t_id": task_id},
        )

        # Seed an APPROVAL_REQUIRED request
        req_id_approval = uuid.uuid4()
        tool_name = "git_push"
        tool_args = {"repo": "maya", "ref": "main"}
        payload_hash = compute_payload_hash(
            tool=tool_name,
            args=tool_args,
            workspace_id=workspace_id,
            user_id=user_id,
            idempotency_key="idemp-1",
        )
        await conn.execute(
            text(
                "INSERT INTO approval_requests ("
                "  id, workspace_id, user_id, task_id, subtask_id, tool_name, tool_args, "
                "  payload_hash, payload_diff, permission_level, action_description, "
                "  status, expires_at, idempotency_key"
                ") VALUES ("
                "  :id, :ws, :user, :task, :subtask, :tool, CAST(:args AS jsonb), "
                "  :hash, '{}'::jsonb, 'APPROVAL_REQUIRED', 'Push to main', 'pending', "
                "  :exp, 'idemp-1'"
                ")"
            ),
            {
                "id": req_id_approval,
                "ws": workspace_id,
                "user": user_id,
                "task": task_id,
                "subtask": subtask_id,
                "tool": tool_name,
                "args": '{"repo": "maya", "ref": "main"}',
                "hash": payload_hash,
                "exp": datetime.now(UTC) + timedelta(hours=1),
            },
        )

        # Seed a RESTRICTED tier request requiring TOTP step-up
        req_id_restricted = uuid.uuid4()
        restricted_hash = compute_payload_hash(
            tool="production_delete",
            args={},
            workspace_id=workspace_id,
            user_id=user_id,
            idempotency_key="idemp-2",
        )
        await conn.execute(
            text(
                "INSERT INTO approval_requests ("
                "  id, workspace_id, user_id, task_id, subtask_id, tool_name, tool_args, "
                "  payload_hash, payload_diff, permission_level, action_description, "
                "  status, expires_at, idempotency_key"
                ") VALUES ("
                "  :id, :ws, :user, :task, :subtask, 'production_delete', '{}'::jsonb, "
                "  :hash, '{}'::jsonb, 'RESTRICTED', 'Delete database', 'pending', "
                "  :exp, 'idemp-2'"
                ")"
            ),
            {
                "id": req_id_restricted,
                "ws": workspace_id,
                "user": user_id,
                "task": task_id,
                "subtask": subtask_id,
                "hash": restricted_hash,
                "exp": datetime.now(UTC) + timedelta(hours=1),
            },
        )

        await conn.commit()

    headers = {"Authorization": f"Bearer {token}"}

    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as client:
        # 1. List approvals
        res = await client.get("/api/v1/approvals", headers=headers)
        assert res.status_code == 200
        items = res.json()
        assert len(items) >= 2
        ids = {it["id"] for it in items}
        assert str(req_id_approval) in ids
        assert str(req_id_restricted) in ids

        # 2. Get single approval
        res = await client.get(f"/api/v1/approvals/{req_id_approval}", headers=headers)
        assert res.status_code == 200
        data = res.json()
        assert data["tool_name"] == "git_push"
        assert data["tier"] == "APPROVAL_REQUIRED"

        # 3. Channel Isolation: Voice channel cannot grant approval
        res = await client.post(
            f"/api/v1/approvals/{req_id_approval}/decide",
            headers={**headers, "X-Maya-Channel": "voice"},
            json={"action": "approve"},
        )
        assert res.status_code == 403
        assert res.json()["error"] == "voice_channel_cannot_approve"

        # 4. RESTRICTED tier requires TOTP
        res = await client.post(
            f"/api/v1/approvals/{req_id_restricted}/decide",
            headers=headers,
            json={"action": "approve"},
        )
        assert res.status_code == 401
        assert res.json()["error"] == "totp_required"

        # 5. RESTRICTED with valid TOTP succeeds
        user_totp_secret = _derive_user_totp_secret(otp_key, user_id)
        valid_totp = compute_totp(user_totp_secret)
        res = await client.post(
            f"/api/v1/approvals/{req_id_restricted}/decide",
            headers=headers,
            json={"action": "approve", "totp_code": valid_totp},
        )
        assert res.status_code == 200
        res_data = res.json()
        assert res_data["status"] == "approved"
        assert "approval_token" in res_data

        # 6. Approve APPROVAL_REQUIRED and verify subtask resumption
        res = await client.post(
            f"/api/v1/approvals/{req_id_approval}/decide",
            headers=headers,
            json={"action": "approve"},
        )
        assert res.status_code == 200
        appr_data = res.json()
        assert appr_data["status"] == "approved"
        minted_token = appr_data["approval_token"]

        # Verify token claims cryptographically
        approval_claims = verify_approval_token(
            minted_token,
            jwt_key,
            expected_workspace_id=workspace_id,
            expected_user_id=user_id,
            expected_tool_name=tool_name,
            expected_payload_hash=payload_hash,
        )
        assert approval_claims.request_id == req_id_approval

        # Verify subtask was updated with approval token
        async with scoped(app_engine, workspace_id) as conn:
            sub_res = await conn.execute(
                text("SELECT status, input_data FROM subtasks WHERE id = :id"),
                {"id": subtask_id},
            )
            sub_row = sub_res.one()
            assert sub_row.status == "pending"
            assert sub_row.input_data.get("approval_token") == minted_token
