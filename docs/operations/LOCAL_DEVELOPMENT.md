# Local foundation runbook

## Scope and approvals

The owner approved Phase 01 scaffolding, infrastructure configuration, dependency
installation and starting local containers in the current implementation conversation.
Paid Gemini calls remain blocked, with capping and real S3/S4 verification
explicitly deferred by the owner; see the
[deferred development record](../architecture/SPIKE_S3_GEMINI_LIVE.md#deferred-development-record).
Commits and pushes are not approved.

The foundation is local-only, not a deployment. Example passwords are deliberately
non-secret development values. Redis has no authentication and must remain loopback-only.
There is no MinIO, object store or Docker socket mounted into an application.

## Start

Run from the repository root. Docker Desktop must be running with Linux containers.
Python 3.12, uv and Node.js 22.12+ (or 24+) are required.

```powershell
docker compose --env-file .env.example config --quiet
docker compose --env-file .env.example up -d --wait
uv sync --directory backend --locked
npm --prefix apps\web ci
```

Use explicit `--env-file .env.example` in agent-run checks: do not read a private
dotenv file. The API also requires an explicit file or process configuration.
In separate terminals:

```powershell
uv run --directory backend python -m app --env-file ..\.env.example
npm --prefix apps\web run dev
```

Open the web client at `http://127.0.0.1:3000` and API docs at `http://127.0.0.1:8000/docs`.
The scaffold does not implement a browser-to-API product workflow yet.

For repository validation on Windows, activate the backend environment or add its
Scripts directory to the validation process PATH:

```powershell
$env:PATH = (Resolve-Path backend\.venv\Scripts).Path + ";" + $env:PATH
npm run validate
```

Use the [latest verification record](../testing/FOUNDATION.md) for validation
results. The original scaffold run failed on planned-file documentation references;
that historical result must not be used as the current status. Comparison checks
require a real base branch; skipped checks are not passes.

## Verify

```powershell
docker compose --env-file .env.example ps
curl.exe --fail http://127.0.0.1:8000/api/v1/healthz
curl.exe --fail http://127.0.0.1:8000/api/v1/readyz
docker compose --env-file .env.example exec -T postgres psql -U maya_migrator -d maya -c "SELECT extname, extversion FROM pg_extension;"
docker compose --env-file .env.example exec -T redis redis-cli CONFIG GET appendonly
```

Host ports are 15432 for PostgreSQL and 16379 for Redis to avoid common local-service
collisions. Change the process configuration and connection URLs together if needed;
do not stop another project's service. Container-internal ports remain 5432 and 6379.
Images are digest-pinned; update them only through an approved dependency/infrastructure change.

## Database schema (migrations)

The schema is created by Alembic with the **migration** role, never the runtime role. The persistent local `maya`
database was migrated to revision `0004` on 2026-11-05 (empty, schema-only backup taken first); a fresh volume starts empty
and is never migrated automatically, so apply the schema deliberately:

```powershell
uv run --directory backend alembic -x env_file=..\.env.example upgrade head
uv run --directory backend alembic -x env_file=..\.env.example downgrade base   # destroys the data in the tables
```

`downgrade` is for disposable and development databases only. Details: [data model](../architecture/DATA_MODEL.md#migrations).
The API refuses to start if `MAYA_DATABASE_URL` points at a role that is a superuser, can bypass row-level security or owns
tables, so keep the runtime and migration URLs separate. Real e-mail delivery needs `MAYA_SMTP_*` in your private `.env`;
without it the registration and recovery endpoints answer 503.

## Checking that e-mail really arrives (run by you)

The registration and recovery endpoints answer 503 until `MAYA_SMTP_HOST` and `MAYA_SMTP_FROM_ADDRESS` (and, if the server
needs a login, `MAYA_SMTP_USERNAME` and `MAYA_SMTP_PASSWORD`) are set in your private `.env`. Agents may not read that file
or contact the mail server, so you run the check yourself:

```powershell
uv run --directory backend python -m app.tools.smtp_smoke --env-file ..\.env
```

It asks for a recipient and a typed `yes`, sends one fixed test message (no code) and prints only whether the server accepted
it, or a failure category. See the [tool's README](../../backend/app/tools/README.md).

## Running the PostgreSQL and Redis tests

Deterministic tests run with `pytest -q`. Tests that need the services are skipped (and reported as skipped) unless you
point them at the local containers; they create and drop disposable `maya_test_` databases and refuse non-loopback hosts:

```powershell
$env:MAYA_TEST_ENV_FILE = "..\.env.example"
$env:MAYA_REQUIRE_POSTGRES_TESTS = "1"
uv run --directory backend pytest -q
```

If your containers were initialized with different passwords than the example file, set `MAYA_TEST_POSTGRES_ADMIN_URL`
and `MAYA_TEST_POSTGRES_APP_URL` yourself. The [verification record](../testing/AUTH_AND_RLS.md) lists what they prove.

## Continuous integration

`.github/workflows/governance.yml` runs `validate.mjs --stage ci` on every pull request and push to `main`. The job starts
digest-pinned PostgreSQL 17 (pgvector) and Redis 7.4 service containers (the images of `docker-compose.yml`), installs the
locked backend environment with a pinned `uv`, installs the web dependencies, creates the runtime role with
`docker/postgres/init.sql`, and sets `MAYA_REQUIRE_POSTGRES_TESTS=1`, so the tenant-isolation and authentication tests run
for real and a missing service fails the job. The passwords there are the public example values. **No run of this workflow
has been observed yet**: the GitHub remote exists since 2026-11-05, so treat it as written, linted and untested until
the first result is seen. Whether it is a required check depends on the ruleset (see `docs/operations/BRANCH_PROTECTION.md`).

## Initialization and recovery

On an empty volume, the image creates the database and runs SQL extension/role
initialization. psql reads the runtime password directly from the container
environment and quotes it as a SQL literal, rather than passing it on a command line. Changing
example passwords later does not change passwords already stored in that volume.

If first initialization fails, inspect the bounded container logs and database state.
Do not delete the volume or blindly rerun the non-idempotent role creation command.
In the initial local verification only, a shell-based initialization attempt failed
on Windows line endings before extensions or the runtime role existed. That new
database was bootstrapped without deleting data. The final implementation uses
the image's native SQL initialization path instead of a shell wrapper.

## Stop

Stop API/web processes with Ctrl+C in their own terminals, then:

```powershell
docker compose --env-file .env.example stop
```

Named volumes persist. Do not use the volume-deletion flag without explicit approval.

Temporary API/web verification servers and the two MAYA data-service containers
were stopped after verification. The local database and AOF volumes were preserved.
