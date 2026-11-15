# Data model and data stores

## Implemented

- Local PostgreSQL 17 with the `vector` and `pg_trgm` extensions, and Redis 7.4 with append-only persistence.
- A bootstrap administrator (`maya_migrator` in the local example; it is a superuser and runs migrations only) and
  a separate `maya_app` runtime login that is not a superuser, cannot create databases or roles, cannot bypass
  row-level security and owns nothing.
- The schema below, created by Alembic revisions in `backend/app/db/migrations/versions/`.
- SQLAlchemy/asyncpg and Redis async clients with explicit startup, probe and cleanup paths, plus a startup guard
  that refuses to run as any role with elevated attributes or object ownership.

Authentication, tenant isolation and the account tables are documented in
[authentication security](../security/AUTHENTICATION.md) and [ADR-0006](../adr/0006-tenant-isolation-with-row-level-security.md).

### Tables (18, all with forced row-level security)

| Table                                                     | Purpose                         | Notes                                                                                                                                                                                                                                                                                                                                                                                 |
| :-------------------------------------------------------- | :------------------------------ | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| `workspaces`                                              | One tenant                      | Policy on `id`; a tenant cannot list other workspaces' names or slugs                                                                                                                                                                                                                                                                                                                 |
| `users`                                                   | Accounts                        | Plan table plus `username`, `email_verified_at`, `password_changed_at`; `hashed_password` is mandatory                                                                                                                                                                                                                                                                                |
| `sessions`, `messages`                                    | Chat conversations              | Plan tables, unchanged. The Phase 04 chat repository (`backend/app/db/repositories/chat.py`) uses them: every statement names the workspace and the user (user-level ownership on top of row-level security), and keyset pagination orders by `(created_at, id)`. No index serves those queries yet; adding one needs a migration (Level 3), recommended before large histories exist |
| `tasks`, `subtasks`, `approval_requests`, `compensations` | Task engine and approvals       | Phase 05 added SQLAlchemy declarative models (`backend/app/db/models.py`) and migration `0005_phase_05_tasks_subtasks.py` adding `user_instruction`, `plan_status`, `active_plan`, `shared_context`, `execution_trace`, and `updated_at` to `tasks`, and `updated_at` plus role check constraints to `subtasks`                                                                       |
| `audit_log`                                               | Audit trail                     | Plan table; the runtime role may insert and read only. Hash-chained per workspace by `backend/app/services/audit_service.py` (see Audit hash chain below)                                                                                                                                                                                                                                                                                   |
| `mcp_servers`                                             | MCP external integrations       | Phase 08 (migration `0011_phase_08_mcp_servers.py`) added `health_status`, `last_ping_at`, `consecutive_failures`, `tool_count`, `tools_manifest`, `updated_at`, and composite index `idx_mcp_servers_workspace_active`. Declarative model in `backend/app/db/models.py`                                                                                                              |
| `file_artifacts`                                          | Generated artifacts             | Phase 09 added declarative model `FileArtifact` tracking `/artifacts/{workspace_id}/` files, SHA-256 checksums, and mime types                                                                                                                                                                                                                                                        |
| `memory_items`                                            | Memory items                    | Plan table; Phase 11                                                                                                                                                                                                                                                                                                                                                                  |
| `auth_otp_challenges`                                     | One-time codes                  | New. Only an HMAC of the code is stored; one live row per user and purpose                                                                                                                                                                                                                                                                                                            |
| `auth_sessions`                                           | Refresh-token sessions          | New. Holds the current refresh token id and a generation counter (any older generation is a replay); revocation and expiry                                                                                                                                                                                                                                                            |
| `checkpoints`, `checkpoint_blobs`, `checkpoint_writes`    | LangGraph durable state         | Phase 05 checkpointer storage; forced RLS binds rows to both workspace and user. Existing rows without an owner remain NULL and inaccessible; no owner is guessed                                                                                                                                                                                                                     |
| `user_settings`                                           | Per-user settings               | Revision `0015` (ADR-0016): one JSONB object per `(workspace_id, user_id)`, first key `chat_intent_classifier`; composite FK to `users`, forced RLS (`ws_isolation`, workspace only; the repository also filters by user); `maya_app` has SELECT, INSERT and UPDATE, no DELETE |
| `chat_idempotency`                                        | Durable chat claims and results | Revision `0007`; scoped by workspace and user, stores keyed digests only (no raw key or request content), forced RLS binds both owner settings; claims are permanent and finalized with an owned compare-and-set                                                                                                                                                                      |

