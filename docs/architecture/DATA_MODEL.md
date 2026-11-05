# Data model and data stores

## Implemented

- Local PostgreSQL 17 with the `vector` and `pg_trgm` extensions, and Redis 7.4 with append-only persistence.
- A bootstrap administrator (`maya_migrator` in the local example; it is a superuser and runs migrations only) and
  a separate `maya_app` runtime login that is not a superuser, cannot create databases or roles, cannot bypass
  row-level security and owns nothing.
- The schema below, created by four reversible Alembic revisions in `backend/app/db/migrations/versions/`.
- SQLAlchemy/asyncpg and Redis async clients with explicit startup, probe and cleanup paths, plus a startup guard
  that refuses to run as any role with elevated attributes or object ownership.

Authentication, tenant isolation and the account tables are documented in
[authentication security](../security/AUTHENTICATION.md) and [ADR-0006](../adr/0006-tenant-isolation-with-row-level-security.md).

### Tables (14, all with forced row-level security)

| Table | Purpose | Notes |
| :--- | :--- | :--- |
| `workspaces` | One tenant | Policy on `id`; a tenant cannot list other workspaces' names or slugs |
| `users` | Accounts | Plan table plus `username`, `email_verified_at`, `password_changed_at`; `hashed_password` is mandatory |
| `sessions`, `messages` | Chat conversations | Plan tables, unchanged; no code uses them yet (Phase 04) |
| `tasks`, `subtasks`, `approval_requests`, `compensations` | Task engine and approvals | Plan tables, unchanged (Phases 05 to 07) |
| `audit_log` | Audit trail | Plan table; the runtime role may insert and read only. Hash chaining is not computed yet (Phase 07) |
| `mcp_servers`, `file_artifacts`, `memory_items` | Integrations, artifacts, memory | Plan tables and indexes, unchanged (Phases 08, 10, 11) |
| `auth_otp_challenges` | One-time codes | New. Only an HMAC of the code is stored; one live row per user and purpose |
| `auth_sessions` | Refresh-token sessions | New. Holds the current refresh token id and a generation counter (any older generation is a replay); revocation and expiry |

The plan lists 12 tables, 11 of them tenant-scoped. This implementation adds the two `auth_*` tables and also
protects `workspaces`, so all 14 have row-level security. The plan's chat table is called `sessions`, hence the
`auth_` prefix on the login-session table.

### Deviations from the plan's DDL (owner-approved on 2026-11-05)

- `users.hashed_password` is `NOT NULL` (own accounts replace the external-identity option); `username` (unique,
  `[a-z0-9_.-]{3,32}`), `email_verified_at` and `password_changed_at` were added; `email` must be lower case;
  `role` is limited to `admin` or `user`.
- Row-level security also covers `workspaces`.
- Two authentication tables were added. No ORM or Core table models exist yet; the store uses parameterized SQL.
- Every reference to a `users`, `sessions`, `tasks` or `subtasks` row is a **composite** foreign key
  `(workspace_id, <parent>_id)` onto a `UNIQUE (workspace_id, id)` of the parent (`compensations.subtask_id` uses
  `ON DELETE SET NULL (subtask_id)` so the workspace column is kept). PostgreSQL does not apply row-level security to
  foreign-key checks, so plain keys would let a row point at another workspace's row and let a delete cascade across
  workspaces. Later phases must insert rows with the parent's workspace identifier, which they do anyway.

### Row-level security and roles

Every table has `ENABLE` and `FORCE ROW LEVEL SECURITY` and one policy, `ws_isolation`, using
`workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid` for both reads and writes
(`id` for `workspaces`). With the setting unset or empty no rows are visible or writable; a malformed value is an
error, not a match. The application sets it per transaction with `set_config(..., true)` in
`backend/app/db/tenant.py`, so it cannot outlive the transaction on a pooled connection.

`maya_app` privileges: `SELECT` and `INSERT` on `workspaces`; on `users` it can read every column **except**
`hashed_password`, insert, and update only `hashed_password`, `email_verified_at` and `password_changed_at`;
`SELECT` and `INSERT` on the two `auth_*` tables, with `UPDATE` limited to their state columns
(`attempts`, `consumed_at`, `invalidated_at`; `refresh_jti`, `refresh_generation`, `refresh_expires_at`,
`last_used_at`, `revoked_at`, `revoked_reason`) and no `DELETE`; `SELECT` and `INSERT` on `audit_log`; full
`SELECT`/`INSERT`/`UPDATE`/`DELETE` on the other plan tables. No `TRUNCATE`, no DDL, no `CREATE` on the schema.

