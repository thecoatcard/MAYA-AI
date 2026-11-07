# Module: app.cli

- **Status:** Implemented
- **Owner:** Platform SRE & DevOps Team
- **Location:** `backend/app/cli`

## Responsibility

This module provides operational command-line interfaces and background maintenance runners for the MAYA AI platform. It encapsulates batch maintenance routines including master key rotation (`rotate_master_key`), long-term memory decay and archival (`run_memory_decay`), and MCP tool schema tampering auditing (`audit_mcp_schemas`). It is not intended for serving client HTTP or WebSocket requests.

## Interfaces

### Maintenance Functions (`app.cli.maintenance`)

- `rotate_master_key(session: AsyncSession, old_key: bytes, new_key: bytes, new_key_id: str | None = None) -> dict[str, Any]`
  Re-encrypts stored environment secrets across all registered `McpServer` records using AES-256-GCM.
- `run_memory_decay(session: AsyncSession, decay_days: int = 180, *, now: datetime | None = None) -> dict[str, Any]`
  Marks non-permanent memory items unaccessed for > 180 days with `expires_at = now()`.
- `audit_mcp_schemas(session: AsyncSession) -> dict[str, Any]`
  Recomputes canonical SHA-256 schema hashes across all registered active MCP tool servers and detects supply-chain description poisoning.
- `main() -> None`
  CLI argument parser entrypoint supporting `--job decay|audit|all`.

```bash
python -m app.cli.maintenance --job decay
```

## Dependencies

- Depends on: `app.core.config`, `app.core.resources`, `app.db.models`, `app.mcp.normalizer`, `cryptography`, `sqlalchemy`.
- Depended on by: Scheduled CronJobs, operational runbooks, and SRE runners.

## Configuration

- `MAYA_DATABASE_URL`: Connection string for PostgreSQL database.
- See [`docs/guides/CONFIGURATION.md`](../../../docs/guides/CONFIGURATION.md) for full configuration reference.

## Data

- Reads and updates: `mcp_servers` (`env_vars_encrypted`, `key_id`, `schema_hash`, `tools_manifest`).
- Reads and updates: `memory_items` (`expires_at`, `permanent`, `last_accessed_at`).

## Failure behavior

- Master key rotation runs in an atomic database transaction; failure on any record triggers complete rollback without secret loss.
- Schema audit logs warnings and flags drifted servers without crashing active processes.

## Security considerations

- Secrets re-encrypted via AES-256-GCM using authenticated cryptographic primitives.
- Never prints or logs raw key material or decrypted credentials.
- Schema verification uses deterministic RFC 8785 canonical JSON sorting to detect unauthorized tool modification.

## Testing

Unit and integration tests are located in [`backend/tests/integration/test_maintenance_jobs.py`](../../tests/integration/test_maintenance_jobs.py):

```bash
pytest backend/tests/integration/test_maintenance_jobs.py
```

## Operations

- Reference guide: [`docs/operations/MAINTENANCE_JOBS.md`](../../../docs/operations/MAINTENANCE_JOBS.md).
- Runbooks: [`RUNBOOK_02_MODEL_CHURN.md`](../../../docs/operations/runbooks/RUNBOOK_02_MODEL_CHURN.md), [`RUNBOOK_04_DATABASE_FAILOVER.md`](../../../docs/operations/runbooks/RUNBOOK_04_DATABASE_FAILOVER.md).
