# MAYA AI — Implementation Status

| Metric               | Status                                                                                                                                   |
| :------------------- | :--------------------------------------------------------------------------------------------------------------------------------------- |
| **Current Baseline** | Phase 00 governance, Phase 01 local foundation, and Phase 02 schema/RLS/authentication implemented locally on branch `feat/phase-02-auth-database-security` (committed locally, not pushed) |
| **Phase Status**     | Phase 00: locally implemented; GitHub CI unverified · Phase 01: local scope implemented/tested; extended free-tier live evidence pending · Phase 02: implemented and tested locally under the owner's gate exception; CI workflow never run; real SMTP delivery and the account flows owner-verified (reported) |
| **Active Tasks** | Queue ADR accepted; extended user-run lifetime/resumption verification approved. Short Gemini smoke completed |
| **Blocked Tasks**    | Extended user-run evidence pending; paid calls blocked until provider-enforced INR 100 cap verified (future paid-use gate, excluded from revised Phase 01); Phase 02 pre-auth audit/workspace visibility; Phase 03 O6 budget caps; Phase 04 chat contract |
| **Last Updated**     | 2026-11-05 (Phase 02 implementation)                                                                                                     |
| **Repository State** | Local scaffolding, isolated S1/S2 evidence, the 14-table schema with forced RLS (applied to disposable test databases and the persistent local database) and own-account authentication; no model gateway, chat, agents or voice |

---

## 1. Phase Status Overview

| Phase                                                                  | Title                                                 | Status                                 | Completion Date | Exit Gate Verification                                                                                   |
| :--------------------------------------------------------------------- | :---------------------------------------------------- | :------------------------------------- | :-------------- | :------------------------------------------------------------------------------------------------------- |
| [`Phase 00`](IMPLEMENTATION_PHASES/PHASE_00_GOVERNANCE.md)             | Project Governance & Development Environment          | **Implemented locally; CI unverified** | 2026-11-05      | Last recorded fast check: 7 passed, 1 skipped; GitHub CI has not run; ADRs 0001 and 0002 remain Proposed |
| [`Phase 01`](IMPLEMENTATION_PHASES/PHASE_01_FOUNDATION.md) | Core Infrastructure & Application Foundation | **Implemented/Tested locally; extended live evidence pending** | Local closure: 2026-11-05 | Foundation, S1/S2 and short S3/S4 smoke verified. ADR-0003 Accepted. Owner revised scope to free-only; long-session/continuity still pending |
| [`Phase 02`](IMPLEMENTATION_PHASES/PHASE_02_AUTH_DATABASE_SECURITY.md) | Authentication, Users, Workspaces & Database Security | **Implemented/Tested locally** | Local: 2026-11-05 | 864 backend tests with local PostgreSQL/Redis, strict lint and types; see the [verification record](../testing/AUTH_AND_RLS.md). Started under the owner's gate exception. Persistent local database migrated; ADR-0005/0006 accepted. Not CI-verified (the workflow has never run). The owner reported (2026-11-05) that the SMTP check, e-mail delivery, registration, verification and login all work against the real mail account |
| [`Phase 03`](IMPLEMENTATION_PHASES/PHASE_03_GEMINI_MODEL_GATEWAY.md)   | Gemini AI Integration & Model Gateway                 | **NOT_STARTED**                        | Projected       | Dependent on Phase 01                                                                                    |
| [`Phase 04`](IMPLEMENTATION_PHASES/PHASE_04_CHAT_CONVERSATIONS.md)     | Chat & Conversation System                            | **NOT_STARTED**                        | Projected       | Dependent on Phase 02, 03; public SSE/session contract requires owner decision                           |
| [`Phase 05`](IMPLEMENTATION_PHASES/PHASE_05_ORCHESTRATOR_AGENTS.md)    | Orchestrator & Agent Framework                        | **NOT_STARTED**                        | Projected       | Dependent on Phase 02, 03                                                                                |
| [`Phase 06`](IMPLEMENTATION_PHASES/PHASE_06_BACKGROUND_TASK_ENGINE.md) | Background Task Execution Engine                      | **NOT_STARTED**                        | Projected       | Dependent on Phase 02, 05                                                                                |
| [`Phase 07`](IMPLEMENTATION_PHASES/PHASE_07_SECURITY_APPROVALS.md)     | Security, Permissions & Human Approval                | **NOT_STARTED**                        | Projected       | Dependent on Phase 02, 06                                                                                |
| [`Phase 08`](IMPLEMENTATION_PHASES/PHASE_08_MCP_INTEGRATIONS.md)       | MCP Gateway & Integrations                            | **NOT_STARTED**                        | Projected       | Dependent on Phase 07                                                                                    |
| [`Phase 09`](IMPLEMENTATION_PHASES/PHASE_09_SPECIALIST_AGENTS.md)      | Specialist AI Agents                                  | **NOT_STARTED**                        | Projected       | Dependent on Phase 07, 08                                                                                |
| [`Phase 10`](IMPLEMENTATION_PHASES/PHASE_10_FILE_PROCESSING.md)        | File Processing & Document Generation                 | **NOT_STARTED**                        | Projected       | Dependent on Phase 02, 09                                                                                |
| [`Phase 11`](IMPLEMENTATION_PHASES/PHASE_11_MEMORY_KNOWLEDGE.md)       | Memory & Knowledge System                             | **NOT_STARTED**                        | Projected       | Dependent on Phase 02, 03                                                                                |
| [`Phase 12`](IMPLEMENTATION_PHASES/PHASE_12_REALTIME_VOICE.md)         | Real-Time Voice System                                | **NOT_STARTED**                        | Projected       | Dependent on Phase 03, 05, 06                                                                            |
| [`Phase 13`](IMPLEMENTATION_PHASES/PHASE_13_BROWSER_AUTOMATION.md)     | Browser & Computer Automation                         | **NOT_STARTED**                        | Projected       | Dependent on Phase 07, 08                                                                                |
| [`Phase 14`](IMPLEMENTATION_PHASES/PHASE_14_FRONTEND_EXPERIENCE.md)    | Frontend Product Experience                           | **NOT_STARTED**                        | Projected       | Dependent on Phase 04, 06, 07, 10, 12                                                                    |
| [`Phase 15`](IMPLEMENTATION_PHASES/PHASE_15_TESTING_HARDENING.md)      | Testing, Security Hardening & Evaluation              | **NOT_STARTED**                        | Projected       | Dependent on Phases 01–14                                                                                |
| [`Phase 16`](IMPLEMENTATION_PHASES/PHASE_16_PRODUCTION_DEPLOYMENT.md)  | Production Deployment & Operations                    | **NOT_STARTED**                        | Projected       | Dependent on Phase 15                                                                                    |