Before the workspace is known (login by username, verification and recovery by e-mail) the API calls
`public.auth_find_account_by_username` and `public.auth_find_account_by_email`. They are `SECURITY DEFINER`,
owned by the NOLOGIN role `maya_auth_definer` (which can read only the lookup columns of `users` through a policy
scoped to that role), have `search_path` fixed to `pg_catalog, pg_temp`, match one identifier exactly and are
executable by `maya_app` only. The by-username function returns the identity columns plus `hashed_password` and
`password_changed_at` (login verifies the hash, and opens its session only if that password version is still
current); the by-e-mail function returns the identity columns only.

Row-level security separates workspaces. Separating users inside a workspace is the repositories' job; it is
tested for the authentication tables only.

### Migrations

Run from the repository root with the migration role (never the runtime role):

```powershell
uv run --directory backend alembic -x env_file=..\.env.example upgrade head
uv run --directory backend alembic -x env_file=..\.env.example downgrade base
```

`MAYA_MIGRATION_DATABASE_URL` in the process environment works without `-x`. Each revision is one transaction.

| Revision | Content |
| :--- | :--- |
| `0001` | Extensions (if missing), `workspaces`, `users` |
| `0002` | The remaining plan tables and indexes |
| `0003` | `auth_otp_challenges`, `auth_sessions` |
| `0004` | Roles, row-level security, grants, the definer functions |

Rollback: `downgrade` drops the objects of each revision, which **destroys the data** in them; it is for disposable and
development databases, and is tested by upgrading, downgrading to the base and upgrading again with identical
schema snapshots. The cluster-level role `maya_auth_definer` is created idempotently and intentionally kept by a
downgrade. Production changes should be forward-only.

The migrations also run as a **non-superuser** owner (tested with a throwaway role that has only `CREATEROLE` and owns
its database). Such a role must first be allowed to `SET ROLE` to `maya_auth_definer`
(`GRANT maya_auth_definer TO <role> WITH SET TRUE`); without it revision 0004 fails early and rolls back. The tests use
disposable `maya_test_*` databases that they create and drop.

**Persistent local database:** on 2026-11-05, with the owner's approval, the persistent `maya` database was migrated to
revision `0004` (it was empty; a schema-only backup was taken first outside the repository). It holds no accounts.

### Not implemented

Audit hash chaining and append-only enforcement beyond the grants, the secrets vault, vector search queries and
every consumer of the plan tables other than `users`, `workspaces` and the `auth_*` tables.

## Synthetic filtered-vector spike

[Spike S2](SPIKE_S2_VECTOR.md) now measures a global HNSW index on disposable
synthetic 768-dimensional vectors with both workspace and user post-index filters.
Its exact baseline and ANN executions have distinct SQL and disabled statement
caching to avoid reusing a sequential-scan plan as an ANN measurement.
An initial perfect-recall run was invalidated by that plan-cache issue and superseded.

In the corrected 30,000-row run, mean recall@10 was 0.056 with iterative scans off,
0.200 with strict ordering and 0.544 with relaxed ordering. Off mode underfilled
all 80 measured queries; both iterative modes returned 10 rows but not necessarily
the exact nearest 10. These are synthetic observations, not acceptance thresholds.
No application vector index, RLS policy, partial index or production tuning decision
was introduced; the disposable database and its volume were removed.

## Offline embedding analysis

[S4](SPIKE_S4_EMBEDDINGS.md) now has a documented 768-dimension SDK request plan
and offline response/vector validation. Hand-constructed fixtures exercise shape,
norm, cosine and local float32-conversion metrics. Those fixture values are not
provider results. The subsequent [user-run S4 smoke](SPIKE_S4_EMBEDDINGS.md#user-run-live-measurements-2026-11-05)
returned three actual 768-dimensional vectors with raw norms near 1.0.
No schema, embedding storage or application model integration changed.

The separately locked actual SDK adapter now sends three independent async
requests, one per synthetic text, and applies that analyzer to the collected
vectors. Gemini Embedding 2 aggregates list inputs; the offline bundle must not
be treated as one request returning three independent embeddings.
This behavior has SDK-model/fake-client coverage and the subsequent bounded
user-run smoke evidence; it does not establish production retrieval quality.

## Planned

The LangGraph checkpointer requires psycopg 3 independently of the current SQLAlchemy/asyncpg connection
path; it is not installed. Pre-authentication audit rows for unknown identifiers have no workspace and are
recorded only as log lines until the Phase 07 audit writer exists (see [ADR-0006](../adr/0006-tenant-isolation-with-row-level-security.md)).

See the [local runbook](../operations/LOCAL_DEVELOPMENT.md) for initialization,
persistence and non-destructive shutdown behavior.
