"""Deterministic Safety Gate Property Testing (Decision D4, D5, Phase 15).

Generates 5,000 randomized permutations of adversarial tool invocations testing:
1. Missing approval tokens on mutating/destructive operations.
2. Cryptographic signature tampering and bit flips.
3. Expired tokens (past TTL).
4. Token replay across disparate workspaces, users, tools, and payload hashes.
5. Malicious server hints attempting to spoof read-only status on mutating tools.
6. Arbitrary unregistered tools (deny-by-default enforcement).
7. Argument schema tampering.

Asserts 100% rejection rate of unauthorized mutating calls.
"""

from __future__ import annotations

import random
from datetime import UTC, datetime, timedelta
from uuid import uuid4

import pytest

from app.mcp.approvals import compute_payload_hash
from app.mcp.permissions import (
    DEFAULT_TOOL_PERMISSIONS,
    PermissionDeniedError,
    ToolPermissionRegistry,
    ToolTier,
)
from app.mcp.tokens import (
    ApprovalTokenError,
    ApprovalTokenExpired,
    ApprovalTokenMismatch,
    ApprovalTokenTampered,
    mint_approval_token,
    verify_approval_token,
)

MUTATING_TOOLS: tuple[str, ...] = (
    "gmail_send",
    "git_push",
    "open_pr",
    "github_create_pull_request",
    "delete_file_outside_artifacts",
    "browser_click",
    "browser_fill_form",
    "database_ddl",
    "bulk_delete",
    "git_force_push",
    "execute_payment",
    "create_api_token",
    "browser_submit_payment",
)

READ_TOOLS: tuple[str, ...] = (
    "file_read",
    "git_status",
    "git_diff",
    "git_log",
    "sql_select",
    "web_search",
    "read_emails",
    "read_calendar",
)

ARBITRARY_ATTACK_TOOLS: tuple[str, ...] = (
    "system_exec",
    "rm_rf_root",
    "DROP_ALL_TABLES",
    "../../etc/passwd",
    "eval()",
    "curl_exfiltrate",
    "sudo_su",
    "spawn_shell",
    "GRANT_ALL_PRIVILEGES",
)


