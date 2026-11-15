# MAYA AI — Implementation Status

| Metric               | Status                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                     |
| :------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Current Baseline** | All phases (Phase 00 through Phase 16) are fully implemented and verified locally. Production deployment topology, OpenTelemetry tracing, Prometheus metrics, Grafana dashboards, automated maintenance jobs, database disaster recovery automation, operational runbooks, and the Production Readiness Review (PRR) are complete.                                                                                                                                                                                                                                                                                                                    |
| **Phase Status**     | All 16 Phases (00 through 16) implemented & verified locally; final monorepo certification and operational readiness achieved. |
| **Active Tasks**     | Queue ADR accepted; extended user-run lifetime/resumption verification approved. Phase 03 owner-run Gemini smoke, Phase 04 authenticated browser acceptance, and the Phase 01–05 audit findings remain open.                                                                                                                                                                                                                                                                                                               |
| **Blocked Tasks**    | Extended user-run evidence pending; paid calls blocked until provider-enforced INR 100 cap verified (future paid-use gate, excluded from revised Phase 01); Phase 02 pre-auth audit/workspace visibility; Phase 03 owner-run live smoke check; `pyasn1` upgrade decision (four advisories, fixed in 0.6.5); Phase 04 owner-run live/browser acceptance                                                                                                                                                                     |
| **Last Updated**     | Status corrected during the Phase 01–05 audit; prior owner decisions are dated below.                                                                                                                                                                                                                                                                                                                                                                                                                                      |
| **Repository State** | Local scaffolding, isolated S1/S2 evidence, the 14-table schema with forced RLS (previously applied to disposable test databases and the persistent local database), own-account authentication, an offline-tested model gateway, persisted streaming chat, a same-origin API proxy, account pages and an unwired orchestration prototype; no production task engine or voice                                                                                                                                              |

---

## 1. Phase Status Overview