---

## 2. Owner Decisions and Open Questions

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
branches to the new GitHub remote (2026-11-05) and, after GitHub reported that branch protection cannot be
enforced on a private repository on the current plan, chose to stay private and merge the Phase 02 branch into
`main` by fast-forward (see `docs/operations/BRANCH_PROTECTION.md`).

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
and [S4 readiness](../architecture/SPIKE_S4_EMBEDDINGS.md). Commits and pushes remain
unapproved. No Phase 02-04 behavior has been implemented.

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

| Decision ID | Question                           | Operational Default                                                                                                                                                                           | Impact on Roadmap                                                                             | Status        |
| :---------- | :--------------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :-------------------------------------------------------------------------------------------- | :------------ |
| **O1**      | Local Ollama provider support      | Master-plan recommendation only; do not make a provider abstraction or Ollama support an MVP requirement without confirmation.                                                                | Phase 03 scope remains Gemini-focused pending owner decision.                                 | OPEN          |
| **O2**      | Identity provider                  | Owner decided own accounts: email + OTP for sign-up verification; username/password for login; emailed OTP for forgot/reset. Google OAuth is for Gmail/Calendar integration later, not login. | Phase 02 must implement these flows; no nullable password or Google OIDC login.               | OWNER DECIDED |
| **O3**      | Team size and target calendar date | No team size or target date confirmed.                                                                                                                                                        | The 22-week roadmap is unvalidated; do not claim a committed schedule.                        | OPEN          |
| **O4**      | MVP hosting target                 | No hosting target confirmed.                                                                                                                                                                  | Keep hosting-specific implementation choices out of scope until decided.                      | OPEN          |
| **O5**      | Voice approvals                    | Master-plan recommendation only; owner has not confirmed it as a decision.                                                                                                                    | Do not rely on voice-approval behavior until confirmed; Phase 07 must use an approved policy. | OPEN          |
| **O6**      | Budget ceilings                    | Master-plan values ($1.50 per task and 10 tool turns) are proposals, not owner-approved caps.                                                                                                 | Phase 03 may instrument usage, but enforcement thresholds require owner confirmation.         | OPEN          |

---

## 3. Known Technical Risks and Mitigation Status

Mitigation statuses below describe specifications or plans, not deployed product controls.
The implemented foundation does not include tenant isolation or agent execution.

| Risk Description                                         | Severity | Master Mitigation                                                                                                     | Implementing Phase | Current Mitigation Status                                                     |
| :------------------------------------------------------- | :------- | :-------------------------------------------------------------------------------------------------------------------- | :----------------- | :---------------------------------------------------------------------------- |
| **Indirect Prompt Injection via untrusted data**         | Critical | Layered defense: permission gate is the true boundary; Research/Email agents read-only; output wrapped in delimiters. | Phase 07, Phase 09 | Specification defined; gate tests designed                                    |
| **Unintended destructive external action** | Critical | Planned deny-by-default 4-tier model; signed single-use tokens bound to payload SHA-256; atomic database consumption. | Phase 07 | Planned only; the foundation has no action-execution or TOCTOU controls |
| **Cross-tenant data leakage in SQL or vector queries**   | Critical | Planned PostgreSQL RLS, `app.workspace_id` session binding, restricted DB role, and workspace/user filtering.         | Phase 02, Phase 11 | Planned only; DDL and RLS are not implemented                                 |
| **Host Docker socket escape from code execution**        | Critical | Decision D14: Docker socket is never mounted. Fixed-function Sandbox Runner with non-root, no host mounts.            | Phase 09           | Architecture specified in P09-T003                                            |
| **MCP supply-chain poisoning & description drift**       | High     | Pinned package versions baked into images; no runtime npx/uvx; SHA-256 schema-hash monitoring alarms.                 | Phase 08           | Specification defined in P08-T002                                             |
| **Hallucinated task completion without actual work**     | High     | Planned evidence-based completion checks.                                                                             | Phase 05, Phase 10 | Planned only; no runtime verifiers exist                                      |
| **Gemini Live audio session drop (~15 minutes)**         | High     | Context-window compression; transparent session resumption tokens rotated every 10 minutes; voice-task decoupling.    | Phase 12           | Decoupling architecture in Decision D3                                        |
| **Duplicate side effects from queue message redelivery** | High     | Per-tenant idempotency keys `UNIQUE (workspace_id, idempotency_key)`; short subtask jobs; transactional outbox.       | Phase 06           | Architectural constraints set in Decision D8                                  |