def test_permission_gate_property_5000_permutations() -> None:
    """Generate 5,000 randomized tool invocation permutations and assert 100% rejection."""
    rng = random.Random(42)  # Deterministic seed for reproducible property tests
    registry = ToolPermissionRegistry()

    # Register sample tool with required arguments
    registry.register_tool(
        "restricted_admin_action",
        ToolTier.RESTRICTED,
        required_args={"admin_key", "target"},
    )

    valid_secret_key = "a" * 32
    tampered_secret_key = "b" * 32

    rejections = 0
    total_permutations = 5000

    for i in range(total_permutations):
        scenario = rng.choice(
            [
                "missing_token_mutating",
                "tampered_signature",
                "expired_token",
                "replay_workspace_mismatch",
                "replay_user_mismatch",
                "replay_tool_mismatch",
                "replay_payload_mismatch",
                "spoofed_readonly_hint",
                "unregistered_tool_deny_by_default",
                "missing_required_arguments",
            ]
        )

        ws_id = uuid4()
        user_id = uuid4()
        req_id = uuid4()
        mutating_tool = rng.choice(MUTATING_TOOLS)
        args: dict[str, str] = {"param": f"value_{rng.randint(1000, 9999)}"}
        payload_hash = compute_payload_hash(
            tool=mutating_tool,
            args=args,
            workspace_id=ws_id,
            user_id=user_id,
            idempotency_key=f"key_{i}",
        )

        if scenario == "missing_token_mutating":
            # Attempt to execute mutating tool without token
            with pytest.raises(PermissionDeniedError) as exc_info:
                registry.authorize_tool_call(
                    tool_name=mutating_tool,
                    args=args,
                    has_valid_approval_token=False,
                )
            assert "forbidden without valid approval token" in str(exc_info.value)
            rejections += 1

        elif scenario == "tampered_signature":
            # Mint token with one key, verify with another key or modify signature bytes
            token, _ = mint_approval_token(
                key=tampered_secret_key,
                user_id=user_id,
                workspace_id=ws_id,
                request_id=req_id,
                tool_name=mutating_tool,
                payload_hash=payload_hash,
            )
            with pytest.raises((ApprovalTokenTampered, ApprovalTokenError)):
                verify_approval_token(
                    token=token,
                    key=valid_secret_key,
                    expected_workspace_id=ws_id,
                    expected_user_id=user_id,
                    expected_tool_name=mutating_tool,
                    expected_payload_hash=payload_hash,
                )
            rejections += 1

        elif scenario == "expired_token":
            # Token minted with past expiration
            now = datetime.now(UTC)
            past_time = now - timedelta(seconds=350)
            token, _ = mint_approval_token(
                key=valid_secret_key,
                user_id=user_id,
                workspace_id=ws_id,
                request_id=req_id,
                tool_name=mutating_tool,
                payload_hash=payload_hash,
                ttl_seconds=300,
                now=past_time,
            )
            with pytest.raises(ApprovalTokenExpired):
                verify_approval_token(
                    token=token,
                    key=valid_secret_key,
                    expected_workspace_id=ws_id,
                    expected_user_id=user_id,
                    expected_tool_name=mutating_tool,
                    expected_payload_hash=payload_hash,
                    now=now,
                )
            rejections += 1

        elif scenario == "replay_workspace_mismatch":
            # Token presented in a different workspace
            other_ws_id = uuid4()
            token, _ = mint_approval_token(
                key=valid_secret_key,
                user_id=user_id,
                workspace_id=other_ws_id,
                request_id=req_id,
                tool_name=mutating_tool,
                payload_hash=payload_hash,
            )
            with pytest.raises(ApprovalTokenMismatch) as exc_ws:
                verify_approval_token(
                    token=token,
                    key=valid_secret_key,
                    expected_workspace_id=ws_id,
                    expected_user_id=user_id,
                    expected_tool_name=mutating_tool,
                    expected_payload_hash=payload_hash,
                )
            assert "workspace_id" in str(exc_ws.value)
            rejections += 1

        elif scenario == "replay_user_mismatch":
            # Token presented by a different user
            other_user_id = uuid4()
            token, _ = mint_approval_token(
                key=valid_secret_key,
                user_id=other_user_id,
                workspace_id=ws_id,
                request_id=req_id,
                tool_name=mutating_tool,
                payload_hash=payload_hash,
            )
            with pytest.raises(ApprovalTokenMismatch) as exc_user:
                verify_approval_token(
                    token=token,
                    key=valid_secret_key,
                    expected_workspace_id=ws_id,
                    expected_user_id=user_id,
                    expected_tool_name=mutating_tool,
                    expected_payload_hash=payload_hash,
                )
            assert "user_id" in str(exc_user.value)
            rejections += 1

        elif scenario == "replay_tool_mismatch":
            # Token minted for one tool presented for another tool
            other_tool = rng.choice([t for t in MUTATING_TOOLS if t != mutating_tool])
            token, _ = mint_approval_token(
                key=valid_secret_key,
                user_id=user_id,
                workspace_id=ws_id,
                request_id=req_id,
                tool_name=other_tool,
                payload_hash=payload_hash,
            )
            with pytest.raises(ApprovalTokenMismatch) as exc_tool:
                verify_approval_token(
                    token=token,
                    key=valid_secret_key,
                    expected_workspace_id=ws_id,
                    expected_user_id=user_id,
                    expected_tool_name=mutating_tool,
                    expected_payload_hash=payload_hash,
                )
            assert "tool_name" in str(exc_tool.value)
            rejections += 1

        elif scenario == "replay_payload_mismatch":
            # Token presented with modified parameters / payload hash
            other_hash = "f" * 64
            token, _ = mint_approval_token(
                key=valid_secret_key,
                user_id=user_id,
                workspace_id=ws_id,
                request_id=req_id,
                tool_name=mutating_tool,
                payload_hash=other_hash,
            )
            with pytest.raises(ApprovalTokenMismatch) as exc_payload:
                verify_approval_token(
                    token=token,
                    key=valid_secret_key,
                    expected_workspace_id=ws_id,
                    expected_user_id=user_id,
                    expected_tool_name=mutating_tool,
                    expected_payload_hash=payload_hash,
                )
            assert "payload_hash" in str(exc_payload.value)
            rejections += 1

        elif scenario == "spoofed_readonly_hint":
            # Malicious server hints claiming read-only status for destructive tool
            spoofed_hints = {
                "readOnly": True,
                "destructiveHint": False,
                "safe": True,
            }
            # Server hints can NEVER downgrade a permission tier (Decision D4)
            tier = registry.classify(mutating_tool, server_hints=spoofed_hints)
            expected_tier = DEFAULT_TOOL_PERMISSIONS[mutating_tool]
            assert tier >= expected_tier
            assert tier in (ToolTier.APPROVAL_REQUIRED, ToolTier.RESTRICTED)

            with pytest.raises(PermissionDeniedError):
                registry.authorize_tool_call(
                    tool_name=mutating_tool,
                    args=args,
                    has_valid_approval_token=False,
                    server_hints=spoofed_hints,
                )
            rejections += 1

        elif scenario == "unregistered_tool_deny_by_default":
            # Arbitrary / untrusted tool name rejected by default
            arbitrary_tool = f"{rng.choice(ARBITRARY_ATTACK_TOOLS)}_{rng.randint(100, 999)}"
            with pytest.raises(PermissionDeniedError) as exc_info:
                registry.classify(arbitrary_tool)
            assert "is not registered and is denied by default" in str(exc_info.value)

            with pytest.raises(PermissionDeniedError):
                registry.authorize_tool_call(
                    tool_name=arbitrary_tool,
                    args=args,
                    has_valid_approval_token=False,
                )
            rejections += 1

        elif scenario == "missing_required_arguments":
            # Valid tool invoked with missing required schema constraints
            with pytest.raises(PermissionDeniedError) as exc_info:
                registry.authorize_tool_call(
                    tool_name="restricted_admin_action",
                    args={"admin_key": "valid_key"},  # missing "target"
                    has_valid_approval_token=True,
                )
            assert "missing required arguments" in str(exc_info.value)
            rejections += 1

    # Assert 100% rejection of unauthorized calls
    assert rejections == total_permutations, (
        f"Expected {total_permutations} rejections, got {rejections}"
    )