The plan lists 12 tables, 11 of them tenant-scoped. This implementation adds the two `auth_*` tables and also
protects `workspaces`, then adds the three LangGraph checkpointer tables and the durable chat-idempotency table, so all 18 have row-level security. The plan's
chat table is called `sessions`, hence the `auth_` prefix on the login-session table.

The durable chat record is inserted before the user-message transaction and is the authoritative uniqueness fence.
The user message and session attachment commit together; the assistant message and terminal result commit together.
If a process stops after claiming but before finalization, the durable row remains `in_progress` and later requests
receive `request_in_progress`. This deliberately favors preventing a duplicate model call over automatically
reclaiming a potentially active request. Redis idempotency entries are only a cache; their TTLs do not delete the
PostgreSQL record. The key digest and request hash are HMACed with domain-separated keys derived from the configured
signing secret.

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
`backend/app/db/tenant.py`, so it cannot outlive the transaction on a pooled connection. Checkpoint tables also compare
`user_id` to the connection-local `app.user_id` setting. The PostgreSQL saver uses one dedicated connection per
owner context, set from server-verified workspace and user UUIDs; it does not accept identity from graph state or
client-supplied checkpoint data. The owner pair references `(users.workspace_id, users.id)` for new writes. Callers
should use server-generated thread IDs; RLS, not thread-ID secrecy, is the authorization boundary. Legacy checkpoint
rows with no owner remain inaccessible to every runtime identity.

`maya_app` privileges: `SELECT` and `INSERT` on `workspaces`; on `users` it can read every column **except**
`hashed_password`, insert, and update only `hashed_password`, `email_verified_at` and `password_changed_at`;
`SELECT` and `INSERT` on the two `auth_*` tables, with `UPDATE` limited to their state columns
(`attempts`, `consumed_at`, `invalidated_at`; `refresh_jti`, `refresh_generation`, `refresh_expires_at`,
`last_used_at`, `revoked_at`, `revoked_reason`) and no `DELETE`; `SELECT` and `INSERT` on `audit_log`; full
`SELECT`/`INSERT`/`UPDATE`/`DELETE` on the other plan tables and checkpoint tables, still limited by their RLS
policies. No `TRUNCATE`, no DDL, no `CREATE` on the schema. Checkpoint tables are created only by Alembic; runtime
checkpointer setup is disabled.

Before the workspace is known (login by username, verification and recovery by e-mail) the API calls
`public.auth_find_account_by_username` and `public.auth_find_account_by_email`. They are `SECURITY DEFINER`,
owned by the NOLOGIN role `maya_auth_definer` (which can read only the lookup columns of `users` through a policy
scoped to that role), have `search_path` fixed to `pg_catalog, pg_temp`, match one identifier exactly and are
executable by `maya_app` only. The by-username function returns the identity columns plus `hashed_password` and
`password_changed_at` (login verifies the hash, and opens its session only if that password version is still
current); the by-e-mail function returns the identity columns only.

Row-level security separates workspaces across tenant tables. Separating users inside a workspace remains the
repositories' responsibility except for the checkpoint tables, whose policies also require the user setting. The new
cross-owner checkpoint PostgreSQL tests have not been run against a live database.

### Migrations

Run from the repository root with the migration role (never the runtime role):

```powershell
uv run --directory backend alembic -x env_file=..\.env.example upgrade head
uv run --directory backend alembic -x env_file=..\.env.example downgrade base
```

`MAYA_MIGRATION_DATABASE_URL` in the process environment works without `-x`. Each revision is one transaction.

| Revision | Content                                                                                             |
| :------- | :-------------------------------------------------------------------------------------------------- |
| `0001`   | Extensions (if missing), `workspaces`, `users`                                                      |
| `0002`   | The remaining plan tables and indexes                                                               |
| `0003`   | `auth_otp_challenges`, `auth_sessions`                                                              |
| `0004`   | Roles, row-level security, grants, the definer functions                                            |
| `0005`   | Phase 05 task and subtask projection columns and indexes                                            |
| `0006`   | LangGraph checkpoint tables and workspace-plus-user RLS                                             |
| `0007`   | Durable chat idempotency claims/results, workspace-plus-user RLS and least-privilege runtime grants |
| `0013`   | Repair of `task_outbox_events` (created by `0008`): `ws_isolation` policy with WITH CHECK, `maya_app` gets SELECT, INSERT and UPDATE (status, dispatched_at) only. Idempotent; see below |
| `0014`   | `auth_any_account_exists()` and `list_workspaces_with_open_work()` definer functions (ids/boolean only) |

