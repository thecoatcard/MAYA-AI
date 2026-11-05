# MAYA API foundation

Implemented: typed configuration, JSON logs with generated request/trace IDs,
async PostgreSQL/Redis pools, startup/shutdown cleanup and public health probes.
Authentication, tenant tables, RLS, migrations, model calls and agents are not implemented.

## Run locally

From the repository root, with Python 3.12 and uv installed:

```powershell
uv sync --directory backend --locked
uv run --directory backend python -m app --env-file ..\.env.example
```

Only use the example file for local development. No dotenv file is loaded implicitly.
Without `--env-file`, configuration comes from process environment variables.
The API binds to loopback port 8000 and fails startup if either data service is unavailable.
Start the services as described in the [local runbook](../docs/operations/LOCAL_DEVELOPMENT.md).

## Validation

```powershell
uv run --directory backend ruff check .
uv run --directory backend ruff format --check .
uv run --directory backend mypy .
uv run --directory backend pytest -q
```

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
