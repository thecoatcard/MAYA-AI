# Foundation verification

## Deterministic suite

Backend pytest tests run without PostgreSQL, Redis, credentials or provider calls.
They cover required and malformed settings, explicit dotenv loading, role separation,
bounded timeouts, redacted logs, generated correlation IDs, generic readiness failures,
startup cleanup, database/Redis error mapping, cancellation and unexpected-error propagation.
Integration-labelled tests wire API components with mocked data-service resources;
they are not live database isolation tests.

Vitest/Testing Library renders the web overview in jsdom and verifies honest planned
states and real navigation targets. TypeScript, ESLint, Prettier and Next.js build checks
run separately. Component tests do not prove browser hydration or backend connectivity.

```powershell
uv run --directory backend pytest -q
npm --prefix apps\web test
```

Both suites were initially run before their corresponding implementation files existed
and failed on the missing imports. No existing tests were weakened or disabled.

## Local service smoke checks

The [runbook](../operations/LOCAL_DEVELOPMENT.md) contains opt-in commands for approved
local PostgreSQL/Redis checks. Verify extension versions, restricted runtime-role flags,
AOF, live HTTP health responses and a real dependency outage followed by recovery.
Do not include these checks in the deterministic test suite.

## Remaining Phase 01 work

The [filtered-vector S2 benchmark](../architecture/SPIKE_S2_VECTOR.md) and
[S1 queue evaluation](../architecture/SPIKE_S1_QUEUE.md) are implemented and measured
in isolated disposable infrastructure.
[S3](../architecture/SPIKE_S3_GEMINI_LIVE.md) and
[S4](../architecture/SPIKE_S4_EMBEDDINGS.md) now have offline fixture harnesses and
isolated actual SDK adapters validated with fake clients. Live measurements and
credential-safe live entry-point wiring remain pending: the owner's
latest scope blocks live calls until a hard cap within INR 100 can be enforced.
No latency, recall or cost acceptance target has been invented.

S2 regression tests cover independent ANN/exact SQL, disabled statement caching,
plan/direct-fetch row-count consistency and both tenant predicates in the ANN
post-index filter. The original perfect-recall result was invalid and is superseded
by the corrected measured run, not retained as evidence.

S1 compares real Celery and ARQ workers running a shared async LangGraph workload,
including worker termination/redelivery, cooperative abort behavior, event-loop lag,
and container memory. Measurements are a single bounded synthetic run. Queue SDKs
remain isolated from the application environment due to their Redis client constraints;
the application dependency versions were not downgraded.

## Initial scaffold verification (2026-11-05, before S1/S2)

| Check | Result |
| :--- | :--- |
| Backend pytest | 33 passed; lint, format and strict mypy passed |
| Web Vitest | 2 passed; ESLint, Prettier, TypeScript and production build passed |
| Governance unit tests | 37 passed; the file-discovery regression failed before its fix |
| Complete validation | 9 passed, 1 failed, 4 skipped: 599 missing future-file references in planning docs; no base branch for comparison checks |
| Secret/sensitive-file checks | Passed; no real credential files were read |
| Web production dependency audit | 0 vulnerabilities; full audit still reports 6 moderate development-tool findings in the TypeScript ESLint/minimatch chain |
| Python dependency consistency | All 40 installed packages compatible; this is not a Python vulnerability audit |
| Fresh PostgreSQL bootstrap | Temporary tmpfs container created vector 0.8.7, pg_trgm 1.6 and the restricted runtime role; test container was removed |
| Live API/data flow | Readiness 200, Redis outage 503 with generic body, liveness still 200, recovery readiness 200 |
| Browser | Production overview rendered, three planned cards and roadmap anchor verified; final console had 0 errors/warnings and no horizontal overflow at mobile width 390 |

The missing future-file count increased when creating actual backend/web directories
activated validation of existing planning references into those directories. These
findings were not hidden or waived. The generated-output environment-scan bug was
fixed with separately approved regression-tested source discovery.

## S1/S2 integration verification (2026-11-05)

