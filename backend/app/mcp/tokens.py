"""Cryptographically signed approval tokens and atomic database consumption (Decision D5)."""

from __future__ import annotations

import hmac
import json
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any
from uuid import UUID, uuid4

import jwt
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncConnection

ALGORITHM = "HS256"
APPROVAL_ISSUER = "maya-api"
APPROVAL_AUDIENCE = "maya-approval-gate"
APPROVAL_MAX_TTL_SECONDS = 300  # 5 minutes maximum lifetime
CLOCK_SKEW_SECONDS = 5

CONSUME_APPROVAL_QUERY = text(
    "UPDATE approval_requests "
    "SET status = 'completed', consumed_at = clock_timestamp(), completed_at = clock_timestamp() "
    "WHERE id = :request_id "
    "  AND workspace_id = :workspace_id "
    "  AND status = 'approved' "
    "  AND expires_at > clock_timestamp() "
    "  AND payload_hash = :payload_hash "
    "RETURNING id"
)

CLAIM_APPROVAL_QUERY = text(
    "UPDATE approval_requests "
    "SET status = 'claimed', claimed_by = :worker_id, claimed_at = clock_timestamp() "
    "WHERE id = :request_id "
    "  AND workspace_id = :workspace_id "
    "  AND status = 'approved' "
    "  AND expires_at > clock_timestamp() "
    "  AND payload_hash = :payload_hash "
    "RETURNING id"
)

START_EXECUTION_QUERY = text(
    "UPDATE approval_requests "
    "SET status = 'executing', started_at = clock_timestamp() "
    "WHERE id = :request_id "
    "  AND workspace_id = :workspace_id "
    "  AND status = 'claimed' "
    "  AND claimed_by = :worker_id "
    "RETURNING id"
)

COMPLETE_EXECUTION_QUERY = text(
    "UPDATE approval_requests "
    "SET status = 'completed', "
    "    consumed_at = clock_timestamp(), "
    "    completed_at = clock_timestamp(), "
    "    execution_output = CAST(:output AS jsonb) "
    "WHERE id = :request_id "
    "  AND workspace_id = :workspace_id "
    "  AND status = 'executing' "
    "  AND claimed_by = :worker_id "
    "RETURNING id"
)

FAIL_EXECUTION_QUERY = text(
    "UPDATE approval_requests "
    "SET status = 'failed', "
    "    failed_at = clock_timestamp(), "
    "    failure_reason = :reason "
    "WHERE id = :request_id "
    "  AND workspace_id = :workspace_id "
    "  AND status IN ('claimed', 'executing') "
    "  AND claimed_by = :worker_id "
    "RETURNING id"
)

REVOKE_APPROVAL_QUERY = text(
    "UPDATE approval_requests "
    "SET status = 'revoked', "
    "    failure_reason = :reason "
    "WHERE id = :request_id "
    "  AND workspace_id = :workspace_id "
    "  AND status IN ('pending', 'approved') "
    "RETURNING id"
)

RECONCILE_ABANDONED_CLAIMS_QUERY = text(
    "UPDATE approval_requests "
    "SET status = 'failed', "
    "    failed_at = clock_timestamp(), "
    "    failure_reason = 'Worker crash recovery: execution lease timed out' "
    "WHERE status IN ('claimed', 'executing') "
    "  AND COALESCE(started_at, claimed_at) < "
    "      clock_timestamp() - make_interval(secs => :lease_timeout_seconds) "
    "RETURNING id, workspace_id, task_id"
)


class ApprovalTokenError(Exception):
    """Base exception for invalid or rejected approval tokens."""

    def __init__(self, message: str = "Invalid approval token") -> None:
        super().__init__(message)


class ApprovalTokenExpired(ApprovalTokenError):
    """Raised when an approval token has exceeded its 5-minute validity window."""

    def __init__(self) -> None:
        super().__init__("Approval token has expired")


