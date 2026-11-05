# MAYA API foundation

Implemented: typed configuration, JSON logs with generated request/trace IDs, async PostgreSQL/Redis pools,
startup/shutdown cleanup, public health probes, the [database schema with forced row-level security](../docs/architecture/DATA_MODEL.md)
and Alembic migrations, and own-account [authentication](../docs/api/AUTH.md) (registration with e-mail codes, login,
password recovery, refresh rotation, logout). Chat, model calls, tasks, agents and voice are not implemented.

## Run locally

From the repository root, with Python 3.12 and uv installed:

```powershell
uv sync --directory backend --locked
uv run --directory backend python -m app --env-file ..\.env.example
```

Only use the example file for local development. No dotenv file is loaded implicitly.
Without `--env-file`, configuration comes from process environment variables.
The API binds to loopback port 8000 and fails startup if either data service is unavailable or if the runtime
database role is privileged. Apply the schema first with the migration role (see the
[data model](../docs/architecture/DATA_MODEL.md#migrations)); the persistent local database was migrated to the current revision on 2026-11-05, and a fresh volume is never migrated automatically.
Start the services as described in the [local runbook](../docs/operations/LOCAL_DEVELOPMENT.md).

## Validation

```powershell
uv run --directory backend ruff check .
uv run --directory backend ruff format --check .
uv run --directory backend mypy .
uv run --directory backend pytest -q
```

Tests that need PostgreSQL or Redis are skipped unless `MAYA_TEST_ENV_FILE` (or the `MAYA_TEST_POSTGRES_*` URLs) is set;
`MAYA_REQUIRE_POSTGRES_TESTS=1` turns a missing service into a failure. They create and drop disposable `maya_test_*`
databases and never touch the persistent one. See the [verification record](../docs/testing/AUTH_AND_RLS.md).

The [configuration reference](../docs/guides/CONFIGURATION.md), [health API](../docs/api/HEALTH.md)
and [test strategy](../docs/testing/FOUNDATION.md) describe contracts and failure behavior.
Dependencies are pinned directly and transitively in the uv lockfile.

## Offline Gemini spike tooling

[S3 session replay](../docs/architecture/SPIKE_S3_GEMINI_LIVE.md) and
[S4 embedding analysis](../docs/architecture/SPIKE_S4_EMBEDDINGS.md) run generated
fixtures without credentials, SDK installation or network access. Their `--run`
commands intentionally fail while the owner's hard spending-cap requirement is
unresolved. Offline outputs are not live Gemini evidence.

The [isolated Gemini SDK project](tests/spikes/gemini/README.md) additionally
implements actual typed SDK operations with injected clients and its own locked
dependencies, strict typing and mocked tests. It is not imported by the application
or offline CLIs. No credential factory or runnable live entry point is provided.