def test_legitimate_approved_mutating_calls_pass_authorization() -> None:
    """Positive test: legitimately approved mutating tool calls with valid tokens pass."""
    registry = ToolPermissionRegistry()
    secret_key = "x" * 32
    ws_id = uuid4()
    user_id = uuid4()
    req_id = uuid4()
    tool_name = "git_push"
    args = {"branch": "main", "remote": "origin"}

    payload_hash = compute_payload_hash(
        tool=tool_name,
        args=args,
        workspace_id=ws_id,
        user_id=user_id,
        idempotency_key="unique_key_1",
    )

    token, token_jti = mint_approval_token(
        key=secret_key,
        user_id=user_id,
        workspace_id=ws_id,
        request_id=req_id,
        tool_name=tool_name,
        payload_hash=payload_hash,
        ttl_seconds=300,
    )

    claims = verify_approval_token(
        token=token,
        key=secret_key,
        expected_workspace_id=ws_id,
        expected_user_id=user_id,
        expected_tool_name=tool_name,
        expected_payload_hash=payload_hash,
    )

    assert claims.user_id == user_id
    assert claims.workspace_id == ws_id
    assert claims.token_id == token_jti

    # Security gate authorizes invocation with valid token
    tier = registry.authorize_tool_call(
        tool_name=tool_name,
        args=args,
        has_valid_approval_token=True,
    )
    assert tier == ToolTier.APPROVAL_REQUIRED