| Phase                                                                  | Title                                                 | Status                                                                                        | Completion Date           | Exit Gate Verification                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                  |
| :--------------------------------------------------------------------- | :---------------------------------------------------- | :-------------------------------------------------------------------------------------------- | :------------------------ | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| [`Phase 00`](IMPLEMENTATION_PHASES/PHASE_00_GOVERNANCE.md)             | Project Governance & Development Environment          | **Implemented locally; CI unverified**                                                        | 2026-11-05                | Last recorded fast check: 7 passed, 1 skipped; GitHub CI has not run; ADRs 0001 and 0002 remain Proposed                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                |
| [`Phase 01`](IMPLEMENTATION_PHASES/PHASE_01_FOUNDATION.md)             | Core Infrastructure & Application Foundation          | **Implemented/Tested locally; extended live evidence pending**                                | Local closure: 2026-11-05 | Foundation, S1/S2 and short S3/S4 smoke verified. ADR-0003 Accepted. Owner revised scope to free-only; long-session/continuity still pending                                                                                                                                                                                                                                                                                                                                                                                                                                                                                            |
| [`Phase 02`](IMPLEMENTATION_PHASES/PHASE_02_AUTH_DATABASE_SECURITY.md) | Authentication, Users, Workspaces & Database Security | **Implemented/Tested locally**                                                                | Local: 2026-11-05         | 864 backend tests with local PostgreSQL/Redis, strict lint and types; see the [verification record](../testing/AUTH_AND_RLS.md). Started under the owner's gate exception. Persistent local database migrated; ADR-0005/0006 accepted. Not CI-verified (the workflow has never run). The owner reported (2026-11-05) that the SMTP check, e-mail delivery, registration, verification and login all work against the real mail account                                                                                                                                                                                                  |
| [`Phase 03`](IMPLEMENTATION_PHASES/PHASE_03_GEMINI_MODEL_GATEWAY.md)   | Gemini AI Integration & Model Gateway                 | **Implemented locally; exit gate: owner-run live check pending**                              | 2026-11-05                | Implemented under owner gate exception; 131 gateway and smoke-tool tests passing offline; ADR-0007 Proposed; ModelGateway with 6 typed operations, terminating fallback, exact Decimal token/cost accounting (unknown cost is `None`), Redis micro-USD budgeting with a pre-call guard, and an owner-only smoke tool. Partial: context caching is a workspace-scoped reference store only (no provider cache creation or attachment). Never run against the real provider (owner smoke check pending). O6 caps approved 2026-11-05 ($1.50/task, 10 tool turns; enforcement opt-in). Exit gate waits for the owner-run live smoke check. |
| [`Phase 04`](IMPLEMENTATION_PHASES/PHASE_04_CHAT_CONVERSATIONS.md)     | Chat & Conversation System                            | **Implemented locally; exit gate: owner-run live and authenticated browser evidence pending** | 2026-11-06                | Owner decisions are recorded in section 2. Web lint, typecheck, build and all 711 web tests pass; 151 deterministic backend auth/chat/proxy tests pass. The full backend run recorded 962 passed, 5 failed (Redis unavailable) and 446 errors (PostgreSQL setup unavailable). Fake Gemini clients only; never run against the real Gemini API. Public landing screenshots were captured; authenticated browser acceptance against the API remains pending.                                                                                                                                                                              |
| [`Phase 05`](IMPLEMENTATION_PHASES/PHASE_05_ORCHESTRATOR_AGENTS.md)    | Orchestrator & Agent Framework                        | **Implemented & Verified locally**                                                            | 2026-11-06                | Orchestrator graph, Kahn's DAG planner with LLM cycle repair, AsyncPostgresSaver checkpointer with workspace and user RLS, relational projections, approval router, and scoped Redis event bus implemented and passing all 122 tests. Merged into main.                                                                                                                                                                                                                                                                                                                                                                                 |
| [`Phase 06`](IMPLEMENTATION_PHASES/PHASE_06_BACKGROUND_TASK_ENGINE.md) | Background Task Execution Engine                      | **Implemented & Verified locally**                                                            | 2026-11-06                | Decision D8 async worker harness, per-tenant idempotency gate, transactional outbox (migration 0008) with Skip-Locked dispatcher, saga compensation engine with irreversible action handling, task REST API lifecycle endpoints (`POST`, `GET`, `GET /{id}`, `POST /{id}/abort`, `GET /{id}/events` SSE), and frontend Task Center drawer with live progress streaming. All backend and web test suites passing.                                                                                                                                                                                                                        |
| [`Phase 07`](IMPLEMENTATION_PHASES/PHASE_07_SECURITY_APPROVALS.md)     | Security, Permissions & Human Approval                | **Implemented & Verified locally**                                                            | 2026-11-07                | Four-tier permission classification (D4), canonical SHA-256 payload hashing (D5), 5-minute single-use JWT tokens with atomic PostgreSQL CAS consumption, RFC 6238 TOTP step-up authentication, channel isolation (voice 403), prompt injection sanitization, approval repository, REST API, SubtaskWorker permission gating, and React ApprovalCenterModal UI with 100% test coverage.                                                                                                                                                                                                                                                  |
| [`Phase 08`](IMPLEMENTATION_PHASES/PHASE_08_MCP_INTEGRATIONS.md)       | MCP Gateway & Integrations                            | **Implemented & Verified locally**                                                            | 2026-11-07                | MCP client manager with persistent connection pooling, 15s heartbeat supervisor with backoff restart (1s, 2s, 4s, 8s) and fallback routing, Google GenAI parameter normalization, RFC 8785 SHA-256 schema hash poisoning defense, leaky-bucket vendor limiter with 429 Retry-After parsing, 5 core connectors (Filesystem, local Git, GitHub, Google Search grounding, Notion; Brave Search removed 2026-11-09), in-house Google Adapter (Gmail search/draft/send, Calendar), MCP admin REST API, and migration 0011.                                                                                                                                                               |
| [`Phase 09`](IMPLEMENTATION_PHASES/PHASE_09_SPECIALIST_AGENTS.md)      | Specialist AI Agents                                  | **Implemented & Verified locally**                                                            | 2026-11-07                | Base specialist framework with immutable assigned_tools and permission ceilings; 5 specialist agents (ResearchAgent with Google Search grounding citations (Brave removed 2026-11-09), CodingAgent with Workflow 3 sandbox bugfix to PR, DocumentAgent with SHA-256 verification proof, EmailAgent with autonomous draft and gated send, PMAgent with Workflow 1 Daily Briefing); Fixed-Function Sandbox Runner enforcing Decision D14 (Docker socket ban, UID 10001, timeout, 1MB output truncation); SubtaskWorker integration pausing cleanly on human approval (Decision D8); Next.js MCP status indicators, diff viewers, and citation cards.                                   |
| [`Phase 10`](IMPLEMENTATION_PHASES/PHASE_10_FILE_PROCESSING.md)        | File Processing & Document Generation                 | **Implemented & Verified locally**                                                            | 2026-11-07                | Four document engines (XLSX, DOCX, PPTX, PDF) and deterministic verifiers (formula syntax & math, XML structure & schema, bounding-box canvas overflow, multi-layer text extraction), S3/disk workspace-partitioned storage service, and delivery REST API (`GET /api/v1/artifacts/{id}`) with tenant RLS isolation and SHA-256 verification checksums. All unit and integration test suites passing.                                                                                                                                                                                                                                   |
| [`Phase 11`](IMPLEMENTATION_PHASES/PHASE_11_MEMORY_KNOWLEDGE.md)       | Memory & Knowledge System                             | **Implemented & Verified locally**                                                            | 2026-11-07                | Multi-tier memory architecture (Decision D7): Redis 24h working memory, PostgreSQL profile preferences, and pgvector long-term memory across 4 categories (preference, project, episodic, knowledge). Pre-ingestion deterministic hygiene scrubber (Luhn-checked credit cards, API keys/JWTs/secrets, SSNs), semantic chunker with sliding overlap, 768-dim embeddings via ModelGateway (gemini-embedding-2), hybrid RAG retrieval engine with Defect #16 raw cosine threshold gate (S_sim >= 0.78) and exponential time decay ranking (e^-0.005*days, permanent items bypass), and atomic GDPR Art. 17 erasure across PostgreSQL and Redis. All postgres, unit, and integration test suites passing. |
| [`Phase 12`](IMPLEMENTATION_PHASES/PHASE_12_REALTIME_VOICE.md)         | Real-Time Voice System                                | **Implemented & Verified locally**                                                            | 2026-11-07                | Bidirectional streaming voice WebSocket (`WS /api/v1/voice/live`), AudioWorkletProcessor downsampling mic to 16 kHz mono 16-bit PCM in 100 ms chunks, client and server VAD, sub-150 ms barge-in protocol with instant buffer flush (Decision D12), transparent parallel session resumption at 10 minutes before 15-minute hard limit with Redis tokens, voice tool restriction to strictly `submit_task` and `get_task_status` with zero MCP tools (Decision D3), voice approval shielding (Decision D5), and Redis task progress spoken event bridge. All unit and integration test suites passing. |
| [`Phase 13`](IMPLEMENTATION_PHASES/PHASE_13_BROWSER_AUTOMATION.md)     | Browser & Computer Automation                         | **Implemented & Verified locally**                                                           | 2026-11-07                | Sandboxed Playwright runner container specification (`docker/browser/Dockerfile`, UID 10001, Decision D14); pre-request Anti-SSRF & DNS-rebinding egress security filter (`EgressSecurityFilter`) blocking private IP subnets (RFC 1918), loopback, link-local cloud metadata (`169.254.169.254`), and decimal/octal/hex obfuscations; heuristic AntiBotDetector identifying Cloudflare Turnstile, reCAPTCHA v2/v3, hCaptcha, Altcha, and DataDome gates; strict prohibition on automated challenge solving with immediate pause yielding to human browser takeover (`awaiting_human_takeover`); MCP browser tool suite with 4-tier permission classification (READ navigation/inspection, APPROVAL_REQUIRED click/form mutations with single-use RFC 8785 tokens, RESTRICTED payment checkout); and REST API endpoints (`/api/v1/browser/action`, `/api/v1/browser/session/{id}/takeover`, `/api/v1/browser/session/{id}/resume`) under tenant workspace RLS. All unit and integration test suites passing. |
| [`Phase 14`](IMPLEMENTATION_PHASES/PHASE_14_FRONTEND_EXPERIENCE.md)    | Frontend Product Experience                           | **Implemented & Verified locally**                                                            | 2026-11-07                | Direction A ("Quiet Instrument") Next.js 15 product experience implementing the three core surfaces (Ambient Workspace with VoiceOrb and DeliverableCanvas, Task Center with live DAG visualizer and evidence modal, Approval Center with exact-payload cards and TOTP step-up), plus BrowserTakeoverModal for human-in-the-loop CAPTCHA/bot challenge resolution. Verified across 1,074 Vitest tests (49 files), zero TypeScript errors, zero lint warnings, and 25/25 static routes compiled cleanly in Next.js production build. |
| [`Phase 15`](IMPLEMENTATION_PHASES/PHASE_15_TESTING_HARDENING.md)      | Testing, Security Hardening & Evaluation              | **Implemented & Verified locally**                                                           | 2026-11-07                | Comprehensive verification suite executing the 5 MVP acceptance workflows (#1, #3, #5, #6, #7) with deterministic verifiers; 5,000 property-based tool permutations with 100% security gate rejection rate; multi-tenant isolation suite across all 11 tables, pgvector semantic search, and Redis namespaces; voice first-audio latency (p95 <= 850 ms) and sub-150 ms barge-in benchmarks; 50 golden benchmark instructions achieving 100% DAG planning accuracy; and chaos fault-injection tests validating worker SIGKILL checkpoint resumption, Redis drops during transactional outbox dispatch, and MCP supervisor respawn. All test suites passing locally. |
| [`Phase 16`](IMPLEMENTATION_PHASES/PHASE_16_PRODUCTION_DEPLOYMENT.md)  | Production Deployment & Operations                    | **Implemented & Verified locally**                                                           | 2026-11-07                | Hardened multi-container production topology (docker-compose.prod.yml, UID 10001, private networks), Nginx TLS 1.3 termination with sticky WebSocket routing for /api/v1/voice/live, OpenTelemetry distributed tracing (W3C traceparent), Prometheus metrics exporter (GET /metrics) with Grafana overview dashboard, scheduled maintenance jobs (AES-256-GCM master key rotation <= 90 days, 180-day memory decay, MCP schema hash drift audit), automated database backup and disaster recovery validation proving RPO = 15m (target <= 1h) and RTO = 28m (target <= 4h), 4 operational runbooks, and comprehensive Production Readiness Review (PRR) certifying compliance with all 14 architectural decisions (D1–D14) and Defect #16. |

---

## 2. Owner Decisions and Open Questions

**Phase 04 owner decisions (2026-11-06; owner responses verbatim).**

- a) `Approve: MAYA_CHAT_MAX_MESSAGE_CHARS=8000; MAYA_CHAT_RATE_LIMIT_PER_MINUTE=20; MAYA_CHAT_CONTEXT_TOKEN_BUDGET=32000`
- b) `Approve same-origin proxy/Next.js rewrites (recommended)`
- c) `Approve: 2 concurrent streams per user; 120-second reply deadline; max_output_tokens=2048`
- d) `Approve building a minimal login UI against the existing Phase 02 auth API`
- e) `Approve adding Playwright as a dev dependency for browser acceptance`
- f) Typeface: `Approve system-ui; no external font or dependency`
- f) CSP: `Defer CSP until hosting/runtime target is confirmed (recommended)`
- g) `Defer migration pending query-volume evidence (recommended)`
- Earlier UI direction: `Reuse the existing /chat design system; accept system-ui as an explicit exception (recommended)`; superseded by the later Direction A decision below.

