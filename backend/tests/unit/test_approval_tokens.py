"""Unit tests for single-use approval token minting, verification, and expiry (Decision D5)."""

from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.mcp.tokens import (
    APPROVAL_MAX_TTL_SECONDS,
    ApprovalTokenExpired,
    ApprovalTokenMismatch,
    ApprovalTokenTampered,
    mint_approval_token,
    verify_approval_token,
)

SECRET = b"0" * 32


def test_mint_and_verify_valid_token() -> None:
    user_id = uuid4()
    workspace_id = uuid4()
    request_id = uuid4()
    tool_name = "gmail_send"
    payload_hash = "a" * 64
    now = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)

    token, jti = mint_approval_token(
        SECRET,
        user_id=user_id,
        workspace_id=workspace_id,
        request_id=request_id,
        tool_name=tool_name,
        payload_hash=payload_hash,
        ttl_seconds=300,
        now=now,
    )

    claims = verify_approval_token(
        token,
        SECRET,
        expected_workspace_id=workspace_id,
        expected_user_id=user_id,
        expected_tool_name=tool_name,
        expected_payload_hash=payload_hash,
        now=now + timedelta(seconds=60),
    )

    assert claims.user_id == user_id
    assert claims.workspace_id == workspace_id
    assert claims.request_id == request_id
    assert claims.tool_name == tool_name
    assert claims.payload_hash == payload_hash
    assert claims.token_id == jti
    assert claims.expires_at == now + timedelta(seconds=300)


def test_token_ttl_capped_at_five_minutes() -> None:
    now = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)
    token, _ = mint_approval_token(
        SECRET,
        user_id=uuid4(),
        workspace_id=uuid4(),
        request_id=uuid4(),
        tool_name="test_tool",
        payload_hash="b" * 64,
        ttl_seconds=3600,  # Caller asks for 1 hour
        now=now,
    )

    import jwt

    decoded = jwt.decode(
        token,
        SECRET,
        algorithms=["HS256"],
        audience="maya-approval-gate",
        options={"verify_exp": False},
    )
    assert decoded["exp"] - decoded["iat"] == APPROVAL_MAX_TTL_SECONDS


def test_expired_token_raises_expired() -> None:
    now = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)
    user_id = uuid4()
    workspace_id = uuid4()
    request_id = uuid4()
    tool_name = "test_tool"
    payload_hash = "c" * 64

    token, _ = mint_approval_token(
        SECRET,
        user_id=user_id,
        workspace_id=workspace_id,
        request_id=request_id,
        tool_name=tool_name,
        payload_hash=payload_hash,
        ttl_seconds=60,
        now=now,
    )

    with pytest.raises(ApprovalTokenExpired):
        verify_approval_token(
            token,
            SECRET,
            expected_workspace_id=workspace_id,
            expected_user_id=user_id,
            expected_tool_name=tool_name,
            expected_payload_hash=payload_hash,
            now=now + timedelta(seconds=70),
        )


def test_tampered_token_raises_tampered() -> None:
    now = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)
    token, _ = mint_approval_token(
        SECRET,
        user_id=uuid4(),
        workspace_id=uuid4(),
        request_id=uuid4(),
        tool_name="test_tool",
        payload_hash="d" * 64,
        now=now,
    )

    tampered = token[:-4] + "xxxx"
    with pytest.raises(ApprovalTokenTampered):
        verify_approval_token(
            tampered,
            SECRET,
            expected_workspace_id=uuid4(),
            expected_user_id=uuid4(),
            expected_tool_name="test_tool",
            expected_payload_hash="d" * 64,
            now=now,
        )

    # Wrong secret key (at least 32 bytes)
    with pytest.raises(ApprovalTokenTampered):
        verify_approval_token(
            token,
            b"wrong_secret_key_32_bytes_long!!",
            expected_workspace_id=uuid4(),
            expected_user_id=uuid4(),
            expected_tool_name="test_tool",
            expected_payload_hash="d" * 64,
            now=now,
        )