| Check | Result |
| :--- | :--- |
| Backend pytest | 96 passed, including deterministic S1/S2 regressions |
| Backend Ruff lint/format and mypy | Passed; mypy checked 26 source files |
| Isolated S1 checks | Subagent verified 37 focused tests, strict mypy over 7 files, Ruff and Docker-layout imports |
| Fast validation | 7 passed, 1 failed, 0 skipped |
| Full validation | 9 passed, 1 failed, 4 skipped; 594 documentation findings for missing planned files; comparison checks had no base branch |
| Web and governance | Web tests/build and governance tests passed through full validation |
| Repository secrets/sensitive files | Passed through full validation |
| Cleanup | Spike-owned disposable containers, data volumes and temporary environments removed; unrelated services untouched |

Only the isolated S1 driver and worker runtime are excluded from application mypy
discovery; their separate environment supplies queue dependencies and strict checks.
Host-side orchestration, metrics and deterministic tests remain in application checks.
No application dependency was downgraded and no broad typing suppression was added.
The benchmark runs were not repeated for package-boundary-only fixes.
The later isolated Gemini project has its own discovery exclusion and strict checks;
offline host tools remain included in application typing.

## S3/S4 offline verification (2026-11-05)

The new tests first failed collection on the two missing harness modules.
After implementation, 44 focused cases and all 140 backend tests passed.
Backend Ruff lint/format and strict mypy passed (30 source files).
Both offline CLI entry points emitted labelled fixture-only JSON.
Tests assert `--run` exits 2 with an explicit spending-cap blocker.
Both actual `--run` command entry points were also invoked and refused execution
with exit 2, without credential access.

S3 cases cover isolated method plans, known PCM format/duration, first-audio
arithmetic, resumption ordering, provider termination versus observation timeout,
invalid timestamps and incomplete events. Events are normalized fixtures, not SDK
messages; real connection behavior and context continuity remain untested.

S4 cases cover SDK keyword spelling, response cardinality, exact dimensions,
nonfinite/nonnumeric/boolean values, zero norms, float32 overflow/roundoff, raw
norm preservation and cosine arithmetic. These do not prove model behavior.
No SDK was installed and no credentials, private environment files, model calls
or user data were used. No new ADR is needed: application architecture is unchanged.

Fast validation recorded 7 passed, 1 failed and 0 skipped. Full validation recorded
9 passed, 1 failed and 4 skipped: 590 missing planned-file documentation findings
remain; the four comparison checks have no base branch. Stack tests/build,
governance tests and repository secret/sensitive-file checks passed.

## Phase 01 compatibility audit (2026-11-05)

| Area | Verified evidence | What it does not prove |
| :--- | :--- | :--- |
| Installed backend | `uv pip check --python backend\.venv\Scripts\python.exe`: all 40 installed packages compatible | Vulnerability status or live-service readiness |
| Infrastructure | `docker compose --env-file .env.example config --quiet` passed without loading private dotenv files | No containers were started by this audit |
| API/database | Existing code uses a factory-mounted router, separate roles, asyncpg, pool size 20, overflow 10 and pre-ping | Tenant schema/RLS remain Phase 02 work |
| Web | Manifest retains exact Next.js 15/React 19 pins and configured stack checks | Does not deliver authenticated chat |
| Queue candidate | Celery 5.6.3 + Kombu 5.7.0 + redis-py 8.1.0 resolved with current backend pins and LangGraph 1.2.20 | New transport/cancellation/recovery behavior is untested |
| ARQ | ARQ 0.28.0 + redis-py 8.1.0 failed resolution, as expected | No runtime client downgrade was attempted |
| Planned SDK/checkpointer | Adding google-genai 2.34.0 and langgraph-checkpoint-postgres 3.2.0 to that resolver input succeeded (73 packages) | No runtime gateway/checkpointer was installed or implemented |

The public metadata for google-genai 2.34.0 requires Python >=3.11 and
Pydantic >=2.12.5,<3; the runtime's Python 3.12 and Pydantic 2.14.2 satisfy
those constraints. The future Postgres checkpointer requires psycopg >=3.2.0
and psycopg-pool >=3.2.0; asyncpg is not a substitute. Its resolver-selected
psycopg 3.3.8 and psycopg-pool 3.3.3 are observations, not new runtime pins.

