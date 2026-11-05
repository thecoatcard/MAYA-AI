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