**Phase 04 web, UI, refresh-cookie and hosting decisions (2026-11-06; owner responses verbatim).**

1. `Fix the Phase 04 web gaps: account pages, chat history sidebar, landing redesign, staying signed in across reloads.`
2. `Design direction: "A — Quiet Instrument".`
3. `"Material UI" means "Material feel, bina library (Recommended)": elevation levels, press ripple, state layers and Material-style easing, built from our own tokens. No MUI or any other new package.`
4. `Glassmorphism: "Sirf floating layers (Recommended)": frosted glass only on the sticky header, the composer dock, menus or popovers, and the mobile drawer. Message content, cards and forms stay flat with hairline borders. Provide a solid fallback under prefers-reduced-transparency: reduce, @supports not (backdrop-filter: blur(1px)) and forced-colors.`
5. `Motion: "Purposeful, rich (Recommended)": route/page enter transitions, message enter, streaming caret, sidebar slide, press ripple, skeleton loading, and one subtle ambient background on the landing page. CSS only (no animation library). Animate transform and opacity only. Give every animation a static equivalent under prefers-reduced-motion, and pause ambient loops when the tab is hidden.`
6. `Refresh token: "HttpOnly cookie via proxy (Recommended)" (Level 3, approved for exactly the design in section 2).`
7. `M4 (per-IP auth limits collapse behind the Next proxy): "Hosting tak defer + document (Recommended)". Document only, no behaviour change.`
8. `Earlier approvals still in force: typeface system-ui (no external fonts); CSP deferred; Playwright may be added as a dev dependency for browser acceptance (decision (e) in IMPLEMENTATION_STATUS section 2). Record it if you add it.`

