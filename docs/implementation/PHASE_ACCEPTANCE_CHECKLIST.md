# MAYA AI — Phase Acceptance Checklist and Exit Gates

## 1. Global Acceptance Criteria (Applied to Every Task and Phase)

Before any task or phase is marked `COMPLETED`, the following conditions must be objectively proven:

- [ ] **Functionality:** Implements the exact requirements specified in the phase document without adding unrequested features, refactoring unrelated files, or violating the principle of smallest correct change.
- [ ] **Test Results:** Automated tests accompany the implementation. No tests are skipped, disabled, or weakened. Positive, negative, boundary, and error cases are covered.
- [ ] **Security Enforcement:** No plaintext secrets or tokens on disk, in code, or in logs. For controls implemented in the phase, positive and negative tests prove the required isolation and authorization; unbuilt controls remain explicitly planned.
- [ ] **Documentation Synchronization:** Every functional change updates its corresponding documentation, API specifications, and architecture records within the same change set.
- [ ] **Code Hygiene & Syntax:** Clean execution of code linters and static typecheckers (`ruff check`, `mypy`, `npm run lint`, `npm run typecheck`). Zero syntax or typing errors.
- [ ] **Zero Banned Placeholders:** Documentation passes `check-docs.mjs` with zero occurrences of banned placeholder strings (`TODO`, `TBD`, `FIXME`, `XXX`, `PLACEHOLDER`, `lorem ipsum`).
- [ ] **Clean Validation:** Fast validation (`npm run validate:fast`) exits with code 0 across all checks.
- [ ] **Deterministic Evidence:** Task completion is certified by automated tool output, exit codes, HTTP 2xx receipts, or cryptographic file hashes. Model prose alone is never accepted as proof.

---

## 2. Phase-Specific Completion Gates

### Phase 00 — Project Governance & Development Environment

- [x] Canonical instructions in [`AGENTS.md`](../../AGENTS.md) strictly under 24,000 bytes.
- [x] Repository-level native git hooks (`commit-msg`, `pre-commit`, `pre-push`) installed and active.
- [x] Agent file generator produces 59 synchronized wrappers with zero drift (`npm run agents:check`).
- [x] Node.js validation harness executes all fast checks in under 10 seconds.
- [ ] CI workflows are configured with security checks; GitHub CI execution remains unverified.
- [x] Baseline validation report recorded; it is not signed.

The last recorded local fast check reported 7 passed and 1 skipped. ADRs 0001 and 0002 remain Proposed.

### Phase 01 — Core Infrastructure & Application Foundation

- [x] Compose configuration validates for PostgreSQL 17 (pgvector and `pg_trgm`) and Redis 7.4 with AOF; local container startup was separately approved and verified.
- [x] Liveness and readiness probes have positive and dependency-failure tests; readiness does not disclose dependency internals.
- [x] Backend and Next.js 15 scaffolds pass the repository-detected lint, type, test, and build commands.
- [x] Spike S1 has reproducible measured reports and owner-accepted ADR-0003: Celery 5.6.3/Kombu 5.7.0/redis-py 8.1.0 passed an isolated 80-task transport/recovery run. Terminal cancellation was not observed within four seconds; durable cooperative cancellation and terminal reconciliation are mandatory for Phase 06.
- [x] Spike S2 measures filtered HNSW behavior with both workspace and user filters; supported scan modes, exact/ANN plan checks and corrected measured results are documented in the S2 report without an unapproved target.
- [x] S3/S4 offline fixture harnesses have deterministic positive/negative tests and explicit blocked-live CLI behavior; no provider measurements are claimed.
- [x] Actual S3/S4 SDK operations have a separately locked environment and strict mocked tests; the separately approved free-tier launcher uses hidden local key input without a runtime SDK dependency.
- [x] User-run free-tier smoke returned three 768-dimensional vectors and audio from both text methods in separate sessions (first audio 484/453 ms); redacted evidence and its hash are documented. No independent billing verification or broad semantic-quality claim is implied.
- [x] Owner explicitly revised Phase 01 to Free-tier-only with no linked billing. Paid mode remains disabled; the INR 100 provider-enforced cap is still mandatory before any future paid use, not claimed verified.
- [ ] Full S3 bounded lifetime observations and real resumption/context continuity evidence from the extended user-run experiment. The short smoke does not pass these criteria.
- [x] Object storage is not part of this phase; MinIO is excluded.

