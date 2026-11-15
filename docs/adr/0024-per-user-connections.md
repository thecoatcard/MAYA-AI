# ADR-0024: Per-user connections (GitHub, Notion) with encrypted storage

- **Status:** Proposed (owner request and approval of per-user credential storage, 2026-11-10)
- **Date:** 2026-11-10
- **Deciders:** Repository owner
- **Related:** [ADR-0022](0022-connected-folder-and-uploads.md), [ADR-0011](0011-mcp-gateway-and-specialist-agents.md), [Connections API](../api/CONNECTIONS.md), [Security](../security/CONNECTIONS.md)

## Context

GitHub and Notion tokens came only from the backend environment, were read once at start, had no UI, and failed opaquely ("not connecting"). Every user shared one account. The owner decided each user should add their own.

## Decision

1. Credentials live in the user's own `user_settings` row (key `connection:<provider>`), no migration, as AES-256-GCM ciphertext bound to workspace, user and provider. The key is `MAYA_CONNECTIONS_ENCRYPTION_KEY`; without it saving fails closed.
2. No API returns a secret; status is masked. Tokens are `SecretStr`, never logged, and the PII scrubber redacts their shapes.
3. Run-time resolution is per requesting user: the PM and coding agents (job owner), the `github_*` gateway tools (tool call scope), and capabilities for planner and prechecks (a context variable). The environment values remain the documented single-user default; an unreadable saved value does not fall back to them.
4. "Test connection" maps each outcome to a stable code and a plain instruction; Notion accepts a database URL or id and normalises it.
5. Providers are a registry (`PROVIDERS`), so Google and others add one entry.

## Consequences

- Uses `cryptography` (already installed transitively and imported by `core/totp.py`); it is not a declared direct dependency. Declaring it in `pyproject.toml` needs approval and was not done.
- Key loss or rotation makes saved tokens unreadable until users reconnect; there is no rotation tool yet.
- Notion stays on API version 2022-06-28 (database query); newer versions query per data source.
- Fleet agents are still built once; only credentials are resolved per user.
