"""Scheduled Operational Maintenance Jobs (Task P16-T004).

Implements:
1. rotate_master_key: Generates new AES-256-GCM key and re-encrypts stored secrets.
2. run_memory_decay: Archives non-permanent memory items unaccessed for > 180 days.
3. audit_mcp_schemas: Detects tool description and parameter schema drift across MCP servers.
"""

from __future__ import annotations

import argparse
import asyncio
import logging
import secrets
from datetime import UTC, datetime, timedelta
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import Settings
from app.core.resources import Resources
from app.db.models import McpServer, MemoryItem
from app.mcp.normalizer import compute_server_schema_hash

logger = logging.getLogger("maya.maintenance")


def generate_master_key() -> tuple[bytes, str]:
    """Generate a high-entropy 256-bit AES-GCM key and formatted key identifier."""
    key_bytes = secrets.token_bytes(32)
    key_id = f"k_{datetime.now(UTC).strftime('%Y%m%d')}_{secrets.token_hex(4)}"
    return key_bytes, key_id


def encrypt_secret(plaintext: bytes, key: bytes) -> bytes:
    """Encrypt secret data with AES-256-GCM using a fresh 12-byte nonce."""
    aes = AESGCM(key)
    nonce = secrets.token_bytes(12)
    ciphertext = aes.encrypt(nonce, plaintext, None)
    return nonce + ciphertext


def decrypt_secret(encrypted_data: bytes, key: bytes) -> bytes:
    """Decrypt AES-256-GCM ciphertext unpackaging the 12-byte nonce."""
    if len(encrypted_data) < 28:
        raise ValueError("Encrypted payload too short for AES-256-GCM")
    nonce = encrypted_data[:12]
    ciphertext = encrypted_data[12:]
    aes = AESGCM(key)
    return aes.decrypt(nonce, ciphertext, None)


async def rotate_master_key(
    session: AsyncSession,
    old_key: bytes,
    new_key: bytes,
    new_key_id: str | None = None,
) -> dict[str, Any]:
    """Rotate AES-256-GCM master key and re-encrypt stored secrets without downtime.

    Iterates through all registered MCP servers with encrypted environment variables,
    decrypts with old_key, re-encrypts with new_key, and updates active key_id.
    """
    key_identifier = new_key_id or f"k_{datetime.now(UTC).strftime('%Y%m%d')}"
    logger.info("Starting master key rotation to new key_id: %s", key_identifier)

    stmt = select(McpServer).where(McpServer.env_vars_encrypted.is_not(None))
    result = await session.execute(stmt)
    servers = list(result.scalars().all())

    reencrypted_count = 0
    for server in servers:
        if server.env_vars_encrypted is None:
            continue
        try:
            decrypted = decrypt_secret(server.env_vars_encrypted, old_key)
            reencrypted = encrypt_secret(decrypted, new_key)
            server.env_vars_encrypted = reencrypted
            server.key_id = key_identifier
            reencrypted_count += 1
        except Exception as exc:
            logger.error("Failed to re-encrypt server %s: %s", server.name, exc)
            raise RuntimeError(f"Key rotation failed on server {server.name}: {exc}") from exc

    await session.commit()
    logger.info("Master key rotation complete. %d records re-encrypted.", reencrypted_count)
    return {
        "status": "completed",
        "job": "rotate_master_key",
        "records_reencrypted": reencrypted_count,
        "new_key_id": key_identifier,
    }


async def run_memory_decay(
    session: AsyncSession,
    decay_days: int = 180,
    *,
    now: datetime | None = None,
) -> dict[str, Any]:
    """Archive stale episodic and knowledge memories unaccessed for > 180 days (Decision D7).

    Executes:
    UPDATE memory_items SET expires_at = :now
    WHERE permanent = false AND expires_at IS NULL AND last_accessed_at < :cutoff
    """
    reference_now = now or datetime.now(UTC)
    cutoff = reference_now - timedelta(days=decay_days)
    logger.info("Running memory decay job with cutoff threshold: %s", cutoff.isoformat())

    stmt = (
        update(MemoryItem)
        .where(
            MemoryItem.permanent.is_(False),
            MemoryItem.expires_at.is_(None),
            MemoryItem.last_accessed_at < cutoff,
        )
        .values(expires_at=reference_now)
    )

    result = await session.execute(stmt)
    await session.commit()
    decayed_count = int(getattr(result, "rowcount", 0) or 0)

    logger.info("Memory decay completed. %d stale items marked expired.", decayed_count)
    return {
        "status": "completed",
        "job": "run_memory_decay",
        "decayed_count": decayed_count,
        "decay_days": decay_days,
        "cutoff": cutoff.isoformat(),
    }


async def audit_mcp_schemas(session: AsyncSession) -> dict[str, Any]:
    """Detect tool parameter and description poisoning across all registered MCP servers.

    Recomputes the canonical SHA-256 schema hash across each server's tools_manifest
    and compares it to the pinned schema_hash. Alerts on any detected drift.
    """
    logger.info("Starting MCP schema hash drift audit.")
    stmt = select(McpServer).where(McpServer.is_active.is_(True))
    result = await session.execute(stmt)
    servers = list(result.scalars().all())

    healthy: list[str] = []
    drifted: list[dict[str, Any]] = []

    for server in servers:
        server_tools = server.tools_manifest or []
        observed_hash = compute_server_schema_hash(server_tools)
        expected_hash = server.schema_hash

        if expected_hash and observed_hash.lower() != expected_hash.lower():
            logger.warning(
                "SCHEMA DRIFT DETECTED: Server '%s' expected hash '%s', but computed '%s'",
                server.name,
                expected_hash,
                observed_hash,
            )
            drifted.append(
                {
                    "server_id": str(server.id),
                    "server_name": server.name,
                    "expected_hash": expected_hash,
                    "observed_hash": observed_hash,
                }
            )
        else:
            healthy.append(server.name)

    logger.info(
        "MCP schema audit complete. Checked %d servers: %d healthy, %d drifted.",
        len(servers),
        len(healthy),
        len(drifted),
    )
    return {
        "status": "completed" if not drifted else "drift_detected",
        "job": "audit_mcp_schemas",
        "total_checked": len(servers),
        "healthy_count": len(healthy),
        "drifted_count": len(drifted),
        "healthy_servers": healthy,
        "drifted_servers": drifted,
    }


async def _run_cli(job: str) -> None:
    settings = Settings()
    resources = Resources(settings)
    async with resources.session_factory() as session:
        if job in ("decay", "all"):
            res = await run_memory_decay(session)
            print(f"Memory decay result: {res}")
        if job in ("audit", "all"):
            res = await audit_mcp_schemas(session)
            print(f"Schema audit result: {res}")
    await resources.close()


def main() -> None:
    parser = argparse.ArgumentParser(description="MAYA AI Operational Maintenance CLI")
    parser.add_argument(
        "--job",
        choices=["decay", "audit", "all"],
        default="all",
        help="Maintenance job to execute",
    )
    args = parser.parse_args()
    asyncio.run(_run_cli(args.job))


if __name__ == "__main__":
    main()