**Local scope implemented and tested; live scope owner-deferred.** Documentation
passes with no findings, fast validation records 8 passed/0 failed/0 skipped,
and full validation records 10 passed/0 failed/4 skipped. Comparison checks lack
a base branch; skipped is not passed. All 146 backend tests pass. The unchecked
live item remains unverified, so this is not full original-scope acceptance.

### Phase 02 — Authentication, Users, Workspaces & Database Security

Evidence for the items below is the [verification record](../testing/AUTH_AND_RLS.md): 864 backend tests passed with local PostgreSQL and Redis, strict lint and types clean; the persistent local database was migrated and the real API probed against it. Not verified in GitHub CI (no run has been observed yet). Real SMTP delivery and the account flows were verified by the owner (reported 2026-11-05, not agent-observed).

- [x] Alembic migration creates the schema in [`MAYA_AI_FINAL_PLAN.md`](../../MAYA_AI_FINAL_PLAN.md) §14 plus the owner-required OTP challenge storage; table count and RLS coverage are explicitly verified (the plan has 12 listed tables, 11 tenant-scoped; the implementation has 14 tables, all with forced RLS: the plan's 12 plus `auth_otp_challenges` and `auth_sessions`).
- [x] Row-Level Security policies active and forced across all tenant tables via `app.workspace_id` (transaction-local; restricted non-owner `maya_app` role; startup guard refuses a privileged runtime role).
- [x] Workspace-name and slug visibility is explicitly decided: `workspaces` also has RLS, so tenants cannot list each other ([ADR-0006](../adr/0006-tenant-isolation-with-row-level-security.md), Proposed).
- [x] Cross-tenant tests cover the workspace boundary for every table (database-enforced) and the user boundary for the authentication tables (repository-enforced; the database does not separate users inside a workspace, see ADR-0006); unauthorized queries return no protected rows.
- [x] Own-account registration, email OTP verification, username/password login, email-OTP password recovery/reset, refresh, and logout flows match the owner decisions.
- [x] OTP codes are HMAC-stored, short-lived, attempt-limited, single-use, invalidated on resend, rate-limited, never logged, and responses do not reveal whether an email exists (one deliberate exception: `403 email_not_verified` after a correct password).
- [x] Password reset revokes all sessions; email events without a workspace have an explicitly designed audit representation: structured, identifier-free log events now; durable `audit_log` rows from the Phase 07 chain writer.
- [x] Plan-defined `audit_log` table scope is verified; workspace-less pre-authentication events are not recorded in the database (log-only) until the Phase 07 writer exists.
- [x] The security review's findings are dispositioned in the [security document](../security/AUTHENTICATION.md#decisions-on-the-reviews-remaining-findings-owner-2026-11-05): pre-hijacking fixed (verification needs the password), composite foreign keys implemented, migrator privileges tested; ADR-0005 and ADR-0006 accepted.
- [x] Owner's run of the SMTP check tool against the real mail account and manual registration, verification and login through the running API (reported by the owner on 2026-11-05).
- [ ] First GitHub CI run of the updated workflow observed (the remote exists since 2026-11-05; the result has not been seen, and the `dependency-review` job may need GitHub Advanced Security on a private repository).

Phase 02 does not implement the secrets vault or audit-chain enforcement. Their owning phase must be reconciled before those controls are scheduled.

### Phase 03 — Gemini AI Integration & Model Gateway

- [ ] MVP aliases are configured and startup availability checks do not fail on post-MVP aliases; SDK model names are normalized as required.
- [ ] Simulated 429 errors trigger full jitter exponential backoff before fallback.
- [ ] Simulated 5xx errors trigger immediate failover to designated secondary model.
- [ ] Malformed structured outputs trigger exactly one self-repair turn before failover.
- [ ] Gateway contract covers text, structured output, streaming, tool calls, embeddings, token counting, and mid-stream errors. Budget enforcement uses only owner-approved limits.
- [ ] Usage accounting includes thinking and tool-use tokens and correctly handles cached-token pricing.
- [ ] Fallback traversal terminates and cannot loop; context-cache references are scoped by workspace.
- [ ] Deterministic tests use mocks; live Gemini checks are separate and require external-service approval.

### Phase 04 — Chat and Conversation System

- [ ] Authenticated basic chat persists user and assistant messages under RLS.
- [ ] Owner approves the contract for `/api/v1/chat/message`, including whether SSE emits task events or model tokens; no unapproved session CRUD endpoints are added.
- [ ] Browser streaming uses fetch with bearer authentication, stable event/message IDs, and a client idempotency key so reconnects do not resubmit or rebill a message.
- [ ] Context-window limits are based on the approved model/token budget, not an invented turn count.
- [ ] Markdown is sanitized at render time and remote images are blocked.
- [ ] Client disconnection terminates upstream model generation immediately to conserve tokens.
- [ ] Browser/UI acceptance is not claimed until an authenticated UI path exists.

### Master-plan P1 — Foundation milestone

- [ ] Implementation Phases 01–04 all pass their gates; registration/login, persisted streaming chat, cross-tenant isolation, and all four spike reports are evidenced.

### Phase 05 — Orchestrator and Agent Framework

- [ ] LangGraph `StateGraph` compiles cleanly using `AgentExecutionState` and `SubTask` Pydantic models.
- [ ] DAG planner decomposes natural language instructions into cycle-free dependency graphs.
- [ ] Cycle detection catches circular loops 100% of the time and triggers self-repair.
- [ ] State persists transactionally to PostgreSQL checkpointer via `AsyncPostgresSaver`.
- [ ] Relational `tasks` and `subtasks` tables project execution states accurately from checkpoints.
- [ ] Graph execution pauses deterministically at interrupt points before approval-required nodes.

### Phase 06 — Background Task Execution Engine

- [ ] Background workers process subtask jobs asynchronously across distributed queue pools.
- [ ] Submissions with duplicate `(workspace_id, idempotency_key)` return existing tasks without re-execution.
- [ ] Transactional outbox commits state and events atomically, relaying cleanly to Redis Pub/Sub.
- [ ] Workers adhere to short jobs rule; workers never block on humans and exit upon hitting `awaiting_approval`.
- [ ] `POST /api/v1/tasks/{id}/abort` terminates worker execution and executes registered saga compensations.

### Phase 07 — Security, Permissions & Human Approval

- [ ] Four permission tiers (READ, WRITE, APPROVAL_REQUIRED, RESTRICTED) enforced by central gate.
- [ ] Deny-by-default policy automatically disables unclassified tools; server hints cannot lower tiers.
- [ ] Single-use approval tokens (JWT, exp <= 5 min) consume atomically with TOCTOU hash re-verification.
- [ ] Voice channel origin header calling approval endpoints is rejected with HTTP 403 (Decision D5).
- [ ] RESTRICTED tier actions enforce verified TOTP code or password re-entry.
- [ ] Prompt sanitizer wraps external tool output in `<<<UNTRUSTED_EXTERNAL_INPUT>>>` delimiters.

### Phase 08 — MCP Gateway & Integrations

- [ ] MCP Client Manager maintains connection pools for stdio subprocesses and Streamable HTTP.
- [ ] JSON-RPC ping every 15s detects unhealthy servers; crashed processes restart with backoff.
- [ ] Schema-hash drift detection alerts and disables servers if tool descriptions change unexpectedly.
- [ ] Core MVP servers (Filesystem in `/artifacts`, local Git, GitHub, Brave Search, Notion read-only) operational.
- [ ] In-house Google adapter successfully searches and drafts emails via OAuth 2.0 without exposing raw credentials.

### Phase 09 — Specialist AI Agents

- [ ] Research Agent searches Brave and synthesizes structured findings with source citations.
- [ ] Fixed-function Sandbox Runner executes commands in ephemeral containers without host Docker socket.
- [ ] Coding Agent replicates bug, applies patch, passes sandbox tests, and pauses for PR approval (Workflow 3).
- [ ] Document Agent generates deliverables strictly within `/artifacts` with verification proofs.
- [ ] Email Agent drafts messages autonomously (WRITE) but gates message transmission on approval (APPROVAL_REQUIRED).
- [ ] PM Agent aggregates Calendar and Notion sprint data into read-only daily agenda (Workflow 1).

### Phase 10 — File Processing & Document Generation

- [ ] XLSX Engine generates workbooks; programmatic verifier detects formula errors (`#REF!`, `#VALUE!`).
- [ ] DOCX Engine generates formatted reports; XML verifier validates paragraph and heading hierarchies.
- [ ] PPTX Engine generates 8-slide presentation; bounding-box verifier confirms zero text overflow outside 16:9 canvas.
- [ ] PDF Engine compiles HTML/CSS; verifier confirms page count > 0, embedded fonts, and extractable text.
- [ ] Workflows 5, 6, and 7 generate verified deliverables; files stream to S3 with recorded SHA-256 hashes.
- [ ] `GET /api/v1/artifacts/{id}` delivers verified files with RLS authorization checks.

### Phase 11 — Memory & Knowledge System

- [ ] Deterministic PII scrubber redacts credit cards, bearer tokens, and government IDs prior to storage.
- [ ] 768-dimensional embeddings generated via `gemini-embedding-2` and stored in pgvector.
- [ ] Hybrid search combines dense cosine similarity (0.7) and GIN `tsvector` full-text search (0.3).
- [ ] Raw similarity threshold (0.78) enforced before time-decay ranking (`lambda = 0.005/day`) is applied.
- [ ] `DELETE /api/v1/memory/purge` erases 100% of target user memories complying with GDPR Article 17.

### Phase 12 — Real-Time Voice System

- [ ] Next.js `AudioWorklet` captures 16 kHz 16-bit PCM; Silero VAD suppresses silence transmission.
- [ ] WebSocket bridge connects client audio bidirectionally to Gemini Live API (`gemini-3.8-live`).
- [ ] First-audio response latency benchmark achieves target p95 <= 850 ms.
- [ ] Barge-in flushes audio playback queue in <= 150 ms upon user speech detection.
- [ ] Continuous 20-minute voice session survives 10-minute session rotation transparently.
- [ ] Voice model tools strictly limited to `submit_task` and `get_task_status`; spoken approval attempts rejected.

### Phase 13 — Browser & Computer Automation

- [ ] Browser sandbox runs headless Chromium and `@playwright/mcp` as non-root user in isolated container.
- [ ] Egress proxy blocks private IP subnets and cloud metadata endpoints (`169.254.169.254`).
- [ ] CAPTCHA detection triggers automatic pause and human handoff without attempting bypass.
- [ ] Browser navigation is READ; form submissions require human approval; checkout is RESTRICTED.

### Phase 14 — Frontend Product Experience

- [ ] Ambient Workspace renders Voice Waveform Orb reacting dynamically to microphone frequencies.
- [ ] Deliverable Canvas previews XLSX, PDF, DOCX, and Git diffs side-by-side with conversation.
- [ ] Task Center renders interactive DAG graph with live status updates from SSE event stream.
- [ ] Approval Center renders exact payload diff cards and enforces TOTP for RESTRICTED operations.
- [ ] Playwright end-to-end tests pass across desktop and mobile viewports.

### Phase 15 — Testing, Security Hardening & Evaluation

- [ ] 5 MVP acceptance workflows (#1, #3, #5, #6, #7) pass end-to-end with zero unhandled exceptions.
- [ ] Safety gate property testing (5,000 permutations) proves 100% enforcement of approval requirements.
- [ ] Automated cross-tenant isolation test suite runs and passes cleanly in CI.
- [ ] Real-time voice latency benchmarks confirm p95 <= 850 ms and barge-in <= 150 ms.
- [ ] Chaos fault-injection tests prove automatic recovery from worker, Redis, and MCP subprocess termination.

### Phase 16 — Production Deployment & Operations

- [ ] Production Docker Compose and Kubernetes manifests deploy with non-root security contexts.
- [ ] TLS reverse proxy terminates HTTPS/WSS and enforces sticky session routing for WebSockets.
- [ ] OpenTelemetry distributed tracing exports spans across FastAPI, LangGraph, and MCP calls.
- [ ] Prometheus scrapes metrics and Grafana renders spend, latency, and queue depth dashboards.
- [ ] Maintenance cron jobs for AES-256 key rotation and memory decay execute cleanly.
- [ ] Disaster recovery drill proves Recovery Point Objective (RPO <= 1h) and Recovery Time Objective (RTO <= 4h).
- [ ] All 4 operational runbooks verified and Production Readiness Review (PRR) signed.