For a deployment with an older checkpoint writer, stop checkpoint writes before applying `0006`, then deploy the
owner-bound checkpointer. Old rows remain inaccessible; the revision does not infer owners or migrate their content.
The migration creates indexes transactionally when missing, so schedule it with the possible table-lock/build cost in
mind.

Rollback: `downgrade` drops the objects of each revision, which **destroys the data** in them; it is for disposable and
development databases, and is tested by upgrading, downgrading to the base and upgrading again with identical
schema snapshots. Revision `0006` refuses to downgrade once any checkpoint data exists, because removing its owner RLS
would expose surviving rows; do not treat an application rollback as permission to remove this policy. The
cluster-level role `maya_auth_definer` is created idempotently and intentionally kept by a downgrade. Production
changes should be forward-only.

The migrations also run as a **non-superuser** owner (tested with a throwaway role that has only `CREATEROLE` and owns
its database). Such a role must first be allowed to `SET ROLE` to `maya_auth_definer`
(`GRANT maya_auth_definer TO <role> WITH SET TRUE`); without it revision 0004 fails early and rolls back. The tests use
disposable `maya_test_*` databases that they create and drop.

**Persistent local database:** on 2026-11-05, with the owner's approval, the persistent `maya` database was migrated to
revision `0004` (it was empty; a schema-only backup was taken first outside the repository). It holds no accounts.
Revisions `0005` through `0007` have not been applied or live-verified in this remediation.

Revision `0014` adds two `SECURITY DEFINER` functions owned by `maya_auth_definer` (pinned `search_path`, no PUBLIC
execute, `EXECUTE` for `maya_app` only) so the runtime can answer cross-tenant questions without reading rows:
`auth_any_account_exists()` (local-owner bootstrap, ADR-0014) and `list_workspaces_with_open_work()` (ids of workspaces
with open tasks, subtasks or pending outbox events). The definer role gets column-level SELECT on only the columns
involved and a SELECT-only `definer_open_work_lookup` policy on `tasks`, `subtasks` and `task_outbox_events`; `maya_app`
still sees no rows of those tables without a tenant context. Idempotent; the downgrade drops the functions, policies
and column grants.

Revision `0013` exists because `0008` as committed gave `maya_app` no privileges on `task_outbox_events` (and named its
policy `task_outbox_events_tenant_isolation`), so the first outbox insert failed with "permission denied". Alembic does
not re-run an applied revision, so `0008` was left at its committed content and `0013` repairs any database whatever
its starting point (committed `0008`, hand-patched grants, or fresh). Its downgrade only removes access (back to the
state `0008` created). Apply to an existing development database from `backend/` with
`python -m alembic -x env_file=<your env file> upgrade head`.

### Not implemented

Audit hash chaining and append-only enforcement beyond the grants, the secrets vault, vector search queries and
production-route integration of the task projection and LangGraph checkpointer.

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


## Audit hash chain

`audit_log` rows are written only through `record_audit` (`backend/app/services/audit_service.py`) inside the caller's
tenant transaction. A per-workspace `pg_advisory_xact_lock` serialises writers; each row stores `prev_hash` (the
previous row's `row_hash`, NULL for the first row) and `row_hash = sha256(prev_hash || canonical_json)`. The canonical
JSON (sorted keys, compact separators, ASCII, UUIDs as strings, `created_at` as UTC ISO-8601 microseconds) is
documented in the module docstring. Inputs and outputs are redacted before storage (bearer tokens, JWTs, passwords,
API keys, card numbers, SSNs, secret-named keys); floats are stored as strings. `verify_chain` recomputes the chain.
Non-tool events use `server_name='maya'` and `tool_name='<category>.<verb>'`. GDPR purge pseudonymises `user_ref`
(`purge_user_ref`, administrative connection only because `maya_app` has no UPDATE) and re-seals the chain. Retention
(365 days) is not enforced by code yet. Audit rows are not yet written by approvals, the worker or the MCP gate.