class ApprovalTokenTampered(ApprovalTokenError):
    """Raised when signature verification fails or required claims are missing."""

    def __init__(self, message: str = "Approval token signature is invalid") -> None:
        super().__init__(message)


class ApprovalTokenMismatch(ApprovalTokenError):
    """Raised when token claims do not match the target execution context."""

    def __init__(self, field: str) -> None:
        super().__init__(f"Approval token {field} does not match execution context")


@dataclass(frozen=True, slots=True)
class ApprovalClaims:
    """Verified claims extracted from an approval token."""

    user_id: UUID
    workspace_id: UUID
    request_id: UUID
    tool_name: str
    payload_hash: str
    token_id: UUID
    expires_at: datetime


def _as_bytes(key: bytes | str) -> bytes:
    return key.encode("utf-8") if isinstance(key, str) else key


def mint_approval_token(
    key: bytes | str,
    *,
    user_id: UUID,
    workspace_id: UUID,
    request_id: UUID,
    tool_name: str,
    payload_hash: str,
    ttl_seconds: int = APPROVAL_MAX_TTL_SECONDS,
    now: datetime | None = None,
) -> tuple[str, UUID]:
    """Mint a cryptographically signed single-use JWT approval token (HS256).

    Token expiration is strictly capped at 5 minutes (300 seconds).
    Returns (signed_jwt_string, token_jti).
    """
    effective_ttl = min(max(1, ttl_seconds), APPROVAL_MAX_TTL_SECONDS)
    current_time = now if now is not None else datetime.now(UTC)
    iat = int(current_time.timestamp())
    exp = iat + effective_ttl
    token_jti = uuid4()

    claims = {
        "iss": APPROVAL_ISSUER,
        "aud": APPROVAL_AUDIENCE,
        "sub": str(user_id),
        "wid": str(workspace_id),
        "rid": str(request_id),
        "tool": tool_name,
        "hash": payload_hash,
        "jti": str(token_jti),
        "iat": iat,
        "exp": exp,
    }

    signed_token = jwt.encode(claims, _as_bytes(key), algorithm=ALGORITHM)
    return signed_token, token_jti


def verify_approval_token(
    token: str,
    key: bytes | str,
    *,
    expected_workspace_id: UUID,
    expected_user_id: UUID,
    expected_tool_name: str,
    expected_payload_hash: str,
    now: datetime | None = None,
) -> ApprovalClaims:
    """Verify an approval token's cryptographic integrity and parameter bindings."""
    current_time = now if now is not None else datetime.now(UTC)

    try:
        claims: dict[str, Any] = jwt.decode(
            token,
            _as_bytes(key),
            algorithms=[ALGORITHM],
            issuer=APPROVAL_ISSUER,
            audience=APPROVAL_AUDIENCE,
            options={
                "require": ["exp", "iat", "sub", "jti", "iss", "aud"],
                "verify_exp": False,
                "verify_iat": False,
            },
        )
    except jwt.InvalidTokenError as err:
        raise ApprovalTokenTampered(str(err)) from None

    try:
        user_id = UUID(claims["sub"])
        workspace_id = UUID(claims["wid"])
        request_id = UUID(claims["rid"])
        token_id = UUID(claims["jti"])
        tool_name = str(claims["tool"])
        payload_hash = str(claims["hash"])
        expires_at = datetime.fromtimestamp(claims["exp"], tz=UTC)
    except (KeyError, ValueError, TypeError) as err:
        raise ApprovalTokenTampered("Token missing required claim or format invalid") from err

    # Constant-time comparison for security claims
    if not hmac.compare_digest(str(workspace_id), str(expected_workspace_id)):
        raise ApprovalTokenMismatch("workspace_id")
    if not hmac.compare_digest(str(user_id), str(expected_user_id)):
        raise ApprovalTokenMismatch("user_id")
    if not hmac.compare_digest(tool_name, expected_tool_name):
        raise ApprovalTokenMismatch("tool_name")
    if not hmac.compare_digest(payload_hash, expected_payload_hash):
        raise ApprovalTokenMismatch("payload_hash")

    if expires_at <= current_time:
        raise ApprovalTokenExpired()

    return ApprovalClaims(
        user_id=user_id,
        workspace_id=workspace_id,
        request_id=request_id,
        tool_name=tool_name,
        payload_hash=payload_hash,
        token_id=token_id,
        expires_at=expires_at,
    )