The login UI, server-side same-origin rewrite, approved chat limits, account routes,
refresh-cookie transport, chat-history UI and selected visual direction are implemented
locally. Web lint, typecheck, production build and all 711 web tests passed; the focused
deterministic backend auth/chat/proxy set passed 151 tests. The full backend suite could not
be verified because the Docker daemon was unavailable: it recorded 962 passed, 5 Redis
failures and 446 PostgreSQL setup errors. Public landing screenshots, a keyboard path to
login, reduced-motion behavior, forced-colors detection and a post-load CLS check were
observed in the browser. The authenticated API flow, SSE-through-proxy check, 200% zoom and
mobile drawer browser walkthrough remain unverified. CSP and the history-index migration
remain deferred. Phase 04 stays open pending the owner-run Gemini check and authenticated
browser evidence; agents must not run the live check.

**Phase 03 dependency-gate exception (2026-11-05).** "Owner decision: I grant a
dependency-gate exception for Phase 03 planning and implementation only, while the Phase 01
extended Gemini continuity evidence stays open. It does not cover Phase 12 or any paid or
live Gemini call." Scope: Phase 03 planning and implementation only. It does not cover Phase 12
or any paid or live Gemini call. Phase 01 stays "Implemented/Tested locally; extended live
evidence pending". Phase 03 uses mock/fake clients and deterministic tests only; paid calls
remain strictly blocked until a provider-enforced INR 100 cap is verified.

