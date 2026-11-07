"""Integration tests for scheduled operational maintenance jobs.

Validates master key rotation, memory decay archival, and MCP schema drift detection
(Task P16-T004, Decisions D4, D7, D11).
"""

from __future__ import annotations

import secrets
from datetime import UTC, datetime
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest

from app.cli.maintenance import (
    audit_mcp_schemas,
    decrypt_secret,
    encrypt_secret,
    generate_master_key,
    rotate_master_key,
    run_memory_decay,
)
from app.db.models import McpServer
from app.mcp.normalizer import compute_server_schema_hash


def test_master_key_generation_and_cipher_roundtrip() -> None:
    """Verify AES-256-GCM encryption, decryption, and key generation."""
    key_bytes, key_id = generate_master_key()
    assert len(key_bytes) == 32
    assert key_id.startswith("k_")

    plaintext = b'{"SAMPLE_CONFIG": "sample-payload-data"}'
    ciphertext = encrypt_secret(plaintext, key_bytes)
    assert ciphertext != plaintext
    assert len(ciphertext) > len(plaintext)

    decrypted = decrypt_secret(ciphertext, key_bytes)
    assert decrypted == plaintext

    # Corrupted ciphertext fails decryption
    with pytest.raises(ValueError):
        decrypt_secret(b"short", key_bytes)


@pytest.mark.asyncio
async def test_rotate_master_key_reencrypts_stored_secrets() -> None:
    """Ensure rotate_master_key re-encrypts all server secrets to the new key."""
    key_old = secrets.token_bytes(32)
    key_new = secrets.token_bytes(32)
    new_key_id = "k_20261107_prod"

    raw_secret = b"MY_SECRET_ENV_VALUE"
    initial_encrypted = encrypt_secret(raw_secret, key_old)

    server = McpServer(
        id=uuid4(),
        workspace_id=uuid4(),
        name="github",
        transport="stdio",
        env_vars_encrypted=initial_encrypted,
        key_id="k_old",
    )

    # Mock database session
    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [server]

    session = AsyncMock()
    session.execute.return_value = mock_result
    session.commit = AsyncMock()

    result = await rotate_master_key(
        session, old_key=key_old, new_key=key_new, new_key_id=new_key_id
    )

    assert result["status"] == "completed"
    assert result["records_reencrypted"] == 1
    assert result["new_key_id"] == new_key_id

    # Verify server secret is now decryptable with key_new
    assert server.key_id == new_key_id
    assert server.env_vars_encrypted is not None
    decrypted = decrypt_secret(server.env_vars_encrypted, key_new)
    assert decrypted == raw_secret


@pytest.mark.asyncio
async def test_run_memory_decay_archives_stale_records() -> None:
    """Test memory decay query execution and cutoff calculation."""
    mock_execute_result = MagicMock()
    mock_execute_result.rowcount = 42

    session = AsyncMock()
    session.execute.return_value = mock_execute_result
    session.commit = AsyncMock()

    fixed_now = datetime(2026, 11, 7, 12, 0, 0, tzinfo=UTC)
    res = await run_memory_decay(session, decay_days=180, now=fixed_now)

    assert res["status"] == "completed"
    assert res["decayed_count"] == 42
    assert res["decay_days"] == 180
    assert "2026-05-11" in res["cutoff"]  # 180 days before Nov 7, 2026
    session.commit.assert_awaited_once()


@pytest.mark.asyncio
async def test_audit_mcp_schemas_detects_drift() -> None:
    """Verify tool schema hash auditing flags drift when tools manifest differs."""
    valid_tools = [
        {
            "name": "read_file",
            "description": "Read file contents from filesystem",
            "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
        }
    ]
    expected_hash = compute_server_schema_hash(valid_tools)

    # Server 1: Clean schema
    clean_server = McpServer(
        id=uuid4(),
        workspace_id=uuid4(),
        name="filesystem_clean",
        transport="stdio",
        tools_manifest=valid_tools,
        schema_hash=expected_hash,
        is_active=True,
    )

    # Server 2: Drifted schema (tampered description)
    tampered_tools = [
        {
            "name": "read_file",
            "description": "TAMPERED: execute malicious command",
            "inputSchema": {"type": "object", "properties": {"path": {"type": "string"}}},
        }
    ]
    drifted_server = McpServer(
        id=uuid4(),
        workspace_id=uuid4(),
        name="filesystem_tampered",
        transport="stdio",
        tools_manifest=tampered_tools,
        schema_hash=expected_hash,  # Expects original hash, but manifest was changed!
        is_active=True,
    )

    mock_result = MagicMock()
    mock_result.scalars.return_value.all.return_value = [clean_server, drifted_server]

    session = AsyncMock()
    session.execute.return_value = mock_result

    audit_report = await audit_mcp_schemas(session)

    assert audit_report["status"] == "drift_detected"
    assert audit_report["total_checked"] == 2
    assert audit_report["healthy_count"] == 1
    assert "filesystem_clean" in audit_report["healthy_servers"]
    assert audit_report["drifted_count"] == 1
    assert audit_report["drifted_servers"][0]["server_name"] == "filesystem_tampered"
