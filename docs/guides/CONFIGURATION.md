# Configuration reference

## Application environment variables

The local foundation reads typed settings from process variables. Dotenv loading is
explicit: use `python -m app --env-file` to select a file. Agents use only the root
example, never private dotenv files. Compose uses its own explicit `--env-file`.

When code starts reading an environment variable:

1. Add it to `.env.example` with a safe placeholder value.
2. Add a row to the table below.
3. Run `npm run docs:check`. It fails if source code reads a variable that is missing from either place, or if `.env.example` lists a variable that is missing here.

| Variable | Purpose | Required | Example value |
| :--- | :--- | :--- | :--- |
| `MAYA_DATABASE_URL` | Required asyncpg URL for the non-admin runtime login | Yes | Root example, loopback port 15432 |
| `MAYA_REDIS_URL` | Redis connection URL without query/fragment overrides | Yes | `redis://127.0.0.1:16379/0` |
| `MAYA_MIGRATION_DATABASE_URL` | Separate migration-role URL, unused by the API | No | Root example; role must differ from runtime |
| `MAYA_PROBE_TIMEOUT_SECONDS` | Combined readiness timeout and pool/connect timeout | No; defaults to 2, greater than 0 and at most 30 | `2` |
| `POSTGRES_DB` | Compose development database | Compose | `maya` |
| `POSTGRES_USER` | Compose bootstrap administrator, not the API login | Compose | `maya_migrator` |
| `POSTGRES_PASSWORD` | Bootstrap password, only a non-secret local example is tracked | Compose | Set privately outside local testing |
| `POSTGRES_APP_PASSWORD` | Runtime login password used at first initialization | Compose | Set privately outside local testing |
| `POSTGRES_PORT` | Loopback host port for PostgreSQL | No | `15432` |
| `REDIS_PORT` | Loopback host port for Redis | No | `16379` |

Never put real secrets in `.env.example` or in this document. Real values live in an untracked `.env` file or a secret manager.

Settings reject missing/invalid connection URLs and hide inputs in validation errors.
Connection strings are `SecretStr` values; the runtime never uses migration credentials.
The local [runbook](../operations/LOCAL_DEVELOPMENT.md) explains first initialization,
password changes and safe shutdown. Gemini and SMTP settings are not consumed yet.

The documentation validator discovers environment-variable reads in tracked and
non-ignored source files. Generated build output and dependency environments ignored
by Git are not application sources. Tracked files remain checked even when an ignore
pattern matches them. Outside Git, directory-based scanning remains available.

## Governance configuration

`governance/governance.config.json` is the machine-readable policy that the validators read. It is a guardrail file: changing it needs explicit human approval.

| Section | Controls |
| :--- | :--- |
| `requiredFiles` | Files that must exist; `docs:check` fails if one is missing |
| `instructionFiles` | Size limit for `AGENTS.md` (24,000 bytes, the Antigravity truncation limit) |
| `agentWrappers` | Name prefixes and the hard-rules markers used by the agent-file generator |
| `markdown` | Which folders are documentation, and the unfinished-work patterns that are rejected |
| `modules` | Where module directories live; each needs a README or a page under `docs/modules/` |
| `env` | Source globs scanned for environment variable use, and names that need no documentation |
| `sensitive` | Files that must never be committed, and the guardrail paths that need a `Human-Approval:` trailer |
| `commits` | Allowed Conventional Commit types and header length |
| `changeRules` | Diff-based documentation, ADR and changelog requirements |
| `stacks` | How application projects are detected and which lint, type, test and build commands run |

## Tooling and CI variables

The governance scripts read no environment variables. In CI, the workflow passes the comparison base ref to `validate.mjs` as an argument. Settings that are not variables: `core.hooksPath` (set by `npm run setup`) and the repository's branch protection (`docs/operations/BRANCH_PROTECTION.md`).