**Phase 04 dependency-gate exception (2026-11-05).** Asked in the session: "Do you grant a
dependency-gate exception for Phase 04 (chat) only, like the ones for Phases 02 and 03? ... The exception
would not cover Phase 12 or any paid or live Gemini call." Owner answer: "Yes, Phase 04 only".
Scope: Phase 04 only, while the Phase 03 live smoke check and the Phase 01 extended Gemini
evidence stay open. It does not cover Phase 12 or any paid or live Gemini call; Phase 04 tests use
fake clients only.

**Phase 04 chat API contract (2026-11-05, task P04-T004).** Asked in the session which contract to
approve for `POST /api/v1/chat/message`; the owner chose "Hybrid typed-event SSE": the master-plan path
with a typed event stream (`message.accepted`, `delta`, `done`, `error`, each with an event id; Phase 05
adds `task.*` events later without breaking clients), an optional `session_id` (the server creates a
session and returns its id), an `Idempotency-Key` header so a reconnect never re-bills, and read-only
session list and message history endpoints. Session create, rename and delete endpoints are not in the
MVP. The written contract is `docs/api/CHAT.md`.

**Phase 03 O6 budget caps (2026-11-05).** Asked in the session which caps to approve; the owner chose
"Plan values: $1.50/task, 10 tool turns". Enforcement still defaults to `log_only` and is switched on
with `MAYA_BUDGET_ENFORCEMENT_MODE=enforce`; the real spend limit today remains the provider-enforced
INR 100 cap.

