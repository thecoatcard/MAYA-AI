# ADR-0006: Tenant isolation with forced row-level security

- **Status:** Accepted (by the repository owner, 2026-11-05)
- **Date:** 2026-11-05
- **Deciders:** Repository owner
- **Related:** [Phase 02](../implementation/IMPLEMENTATION_PHASES/PHASE_02_AUTH_DATABASE_SECURITY.md), [ADR-0005](0005-own-account-authentication.md), [data model](../architecture/DATA_MODEL.md), [authentication security](../security/AUTHENTICATION.md), master plan section 14

## Context

The master plan requires a `workspace_id` on every tenant table, forced row-level security (RLS) keyed by a session
setting, a runtime role that does not own the tables and cannot bypass RLS, and a `SECURITY DEFINER` function for login
before the workspace is known. Review of the plan found open points: the `workspaces` table has no RLS, so any tenant
could list every workspace name; audit events before authentication (failed logins, codes for unknown addresses) have no
workspace, but `audit_log.workspace_id` is mandatory and chained per workspace; the plan's `sessions` table is the chat
table, so login sessions need another name; and RLS keyed by workspace alone cannot separate users within a workspace.

## Decision

1. **RLS everywhere:** `ENABLE` and `FORCE` RLS with one `ws_isolation` policy on all 14 tables, including `workspaces`
   (`id = setting`) and the two new `auth_otp_challenges` and `auth_sessions` tables. A workspace is therefore not
   listable by others. Registration creates its workspace after setting the context to the new identifier.
2. **Context:** `app.workspace_id` is set per transaction with `set_config(..., true)` (a bind parameter; `SET LOCAL`
   cannot take one) by `tenant_transaction`. Transaction boundaries belong to the service layer, not to dependency teardown.
   Unset, empty or foreign means no rows and no writes; a malformed value is an error.
3. **Roles:** the runtime role `maya_app` owns nothing, has no `BYPASSRLS`, DDL or `TRUNCATE`, cannot read
   `users.hashed_password` directly, may update only the state columns of the two `auth_*` tables, and appends to but
   cannot change `audit_log`. The API refuses to start as a role that is
   privileged or owns objects, because a mistaken runtime URL would silently disable every policy.
4. **Pre-login lookups:** two `SECURITY DEFINER` functions (by username, by e-mail), owned by a NOLOGIN role that can read
   only the lookup columns of `users` through a policy scoped to that role (not `BYPASSRLS`), with a fixed
   `search_path = pg_catalog, pg_temp`, one exact-match row, and `EXECUTE` for `maya_app` only. Only the by-username
   function returns the hash and the password version (login needs them). Refresh and logout take the workspace from a
   verified token claim and need no further definer function.
5. **User separation inside a workspace** is done by repository queries that always carry the user identifier, with tests;
   the database enforces workspace separation only. A second setting (`app.user_id`) and per-user policies were considered
   and deferred (owner decision, 2026-11-05): a setting the runtime role can change itself protects only against missing
   filters, which the repository SQL carries and tests prove for the authentication tables. Revisit when user-owned tables
   get consumers (Phase 04 onward).
6. **Pre-authentication audit:** Phase 02 writes no `audit_log` rows. Events go to structured logs with fixed names and no
   identifiers. Known-account events will be written to that account's workspace by the Phase 07 chain writer; events about
   unknown identifiers stay log-only unless the owner later approves a separate append-only table.
7. **Migrations** live in `backend/app/db/migrations/` (so the existing approval glob `**/migrations/**` covers them), run as
   the migration role, one transaction per revision; the NOLOGIN role is created idempotently and kept on downgrade.
8. **Grants** are broad CRUD on the plan tables not used yet (`audit_log` insert and read only), narrowed by their owning
   phases; RLS applies regardless.
9. **Composite foreign keys.** Row-level security does not apply to foreign-key checks, so every reference to a user,
   session, task or subtask is `(workspace_id, <parent>_id)` onto a `UNIQUE (workspace_id, id)` of the parent. A row can
   then never point at, or cascade into, another workspace. This deviates from the plan's simple keys.
10. **Migration owner.** The migrations also run as a non-superuser owner (tested). Transferring the lookup functions to
    `maya_auth_definer` needs the right to `SET ROLE` to it and `CREATE` on the schema for that role during the transfer;
    revision 0004 grants and then revokes the latter and fails early with a clear message when the former is missing.

## Alternatives considered

- **Leave `workspaces` without RLS (as in the plan):** leaks tenant names and slugs.
- **A system workspace or nullable `audit_log.workspace_id` for pre-login events:** pollutes tenant data or changes the
  plan DDL and breaks the per-workspace chain; deferred rather than guessed.
- **A separate `auth_events` table:** one more table for data with no reader yet; not approved.
- **`BYPASSRLS` for the definer role:** broader than a policy scoped to the one role and the lookup columns.
- **Policy exceptions for login on `maya_app`:** would weaken the one control that must have none.
- **Two settings (workspace and user) in every policy now:** deviates from the plan's policy and complicates admin and
  listing cases before any consumer exists.

## Consequences

- Positive: a missing `WHERE workspace_id` in application code returns nothing instead of leaking; the credential column is
  unreadable to the application role; pooled connections cannot carry a tenant forward.
- Negative: a connection that can execute arbitrary SQL as `maya_app` could set the setting itself, so RLS is defense in
  depth beside parameterized SQL (a test requires literal statements), not a replacement; per-user isolation is only as
  good as each repository; one transaction per authenticated request for the session check plus the work; the
  composite keys add a unique index per parent table and every later insert must carry the parent's workspace; a
  non-superuser migration owner must be granted membership of `maya_auth_definer` first.
- Mitigations: static SQL-hygiene test, the startup guard, a negative-control test showing the matrix fails when RLS is
  disabled, and the real-database suite in the [verification record](../testing/AUTH_AND_RLS.md).

## Security and compliance impact

The tenant boundary is enforced by the database engine for every table. Personal data (e-mail, username) is readable by
the application role only inside its own workspace, and only through the definer functions across workspaces by exact
identifier. Logs and responses carry no identifiers about other accounts.

## Implementation notes

Alembic revisions 0001 to 0004 in `backend/app/db/migrations/versions/0004_roles_rls_grants.py` and its predecessors,
`backend/app/db/tenant.py`, `backend/app/auth/postgres.py`.
PostgreSQL tests run against disposable `maya_test_*` databases created from the local service; they are skipped (and
reported as skipped) when the service is not configured, and fail instead when `MAYA_REQUIRE_POSTGRES_TESTS=1`.

## Review trigger

User-owned tables gain consumers (decide on a user-level policy), the Phase 07 audit writer exists (record known-account
events and decide on unknown ones), the application starts using a connection pooler other than SQLAlchemy's, or a
production role split replaces the local superuser migrator.
