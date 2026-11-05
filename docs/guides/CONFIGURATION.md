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
| `MAYA_JWT_SIGNING_KEY` | HS256 key for access and refresh tokens; at least 32 bytes, different from the OTP key | Yes | Public local example in the root example only |
| `MAYA_OTP_HMAC_KEY` | Server-side key for one-time-code HMACs and rate-limit key hashing; at least 32 bytes | Yes | Public local example in the root example only |
| `MAYA_ACCESS_TOKEN_TTL_SECONDS` | Access-token lifetime | No; defaults to 900, between 60 and 3600 | `900` |
| `MAYA_REFRESH_TOKEN_TTL_SECONDS` | Sliding refresh-token lifetime | No; defaults to 604800 (7 days), between 3600 and 2592000 | `604800` |
| `MAYA_REFRESH_ABSOLUTE_TTL_SECONDS` | Hard session lifetime from login; must not be below the refresh lifetime | No; defaults to 2592000 (30 days), at most 7776000 | `2592000` |
| `MAYA_OTP_RESEND_COOLDOWN_SECONDS` | Minimum time between one-time codes for the same account and purpose | No; defaults to 60, between 1 and 3600 | `60` |
| `MAYA_SMTP_HOST` | SMTP server for one-time-code e-mail; unset disables e-mail and the e-mail endpoints answer 503 | No; needs the sender address | Placeholder host in the root example (commented) |
| `MAYA_SMTP_PORT` | SMTP port | No; defaults to 587 | `587` |
| `MAYA_SMTP_SECURITY` | `starttls` or `ssl`; plaintext SMTP is not supported | No; defaults to `starttls` | `starttls` |
| `MAYA_SMTP_USERNAME` | SMTP login; set together with the password | No | Set privately |
| `MAYA_SMTP_PASSWORD` | SMTP password or app password; never logged | No | Set privately |
| `MAYA_SMTP_FROM_ADDRESS` | Sender address; set together with the host | No | Placeholder in the root example (commented) |
| `POSTGRES_DB` | Compose development database | Compose | `maya` |
| `POSTGRES_USER` | Compose bootstrap administrator, not the API login | Compose | `maya_migrator` |
| `POSTGRES_PASSWORD` | Bootstrap password, only a non-secret local example is tracked | Compose | Set privately outside local testing |
| `POSTGRES_APP_PASSWORD` | Runtime login password used at first initialization | Compose | Set privately outside local testing |
| `POSTGRES_PORT` | Loopback host port for PostgreSQL | No | `15432` |
| `REDIS_PORT` | Loopback host port for Redis | No | `16379` |

Never put real secrets in `.env.example` or in this document. Real values live in an untracked `.env` file or a secret manager.

Settings reject missing/invalid connection URLs and hide inputs in validation errors.
Connection strings, the two keys and the SMTP password are `SecretStr` values; the runtime never uses migration
credentials. The local [runbook](../operations/LOCAL_DEVELOPMENT.md) explains first initialization,
password changes and safe shutdown. Gemini settings are not consumed yet. The keys in the root example are public
placeholders for loopback use; use private random values (at least 32 bytes each, different from each other) anywhere else.
Without `MAYA_SMTP_HOST` and `MAYA_SMTP_FROM_ADDRESS` the application starts, and the endpoints that must send a code answer 503. The owner can check a real mail account with the [SMTP check tool](../../backend/app/tools/README.md).
Test-only variables (`MAYA_TEST_ENV_FILE`, `MAYA_TEST_POSTGRES_ADMIN_URL`, `MAYA_TEST_POSTGRES_APP_URL`,
`MAYA_TEST_REDIS_URL`, `MAYA_REQUIRE_POSTGRES_TESTS`) are described in the [verification record](../testing/AUTH_AND_RLS.md).

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