async def consume_approval_atomically(
    connection: AsyncConnection,
    *,
    request_id: UUID,
    workspace_id: UUID,
    payload_hash: str,
) -> bool:
    """Atomically consume an approved request in PostgreSQL, preventing TOCTOU races."""
    result = await connection.execute(
        CONSUME_APPROVAL_QUERY,
        {
            "request_id": request_id,
            "workspace_id": workspace_id,
            "payload_hash": payload_hash,
        },
    )
    return result.first() is not None


async def claim_approval_atomically(
    connection: AsyncConnection,
    *,
    request_id: UUID,
    workspace_id: UUID,
    payload_hash: str,
    worker_id: str,
) -> bool:
    """Atomically claim an approved request for worker execution (prevents multi-worker races)."""
    result = await connection.execute(
        CLAIM_APPROVAL_QUERY,
        {
            "request_id": request_id,
            "workspace_id": workspace_id,
            "payload_hash": payload_hash,
            "worker_id": worker_id,
        },
    )
    return result.first() is not None


async def start_execution_atomically(
    connection: AsyncConnection,
    *,
    request_id: UUID,
    workspace_id: UUID,
    worker_id: str,
) -> bool:
    """Atomically transition a claimed approval request into EXECUTING state."""
    result = await connection.execute(
        START_EXECUTION_QUERY,
        {
            "request_id": request_id,
            "workspace_id": workspace_id,
            "worker_id": worker_id,
        },
    )
    return result.first() is not None


async def complete_execution_atomically(
    connection: AsyncConnection,
    *,
    request_id: UUID,
    workspace_id: UUID,
    worker_id: str,
    output: dict[str, Any] | list[Any] | None = None,
) -> bool:
    """Atomically record successful execution and transition to COMPLETED state."""
    result = await connection.execute(
        COMPLETE_EXECUTION_QUERY,
        {
            "request_id": request_id,
            "workspace_id": workspace_id,
            "worker_id": worker_id,
            "output": json.dumps(output or {}),
        },
    )
    return result.first() is not None


async def fail_execution_atomically(
    connection: AsyncConnection,
    *,
    request_id: UUID,
    workspace_id: UUID,
    worker_id: str,
    reason: str,
) -> bool:
    """Atomically transition a claimed or executing request into FAILED state."""
    result = await connection.execute(
        FAIL_EXECUTION_QUERY,
        {
            "request_id": request_id,
            "workspace_id": workspace_id,
            "worker_id": worker_id,
            "reason": reason[:500],
        },
    )
    return result.first() is not None


async def revoke_approval_atomically(
    connection: AsyncConnection,
    *,
    request_id: UUID,
    workspace_id: UUID,
    reason: str = "Revoked by human administrator",
) -> bool:
    """Atomically revoke a pending or approved request, preventing future execution."""
    result = await connection.execute(
        REVOKE_APPROVAL_QUERY,
        {
            "request_id": request_id,
            "workspace_id": workspace_id,
            "reason": reason[:500],
        },
    )
    return result.first() is not None


async def reconcile_abandoned_claims(
    connection: AsyncConnection,
    *,
    lease_timeout_seconds: int = 300,
) -> list[dict[str, Any]]:
    """Recover from worker crashes by detecting expired claims and failing them safely."""
    result = await connection.execute(
        RECONCILE_ABANDONED_CLAIMS_QUERY,
        {"lease_timeout_seconds": lease_timeout_seconds},
    )
    rows = result.fetchall()
    return [{"id": row[0], "workspace_id": row[1], "task_id": row[2]} for row in rows]