def test_claim_mismatch_raises_mismatch() -> None:
    now = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)
    user_id = uuid4()
    workspace_id = uuid4()
    request_id = uuid4()
    tool_name = "test_tool"
    payload_hash = "e" * 64

    token, _ = mint_approval_token(
        SECRET,
        user_id=user_id,
        workspace_id=workspace_id,
        request_id=request_id,
        tool_name=tool_name,
        payload_hash=payload_hash,
        now=now,
    )

    with pytest.raises(ApprovalTokenMismatch) as exc_info:
        verify_approval_token(
            token,
            SECRET,
            expected_workspace_id=uuid4(),  # Different workspace
            expected_user_id=user_id,
            expected_tool_name=tool_name,
            expected_payload_hash=payload_hash,
            now=now,
        )
    assert "workspace_id" in str(exc_info.value)

    with pytest.raises(ApprovalTokenMismatch) as exc_info:
        verify_approval_token(
            token,
            SECRET,
            expected_workspace_id=workspace_id,
            expected_user_id=uuid4(),  # Different user
            expected_tool_name=tool_name,
            expected_payload_hash=payload_hash,
            now=now,
        )
    assert "user_id" in str(exc_info.value)

    with pytest.raises(ApprovalTokenMismatch) as exc_info:
        verify_approval_token(
            token,
            SECRET,
            expected_workspace_id=workspace_id,
            expected_user_id=user_id,
            expected_tool_name="other_tool",
            expected_payload_hash=payload_hash,
            now=now,
        )
    assert "tool_name" in str(exc_info.value)

    with pytest.raises(ApprovalTokenMismatch) as exc_info:
        verify_approval_token(
            token,
            SECRET,
            expected_workspace_id=workspace_id,
            expected_user_id=user_id,
            expected_tool_name=tool_name,
            expected_payload_hash="f" * 64,  # Different payload hash
            now=now,
        )
    assert "payload_hash" in str(exc_info.value)


def test_jti_is_unique_per_mint() -> None:
    now = datetime(2026, 11, 6, 12, 0, 0, tzinfo=UTC)
    user_id = uuid4()
    workspace_id = uuid4()
    request_id = uuid4()

    token1, jti1 = mint_approval_token(
        SECRET,
        user_id=user_id,
        workspace_id=workspace_id,
        request_id=request_id,
        tool_name="tool",
        payload_hash="0" * 64,
        now=now,
    )
    token2, jti2 = mint_approval_token(
        SECRET,
        user_id=user_id,
        workspace_id=workspace_id,
        request_id=request_id,
        tool_name="tool",
        payload_hash="0" * 64,
        now=now,
    )

    assert jti1 != jti2
    assert token1 != token2


@pytest.mark.asyncio
async def test_claim_and_lifecycle_cas_queries() -> None:
    from unittest.mock import AsyncMock, MagicMock

    from app.mcp.tokens import (
        claim_approval_atomically,
        complete_execution_atomically,
        fail_execution_atomically,
        reconcile_abandoned_claims,
        revoke_approval_atomically,
        start_execution_atomically,
    )

    mock_conn = MagicMock()
    mock_result_ok = MagicMock()
    mock_result_ok.first.return_value = (uuid4(),)
    mock_result_ok.fetchall.return_value = [(uuid4(), uuid4(), uuid4())]

    mock_result_empty = MagicMock()
    mock_result_empty.first.return_value = None

    # 1. Successful claim
    mock_conn.execute = AsyncMock(return_value=mock_result_ok)
    req_id = uuid4()
    ws_id = uuid4()
    p_hash = "a" * 64
    worker_id = "worker-123"

    claimed = await claim_approval_atomically(
        mock_conn,
        request_id=req_id,
        workspace_id=ws_id,
        payload_hash=p_hash,
        worker_id=worker_id,
    )
    assert claimed is True

    # 2. Duplicate concurrent claim refused
    mock_conn.execute = AsyncMock(return_value=mock_result_empty)
    claimed_again = await claim_approval_atomically(
        mock_conn,
        request_id=req_id,
        workspace_id=ws_id,
        payload_hash=p_hash,
        worker_id="worker-456",
    )
    assert claimed_again is False

    # 3. Start execution
    mock_conn.execute = AsyncMock(return_value=mock_result_ok)
    started = await start_execution_atomically(
        mock_conn,
        request_id=req_id,
        workspace_id=ws_id,
        worker_id=worker_id,
    )
    assert started is True

    # 4. Complete execution
    mock_conn.execute = AsyncMock(return_value=mock_result_ok)
    completed = await complete_execution_atomically(
        mock_conn,
        request_id=req_id,
        workspace_id=ws_id,
        worker_id=worker_id,
        output={"status": "done"},
    )
    assert completed is True

    # 5. Fail execution
    mock_conn.execute = AsyncMock(return_value=mock_result_ok)
    failed = await fail_execution_atomically(
        mock_conn,
        request_id=req_id,
        workspace_id=ws_id,
        worker_id=worker_id,
        reason="Network timeout",
    )
    assert failed is True

    # 6. Revoke approval
    mock_conn.execute = AsyncMock(return_value=mock_result_ok)
    revoked = await revoke_approval_atomically(
        mock_conn,
        request_id=req_id,
        workspace_id=ws_id,
        reason="Revoked by admin",
    )
    assert revoked is True

    # 7. Reconcile abandoned claims
    mock_conn.execute = AsyncMock(return_value=mock_result_ok)
    reconciled = await reconcile_abandoned_claims(mock_conn, lease_timeout_seconds=300)
    assert len(reconciled) == 1
