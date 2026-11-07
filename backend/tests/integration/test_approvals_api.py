"""Integration tests for Approval Center API, channel isolation, and atomic consumption."""

from datetime import UTC, datetime, timedelta

import httpx
import pytest
from pydantic import SecretStr
from sqlalchemy.ext.asyncio import AsyncEngine

from app.auth.dependencies import TenantDatabase, get_principal, get_tenant_database
from app.auth.models import Principal
from app.core.config import Settings
from app.core.totp import compute_totp
from app.db.repositories.approvals import ApprovalRepository
from app.main import create_app
from app.mcp.approvals import compute_payload_hash
from app.mcp.tokens import consume_approval_atomically, verify_approval_token
from tests.support.postgres import scoped, seed_tenant

JWT_SECRET = "0" * 32
OTP_SECRET = "1" * 32


@pytest.fixture
def settings() -> Settings:
    return Settings(
        database_url=SecretStr("postgresql+asyncpg://maya_app:placeholder@localhost:5432/maya"),
        redis_url=SecretStr("redis://localhost:6379/0"),
        jwt_signing_key=SecretStr(JWT_SECRET),
        otp_hmac_key=SecretStr(OTP_SECRET),
    )


@pytest.mark.asyncio
async def test_approvals_api_end_to_end_workflow(
    app_engine: AsyncEngine,
    admin_engine: AsyncEngine,
    settings: Settings,
) -> None:
    now = datetime.now(UTC)
    tool_args = {"recipient": "boss@example.com", "subject": "Update", "body": "Report"}

    async with admin_engine.connect() as conn, conn.begin():
        tenant = await seed_tenant(conn, "appr_test")
        hash_val = compute_payload_hash(
            "gmail_send", tool_args, tenant.workspace_id, tenant.user_id, "idem_1"
        )
        repo = ApprovalRepository(conn, tenant.workspace_id, tenant.user_id)
        req = await repo.create_request(
            task_id=tenant.task_id,
            subtask_id=tenant.subtask_id,
            tool_name="gmail_send",
            tool_args=tool_args,
            payload_hash=hash_val,
            tier="APPROVAL_REQUIRED",
            action_description="Send external report email to boss",
            payload_diff={"type": "email", "recipient": "boss@example.com"},
            expires_at=now + timedelta(minutes=10),
        )
        req_id = req.id

        restr_args = {"table": "logs"}
        restr_hash = compute_payload_hash(
            "bulk_delete", restr_args, tenant.workspace_id, tenant.user_id, "idem_2"
        )
        restr_req = await repo.create_request(
            task_id=tenant.task_id,
            subtask_id=tenant.subtask_id,
            tool_name="bulk_delete",
            tool_args=restr_args,
            payload_hash=restr_hash,
            tier="RESTRICTED",
            action_description="Bulk delete application logs",
            payload_diff=None,
            expires_at=now + timedelta(minutes=10),
        )
        restr_id = restr_req.id

        exp_req = await repo.create_request(
            task_id=tenant.task_id,
            subtask_id=tenant.subtask_id,
            tool_name="gmail_send",
            tool_args=tool_args,
            payload_hash=hash_val,
            tier="APPROVAL_REQUIRED",
            action_description="Expired action",
            payload_diff=None,
            expires_at=now - timedelta(minutes=5),
        )
        exp_id = exp_req.id

    app = create_app(settings)
    app.state.settings = settings

    principal = Principal(
        user_id=tenant.user_id,
        workspace_id=tenant.workspace_id,
        role="member",
        session_id=tenant.session_id,
    )

    app.dependency_overrides[get_principal] = lambda: principal
    app.dependency_overrides[get_tenant_database] = lambda: TenantDatabase(
        app_engine, tenant.workspace_id
    )

    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app), base_url="http://test"
    ) as client:
        # 1. Test GET /api/v1/approvals
        res = await client.get("/api/v1/approvals")
        assert res.status_code == 200
        items = res.json()
        assert len(items) >= 2
        ids = [item["id"] for item in items]
        assert str(req_id) in ids
        assert str(restr_id) in ids

        # 2. Test GET /api/v1/approvals/{id}
        detail_res = await client.get(f"/api/v1/approvals/{req_id}")
        assert detail_res.status_code == 200
        detail = detail_res.json()
        assert detail["id"] == str(req_id)
        assert detail["tool_name"] == "gmail_send"
        assert detail["tier"] == "APPROVAL_REQUIRED"
        assert detail["status"] == "pending"

        # 3. Test Voice Channel Rejection (HTTP 403)
        voice_res = await client.post(
            f"/api/v1/approvals/{req_id}/decide",
            headers={"X-Maya-Channel": "voice"},
            json={"action": "approve"},
        )
        assert voice_res.status_code == 403
        assert voice_res.json()["error"] == "voice_channel_cannot_approve"

        # 4. Test Expired Request Rejection (HTTP 410)
        exp_res = await client.post(
            f"/api/v1/approvals/{exp_id}/decide",
            json={"action": "approve"},
        )
        assert exp_res.status_code == 410
        assert exp_res.json()["error"] == "approval_request_expired"

        # 5. Test Step-Up Auth on RESTRICTED tier
        # 5a. Missing TOTP code returns 401
        no_totp_res = await client.post(
            f"/api/v1/approvals/{restr_id}/decide",
            json={"action": "approve"},
        )
        assert no_totp_res.status_code == 401
        assert no_totp_res.json()["error"] == "totp_required"

        # 5b. Invalid TOTP code returns 401
        bad_totp_res = await client.post(
            f"/api/v1/approvals/{restr_id}/decide",
            json={"action": "approve", "totp_code": "000000"},
        )
        assert bad_totp_res.status_code == 401
        assert bad_totp_res.json()["error"] == "invalid_totp"

        # 5c. Valid TOTP code approves RESTRICTED tier
        import hashlib
        import hmac as py_hmac

        user_totp_secret = py_hmac.new(
            OTP_SECRET.encode("utf-8"), tenant.user_id.bytes, hashlib.sha256
        ).digest()
        valid_code = compute_totp(user_totp_secret)
        good_totp_res = await client.post(
            f"/api/v1/approvals/{restr_id}/decide",
            json={"action": "approve", "totp_code": valid_code},
        )
        assert good_totp_res.status_code == 200
        restr_approved = good_totp_res.json()
        assert restr_approved["status"] == "approved"
        assert restr_approved["approval_token"] is not None

        # 6. Approve APPROVAL_REQUIRED request
        approve_res = await client.post(
            f"/api/v1/approvals/{req_id}/decide",
            json={"action": "approve"},
        )
        assert approve_res.status_code == 200
        approved = approve_res.json()
        assert approved["status"] == "approved"
        token = approved["approval_token"]
        assert token is not None

        # 7. Cryptographic Token Verification
        verified_claims = verify_approval_token(
            token,
            JWT_SECRET,
            expected_workspace_id=tenant.workspace_id,
            expected_user_id=tenant.user_id,
            expected_tool_name="gmail_send",
            expected_payload_hash=hash_val,
        )
        assert verified_claims.tool_name == "gmail_send"
        assert verified_claims.payload_hash == hash_val
        assert verified_claims.user_id == tenant.user_id

        # 8. Atomic Database Consumption (TOCTOU Elimination)
        async with scoped(app_engine, tenant.workspace_id) as conn:
            consumed_first = await consume_approval_atomically(
                conn,
                request_id=req_id,
                workspace_id=tenant.workspace_id,
                payload_hash=hash_val,
            )
            assert consumed_first is True

            # Second attempt must return False (single-use invariant)
            consumed_second = await consume_approval_atomically(
                conn,
                request_id=req_id,
                workspace_id=tenant.workspace_id,
                payload_hash=hash_val,
            )
            assert consumed_second is False

            # Tampered hash must return False
            consumed_tampered = await consume_approval_atomically(
                conn,
                request_id=req_id,
                workspace_id=tenant.workspace_id,
                payload_hash="0" * 64,
            )
            assert consumed_tampered is False

        # 9. Revocation test on new pending request
        async with admin_engine.connect() as conn, conn.begin():
            repo = ApprovalRepository(conn, tenant.workspace_id, tenant.user_id)
            rev_req = await repo.create_request(
                task_id=tenant.task_id,
                subtask_id=tenant.subtask_id,
                tool_name="gmail_send",
                tool_args=tool_args,
                payload_hash=hash_val,
                tier="APPROVAL_REQUIRED",
                action_description="Request to be revoked",
                payload_diff=None,
                expires_at=now + timedelta(minutes=10),
            )
            rev_id = rev_req.id

        revoke_resp = await client.post(
            f"/api/v1/approvals/{rev_id}/decide",
            json={"action": "revoke", "rejection_reason": "Security cancellation"},
            headers={"X-Maya-Channel": "web"},
        )
        assert revoke_resp.status_code == 200
        assert revoke_resp.json()["status"] == "revoked"

        # Check detail endpoint reflects revoked status
        detail_rev = await client.get(f"/api/v1/approvals/{rev_id}")
        assert detail_rev.status_code == 200
        assert detail_rev.json()["status"] == "revoked"

        # Further decide attempts must be rejected with 409 Conflict
        decide_after_rev = await client.post(
            f"/api/v1/approvals/{rev_id}/decide",
            json={"action": "approve"},
            headers={"X-Maya-Channel": "web"},
        )
        assert decide_after_rev.status_code == 409