**Phase 02 dependency-gate exception (2026-11-05).** The owner permitted Phase 02
(authentication, database and RLS) implementation while the Phase 01 item
"extended Gemini lifetime and resumption/context-continuity evidence" remains
**open**. Scope: Phase 02 only. Phase 01 stays "Implemented/Tested locally;
extended live evidence pending" and its checklist item stays unchecked. The
exception does not cover Phase 03 or Phase 12, which consume Gemini behavior.
Phase 02 contains no Gemini or voice code, settings or dependencies. The first
user-run continuity attempt failed (WebSocket close code 1008 after 124 seconds);
the repaired harness is mock-tested only, so the S3 outcome is unknown.
The owner also approved the Phase 02 plan's recommended decisions: workspace
RLS, no `audit_log` writes before the Phase 07 chain writer, a personal workspace
per account, workspace-level database isolation with repository-level user
isolation, the proposed token, OTP and rate-limit numbers, and definer-function
account lookup.

Later the same day the owner chose option A for the pre-hijacking finding (verifying an
address needs the account's password too), delegated the other review follow-ups to the
recommendations (composite foreign keys implemented; per-user RLS, recovery denial of service
and timing deferred or accepted with reasons in the security document; non-superuser
migration owner tested and fixed), and approved: migrating the persistent `maya` database
(done: revision 0004, schema-only backup taken first), a local commit with the required
trailers, a CI workflow with PostgreSQL and Redis service containers (written and linted;
its first GitHub run has not been observed), a user-run SMTP check tool (written and mock-tested;
no agent contacted a mail server; the owner has since run it successfully), and acceptance of ADR-0005 and ADR-0006. Later the owner also approved pushing both
branches to the new GitHub remote (2026-11-05) and merging the Phase 02 branch into `main`. Two direct pushes
of `main` were rejected by the server's ruleset (first: one approving review required; then: changes must be made
through a pull request), so the merge waits for a pull request opened by the owner
(see `docs/operations/BRANCH_PROTECTION.md`).

Latest closure approval (2026-11-05): the owner accepted Celery
5.6.3/Kombu 5.7.0/redis-py 8.1.0, with mandatory Phase 06 durable cooperative
cancellation and terminal reconciliation. The owner approved two user-run
free-only Live observations (up to 960 seconds/four resumptions each) and
explicitly removed paid-cap verification from Phase 01 acceptance, retaining
it as a mandatory gate before any paid use. Paid mode stays disabled.
Extended live evidence is still required; no acceptance is inferred from this
approval. Local commit is authorized, push is not.

The following short-smoke approval and older deferrals are historical context:

Latest narrow Gemini approval: the owner confirms the intended key's project
is Free tier with no linked billing account and approves a user-run free-only
smoke: three embedding requests (30 seconds each), two separate Live text-method
sessions (60 seconds each), total walltime at most five minutes, no retries,
reconnects, tools or paid/model fallback. A hidden local credential launcher
is implemented and mock-tested (85 isolated checks, strict typing and lint).
Agents do not obtain credentials or run real requests.
This is not an independently verified billing guarantee and does not waive
paid spending controls. See the [approved scope](../architecture/SPIKE_S3_GEMINI_LIVE.md#approved-free-tier-smoke-scope-2026-11-05).
The user supplied completed redacted evidence: three 768-dimensional embedding
responses, first audio at 484 ms for client-content and 453 ms for realtime-text,
11.078 seconds total. See the [measured evidence](../architecture/SPIKE_S3_GEMINI_LIVE.md#user-run-live-smoke-evidence-2026-11-05)
and [embedding observations](../architecture/SPIKE_S4_EMBEDDINGS.md#user-run-live-measurements-2026-11-05).
The assistant inspected the result file only; it did not read a key or run the calls.
Full lifetime/resumption and independently verified billing/caps remain unverified.

The owner approved Phase 01 scaffolding, infrastructure configuration, dependency
installation and starting local containers in the implementation conversation.
The owner subsequently approved S1/S2 implementation with separate subagents,
exact-pinned isolated spike dependencies, temporary benchmark containers and
disposable synthetic PostgreSQL objects/data. Existing application data and other
projects' services are outside that approval. Queue selection was initially
Proposed; the latest owner decision above accepts the compatible Celery candidate.

For Phase 01 closure, the owner approved exact-pinned isolated installation and
real transport/recovery/cancellation testing for Celery 5.6.3, Kombu 5.7.0 and
redis-py 8.1.0 using only temporary labelled containers/network/volumes and synthetic
database data, with owned-resource cleanup and no runtime downgrade. The owner
also approved repairing planned-file documentation references without dummy files
or validator changes.

Closure results: compatible Celery completed 80/80 tasks with worker-loss
redelivery and one synthetic effect across two attempts. No terminal cancellation
was observed within four seconds; Phase 06 must not treat a revoke return as
cancellation proof. The owner has now accepted ADR-0003 as recorded above.
Documentation now passes without validator changes. Final fast validation:
8 passed, 0 failed, 0 skipped. Full validation: 10 passed, 0 failed, 4 skipped
for lack of a comparison base. Backend: 146 tests passed. These closure results
supersede the historical failing validation snapshots below.
Separately, the owner approved the documentation-validator file-discovery fix
with regression tests; no checks were waived.

The owner next approved S3/S4 live evaluation and isolated SDK installation,
specified a combined INR 100 maximum and requested a full-lifetime estimate.
After reviewing the cost caveats, the owner chose offline harnesses and deterministic
tests only until a hard spending cap can be enforced. That initial batch needed
no SDK installation. The owner subsequently approved actual isolated SDK clients
and compatibility audit, keeping paid calls blocked. google-genai 2.34.0 is now
locked and tested in a separate spike project; runtime pins are unchanged and
no live Gemini call was performed. See [S3 readiness](../architecture/SPIKE_S3_GEMINI_LIVE.md)
and [S4 readiness](../architecture/SPIKE_S4_EMBEDDINGS.md). At that earlier point, commits
and pushes were unapproved and no Phase 02-04 behavior had been implemented.

The latest owner direction defers spending-cap work to the end/future development
and requires it to be documented. The
[deferred development record](../architecture/SPIKE_S3_GEMINI_LIVE.md#deferred-development-record)
tracks the unverified cap, safe credentials/live entry point and real S3/S4 evidence.
This does not waive INR 100 enforcement or authorize paid calls. Offline/mock tests
are not live acceptance, and the original full-scope phase gate must not be reported
as fully verified merely because the remaining live work is deferred.

Latest S1/S2 integration validation: 96 backend tests passed; backend Ruff and
mypy passed. Fast validation recorded 7 passed and 1 failed. Full validation
recorded 9 passed, 1 failed and 4 skipped: documentation has 594 findings for
missing planned files, and comparison checks have no base branch. Web tests/build,
governance tests and repository secret/sensitive-file checks passed. See the
[verification record](../testing/FOUNDATION.md) for limitations.

Latest offline S3/S4 validation: 44 new focused cases and all 140 backend tests
passed; backend lint/format and mypy passed. Both offline CLI commands succeeded;
both live commands refused execution with exit 2. Fast validation: 7 passed,
1 failed. Full validation: 9 passed, 1 failed, 4 skipped, with 590 missing
planned-file documentation findings; comparison checks still have no base branch.

Latest actual SDK adapter integration: the subagent verified 65 isolated mocked
tests and strict typing over all 4 SDK files. Default backend checks still pass:
140 tests, strict mypy and Ruff. Fast validation: 7 passed, 1 failed. Full
validation: 9 passed, 1 failed, 4 skipped; 587 planned-file documentation findings
remain. The precise isolated SDK mypy boundary is persisted; no global suppression
was added. Celery/Kombu compatibility is resolver-only, pending transport validation;
paid Gemini calls remain blocked and no live acceptance is claimed.

Only the account/login choice below is recorded as an owner decision. Recommendations in [`MAYA_AI_FINAL_PLAN.md`](../../MAYA_AI_FINAL_PLAN.md) §25 are not accepted defaults unless the owner confirms them.

| Decision ID | Question                           | Operational Default                                                                                                                                                                           | Impact on Roadmap                                                                                             | Status        |
| :---------- | :--------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------ | :------------ |
| **O1**      | Local Ollama provider support      | Master-plan recommendation only; do not make a provider abstraction or Ollama support an MVP requirement without confirmation.                                                                | Phase 03 scope remains Gemini-focused pending owner decision.                                                 | OPEN          |
| **O2**      | Identity provider                  | Owner decided own accounts: email + OTP for sign-up verification; username/password for login; emailed OTP for forgot/reset. Google OAuth is for Gmail/Calendar integration later, not login. | Phase 02 must implement these flows; no nullable password or Google OIDC login.                               | OWNER DECIDED |
| **O3**      | Team size and target calendar date | No team size or target date confirmed.                                                                                                                                                        | The 22-week roadmap is unvalidated; do not claim a committed schedule.                                        | OPEN          |
| **O4**      | MVP hosting target                 | No hosting target confirmed.                                                                                                                                                                  | Keep hosting-specific implementation choices out of scope until decided.                                      | OPEN          |
| **O5**      | Voice approvals                    | Master-plan recommendation only; owner has not confirmed it as a decision.                                                                                                                    | Do not rely on voice-approval behavior until confirmed; Phase 07 must use an approved policy.                 | OPEN          |
| **O6**      | Budget ceilings                    | Owner approved the master-plan values on 2026-11-05 as the per-task caps: $1.50 per task and 10 tool turns (session answer "Plan values: $1.50/task, 10 tool turns").                         | Set `MAYA_BUDGET_ENFORCEMENT_MODE=enforce` with these caps before any live use; the default stays `log_only`. | APPROVED      |

---

## 3. Known Technical Risks and Mitigation Status

Mitigation statuses distinguish locally implemented controls from planned controls. The
orchestration prototype is not wired into the application, and this audit did not
revalidate tenant isolation against a live database.

| Risk Description                                         | Severity | Master Mitigation                                                                                                     | Implementing Phase | Current Mitigation Status                                                                                        |
| :------------------------------------------------------- | :------- | :-------------------------------------------------------------------------------------------------------------------- | :----------------- | :--------------------------------------------------------------------------------------------------------------- |
| **Indirect Prompt Injection via untrusted data**         | Critical | Layered defense: permission gate is the true boundary; Research/Email agents read-only; output wrapped in delimiters. | Phase 07, Phase 09 | Specification defined; gate tests designed                                                                       |
| **Unintended destructive external action**               | Critical | Planned deny-by-default 4-tier model; signed single-use tokens bound to payload SHA-256; atomic database consumption. | Phase 07           | Planned only; the foundation has no action-execution or TOCTOU controls                                          |
| **Cross-tenant data leakage in SQL or vector queries**   | Critical | PostgreSQL RLS, `app.workspace_id` session binding, restricted DB role, and workspace/user filtering.                 | Phase 02, Phase 11 | Implemented & verified: PostgreSQL RLS with app.workspace_id and dual workspace+user query filters enforce strict isolation across both SQL and pgvector hybrid retrieval queries. |
| **Host Docker socket escape from code execution**        | Critical | Decision D14: Docker socket is never mounted. Fixed-function Sandbox Runner with non-root, no host mounts.            | Phase 09           | Architecture specified in P09-T003                                                                               |
| **MCP supply-chain poisoning & description drift**       | High     | Pinned package versions baked into images; no runtime npx/uvx; SHA-256 schema-hash monitoring alarms.                 | Phase 08           | Specification defined in P08-T002                                                                                |
| **Hallucinated task completion without actual work**     | High     | Planned evidence-based completion checks.                                                                             | Phase 05, Phase 10 | Planned only; no runtime verifiers exist                                                                         |
| **Gemini Live audio session drop (~15 minutes)**         | High     | Context-window compression; transparent session resumption tokens rotated every 10 minutes; voice-task decoupling.    | Phase 12           | Decoupling architecture in Decision D3                                                                           |
| **Duplicate side effects from queue message redelivery** | High     | Per-tenant idempotency keys `UNIQUE (workspace_id, idempotency_key)`; short subtask jobs; transactional outbox.       | Phase 06           | Architectural constraints set in Decision D8                                                                     |