See the [S1 follow-up](../architecture/SPIKE_S1_QUEUE.md#compatibility-follow-up)
for exact queue resolution inputs, source metadata and pending approvals.
No application dependency, infrastructure, migration, guardrail or accepted ADR
changed in this audit. Actual SDK clients are developed separately from the
application environment and remain subject to the live spending-cap blocker.

## Actual Gemini SDK adapter integration (2026-11-05)

The [isolated project](../../backend/tests/spikes/gemini/README.md) pins
google-genai 2.34.0 and has its own strict checks. The implementing subagent ran
65 mocked tests, including asyncio-debug/warnings-as-errors, strict mypy over all
4 adapter/check files and Ruff lint/format. Real SDK configuration and response
models were used; client constructors, credential acquisition and HTTP sends
were forbidden by test fixtures. No live request or real client construction occurred.

Negative cases cover connection/send/receive/cleanup failures, cancellation,
invalid audio/vectors, missing setup, stale handle invalidation, bounded reconnects
and distinguishing cleanup failure from observation expiry. S4 sends one text per
request rather than relying on Embedding 2's aggregated list behavior.
Context continuity, session lifetime and model output remain live-only gaps.

After persisting the precise isolated-project mypy discovery boundary, the
orchestrator ran default backend checks: 140 tests passed, strict mypy passed
for 30 host files, and Ruff lint/format passed. Both actual `--run` entry points
still exited 2 before SDK/credential access. Fast validation: 7 passed, 1 failed.
Full validation: 9 passed, 1 failed, 4 skipped, with 587 missing planned-file
documentation findings and no base for comparison checks. Web tests/build,
governance tests and repository secret/sensitive-file checks passed.

The isolated environment/caches were removed by the subagent after validation;
the exact manifest, lock, source and checks persist. Application dependencies,
API/database contracts and infrastructure are unchanged. No accepted architecture
decision, paid execution, commit or push was introduced.

## Phase 01 closure: live local-service recheck (2026-11-05)

The orchestrator repeated the approved local smoke checks using only
`.env.example`, existing MAYA foundation containers/volumes and a temporary
loopback API on port 18091:

- Compose configuration and installed runtime consistency checks passed.
- Both services became healthy. PostgreSQL reported vector 0.8.7 and pg_trgm 1.6;
  `maya_app` had no superuser, database/role creation, replication or RLS-bypass flags.
- Redis reported `appendonly=yes`.
- Real liveness and readiness returned 200 with their generic bodies and a
  server-generated request ID.
- Stopping only the foundation Redis container produced readiness 503 with
  `{"status":"unavailable"}`, while liveness remained 200. Restarting that
  container restored readiness 200. The outage body was verified with curl
  after PowerShell's exception response stream returned an empty body.
- The temporary API was stopped and its port checked for no listener. Both
  foundation containers were restored to their previously stopped state; named
  volumes were preserved. Other projects' services were untouched.

This demonstrates local dependency behavior, not authentication, tenant
isolation, production readiness or real Gemini behavior. The owner deferred
Gemini cap enforcement and live S3/S4 evidence to the end/future development;
see the [deferred development record](../architecture/SPIKE_S3_GEMINI_LIVE.md#deferred-development-record).

## Final local Phase 01 closure (2026-11-05)

The compatible Celery subagent measured 80/80 tasks completed at 22.79 tasks/s,
p50 1,505.50 ms and p95 2,831.45 ms. Worker-loss redelivery completed attempt 2
in 6,986.865 ms, with one synthetic effect across two attempts. Native revoke
returned but no terminal cancellation/worker acknowledgment was observed within
four seconds. This is a negative cancellation finding, not success.
Peak driver-container memory including worker children was 252,077,670 bytes.
See the [S1 report](../architecture/SPIKE_S1_QUEUE.md) for the versioned candidate
lock, raw evidence, cleanup and single-run limitations.

The subagent verified 43 focused S1 tests, isolated candidate typing (3 files)
and historical S1 typing (4 files). Two non-fatal pytest-asyncio configuration
warnings occurred in the isolated S1 test environment. All run-labelled candidate
containers, volumes, network and candidate image were removed; original lock
and measured results were preserved. Runtime dependency pins are unchanged.

The documentation agent corrected 587 future-artifact references in 15 phase
documents, retaining their paths/requirements and labelling them planned.
No validator was changed and no dummy target files were created.

The orchestrator independently verified:

| Command | Final result |
| :--- | :--- |
| `uv run --directory backend --no-sync pytest -q` | 146 passed |
| `npm run validate:fast` with backend tools on PATH | 8 passed, 0 failed, 0 skipped |
| `npm run validate` with backend tools on PATH | 10 passed, 0 failed, 4 skipped |
| Documentation within validation | 134 Markdown files and all documentation rules passed |
| Stack lint/typecheck and tests/build | Passed, including backend and web |
| Governance unit tests | 37 passed |
| Repository secrets/sensitive files | Passed |
| `git diff --check` | Passed |

The four full-validation skips require a comparison base branch and were not
executed. GitHub CI and accepted ADR decisions are not implied. Local scope is
implemented/tested; real S3/S4 verification and spending controls remain explicitly
owner-deferred. This does not claim that every original phase acceptance test
passed or authorize paid Gemini calls, queue adoption, commits or pushes.

## Free-tier user-run launcher readiness (2026-11-05)

The implementing subagent verified 85 isolated Gemini checks, strict mypy over
5 files and Ruff lint/format, including CLI help/noninteractive refusal subprocess
tests. The launcher uses hidden local key input only after explicit free/no-billing
confirmations; mocked tests cover bounded calls, failures, redacted output and
absence of retries/reconnects/fallback. No credentials were accessed and no Gemini
request occurred. Free/no-billing status is user-attested, not independently verified.

The subagent ran full validation: 10 passed, 0 failed, 4 no-base skips. The
orchestrator independently ran fast validation: 8 passed, 0 failed, 0 skipped.
The isolated environment was removed after checks; recreate it from the pinned
lock using the [user-run commands](../../backend/tests/spikes/gemini/README.md#user-run-free-tier-only-smoke).
At that checkpoint, evidence awaited user execution; the subsequent result is below.

## User-executed Gemini smoke evidence (2026-11-05)

The user returned a completed redacted result from the hidden-prompt launcher.
The assistant inspected only that JSON and fingerprinted it; no credential was
read and no model call was made by the assistant.

The smoke completed in 11.078 seconds with google-genai 2.34.0. All three
`gemini-embedding-2` responses had 768 dimensions and raw norms near 1.0.
Separate `gemini-3.8-live` sessions completed one audio turn each:
client-content first audio at 484 ms, realtime-text at 453 ms.
See the [S3 measured report](../architecture/SPIKE_S3_GEMINI_LIVE.md#user-run-live-smoke-evidence-2026-11-05)
for the source filename/hash and the
[S4 measurements](../architecture/SPIKE_S4_EMBEDDINGS.md#user-run-live-measurements-2026-11-05)
for norms, cosine scores and local conversion error.

This is one successful bounded user-run experiment, not statistical performance,
audio-quality assessment, independent billing verification or paid budget enforcement.
The unchanged source result has `live_acceptance_verified=false`; full lifetime
and resumption/context continuity were deliberately not exercised. No new API
request or dependency installation was needed for this documentation update.

## Closure review and owner decisions (2026-11-05)

The owner accepted [ADR-0003](../adr/0003-task-queue-selection.md) for the
compatible Celery candidate, with durable cooperative cancellation and terminal
reconciliation required in Phase 06. No runtime queue dependency was added.
The owner revised Phase 01 acceptance to Free-tier-only/no linked billing;
the provider-enforced INR 100 gate remains mandatory before future paid use.
Two extended user-run sessions (up to 960 seconds and four resumptions each)
are approved; their lifetime/continuity evidence remains pending.

Before the extended launcher changes, the orchestrator reran:

| Command | Observed result |
| :--- | :--- |
| `npm run validate` with backend environment on PATH | 10 passed, 0 failed, 4 skipped: no comparison base |
| `uv run --directory backend --no-sync pytest -q` | 146 passed |
| `git diff --check` | Passed |

This is a pre-change checkpoint, not validation of the extended launcher.
Neither approval nor mocked tests replace user-run live evidence. A local
commit was authorized; no push, paid API request or billing change was approved.

After the compatibility correction to AUDIO plus output transcription, the
orchestrator independently ran the isolated Gemini checks: 111 passed, and the
extended launcher's help command passed. The implementing agent also reported
strict mypy for six files and Ruff lint/format passing. Full repository validation
with backend tools on PATH passed 10 checks, failed none, and skipped four
comparison checks before staging. Extended real-user evidence remains pending.
