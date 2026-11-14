# app/connections: per-user connections to GitHub, Notion and others (ADR-0024)

- `crypto.py`: AES-256-GCM `ConnectionCipher`. Key from `MAYA_CONNECTIONS_ENCRYPTION_KEY`; the associated data binds each value to `workspace|user|provider`. No key means no cipher, so saving fails closed.
- `providers.py`: `PROVIDERS` (GitHub, Notion): token and Notion database URL/id normalisation, and the live check that turns every outcome into a `TestResult` with a stable `code` and a plain sentence. Add a provider here (one `ProviderSpec`) and the storage, API and web form follow.
- `service.py`: `ConnectionService`: save, status (masked), test, disconnect, and `resolve(workspace, user, provider)` used at run time by the PM and coding agents, the `github_*` gateway tools, and the per-user capabilities that planner and prechecks read.

Storage is the caller's own `user_settings` row (key `connection:<provider>`, value `v1.<base64>` ciphertext): no migration. Resolution order: the user's saved connection, else the server environment default (`MAYA_GITHUB_TOKEN`, `MAYA_NOTION_*`, `MAYA_MAIL_*`) ONLY for the deployment owner (`ownership.py`, `MAYA_ENV_CONNECTIONS_USERS`; empty means nobody), else not connected. A saved but unreadable value never falls back to the environment default.

Contract: `docs/api/CONNECTIONS.md`. Threats and key handling: `docs/security/CONNECTIONS.md`. Tests: `tests/unit/test_connections.py`.
