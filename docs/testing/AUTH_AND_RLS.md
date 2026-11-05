# Verification record: authentication and row-level security (Phase 02)

Recorded 2026-11-05 on the owner's Windows machine, branch `feat/phase-02-auth-database-security`.
Services: the project's own PostgreSQL 17 (pgvector image) and Redis 7.4 containers, started from the root Compose file
with the example environment. The test suites only use disposable `maya_test_*` databases. After the suite passed, the owner-
approved migration of the persistent `maya` database was run (schema-only backup first, revision `0004`, 14 tables with forced
RLS, 0 rows) and the real API process was started against it and probed over loopback (health, readiness, generic 401 for
bad credentials, 503 for registration without SMTP, 401 without a token, 413 for an oversize body). No external service (SMTP,
Gemini) was contacted by any agent.

## How to run

```powershell
uv run --directory backend pytest -q                         # deterministic tests; service tests are SKIPPED
$env:MAYA_TEST_ENV_FILE = "..\.env.example"                  # point the service tests at the local containers
$env:MAYA_REQUIRE_POSTGRES_TESTS = "1"                       # missing services fail instead of skipping
uv run --directory backend pytest -q
uv run --directory backend ruff check .; uv run --directory backend ruff format --check .; uv run --directory backend mypy .
```

Alternatively set `MAYA_TEST_POSTGRES_ADMIN_URL` and `MAYA_TEST_POSTGRES_APP_URL` (asyncpg URLs of the migration and
runtime roles) and `MAYA_TEST_REDIS_URL`. Service tests create a database named `maya_test_<12 hex>` on a **loopback**
host only, run the migrations as the migration role, test as the runtime role, and drop it afterwards; the harness refuses
to drop anything that does not match that name. Seeding uses the administrator, which bypasses row-level security.

## Results

| Run | Result |
| :--- | :--- |
| Full backend suite with local services and `MAYA_REQUIRE_POSTGRES_TESTS=1` | **864 passed** |
| Same suite without service configuration | 502 passed, **362 skipped** (skipped is not passed) |
| `ruff check`, `ruff format --check`, strict `mypy` over app and tests | clean (101 source files) |
| Disposable databases and probe roles left behind | 0 |

The suite also passed when its modules were run in reverse order. GitHub CI has never run (no remote), so none of
this is CI-verified.

## What is covered

| Area | Tests | Highlights |
| :--- | :--- | :--- |
| Primitives (`tests/unit`) | 378 total in the folder | Argon2id parameters/rehash/policy, OTP MAC and constant-time compare, JWT claims, algorithm and type confusion, expiry at the exact boundary, generation claim, settings validation, SMTP over mandatory TLS with generic errors and header-injection refusal, Redis limiter (fake and real), request-body guard, SQL-literal static check |
| Flows (`tests/auth`, 90 scenarios) | each run on an in-memory store **and** the real database (180 runs) | Registration, verification, login, recovery, refresh, logout; enumeration-identical results; code expiry, five-attempt cap, single use, supersession, cross-purpose; rate limits and cooldown windows; concurrent redemption and refresh; multi-generation replay; login versus reset races |
| HTTP (`tests/integration`) | 33 | Full lifecycle over HTTP, identical responses for every registration outcome, no echo of submitted values, error shapes, 429/503 behavior, public surface equals the documented endpoint list, log scan for credentials/codes/tokens/addresses |
| Real PostgreSQL (`tests/postgres`) | 273 | Below |

### Real PostgreSQL

- **Migrations:** upgrade to head; every table has forced RLS and a policy; downgrade to base and upgrade again give identical
  schema, policy, grant and function snapshots; each revision reverts one step at a time; the Alembic CLI works with
  `-x env_file`, with the environment variable, and refuses an invalid URL or offline SQL.
- **RLS matrix, as the runtime role, for all 14 tables:** no context, empty context and another workspace's context return no
  rows; a malformed context is an error; each workspace sees exactly its own rows and cannot read a foreign row by id;
  `UPDATE` and `DELETE` affect only own rows; inserts and row moves into another workspace fail with a row-level-security
  violation; a negative control disables RLS on one table and shows the matrix would notice the leak.
- **Roles and privileges:** the runtime role has no elevated attribute, owns nothing and has no memberships; 40+ statements
  (`TRUNCATE`, `DROP`, disabling RLS, creating policies, roles, schemas or functions, `SET ROLE`, updating identity or state
  columns it may not, `SELECT hashed_password`, `SELECT *` from `users`, changing or deleting `audit_log`) are refused with
  `insufficient_privilege`.
- **Definer functions:** `SECURITY DEFINER`, owner, fixed `search_path`, no `PUBLIC` execute; exact columns (the e-mail lookup
  has no hash); exact match only (injection strings, wildcards and case variants find nothing); a shadow table on the search
  path cannot hijack them; the definer role can read only the lookup columns.
- **Composite foreign keys:** a row cannot reference a user, session, task or subtask of another workspace (constraint
  violation even for the administrator); deleting a workspace cascades only inside it; `ON DELETE SET NULL (subtask_id)`
  keeps the workspace column.
- **Non-superuser migration owner:** a throwaway role with only `CREATEROLE` that owns its database migrates up and down;
  without the right to `SET ROLE` to the definer role revision 0004 fails early and rolls back as a unit. This test found
  that the function owner needs `CREATE` on the schema during the transfer, which a superuser hides.
- **Tenant context:** a pooled connection (single physical connection) carries no context after commit, rollback or an
  exception; 24 concurrent requests from two workspaces over a pool of three never see each other's rows; success commits and
  an error rolls back.
- **Authentication store isolation:** another workspace's context and another user in the same workspace cannot attempt a
  code, rotate, read or revoke a session, or change a password version; writing into a foreign workspace fails; unique
  constraints see accounts hidden by RLS and roll the half-created workspace back.
- **Whole application:** the production factory with real lifespan, PostgreSQL and Redis completes registration to refresh
  replay; startup **refuses** to run as the administrator role; the HTTP API with two workspaces keeps tokens and tenant context
  apart under concurrency, and logout and password reset end access immediately.

## What is not verified

- GitHub CI: the workflow now provisions PostgreSQL, Redis, Python and the web dependencies, but it has never run
  (there is no remote), so it is written and linted only.
- Real SMTP delivery: no agent contacted a mail server. The owner can use the SMTP check tool; its logic is covered by tests
  with a mocked mail library.
- Timing resistance beyond the extra work that is equalized; load or abuse behavior at scale.
- Per-user separation inside a workspace for tables other than the authentication tables (the database does not enforce it).
- Findings the review raised that need an owner decision are listed in the
  [security document](../security/AUTHENTICATION.md#known-limits-and-residual-risks).
