# MAYA AI — Implementation Phases Directory

This directory contains the operational, phase-by-phase implementation specifications for MAYA AI. Each specification translates the architectural requirements of [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) into concrete, bite-sized, independently testable tasks designed for autonomous execution by AI coding agents and human engineers.

## 1. Directory Index

| Phase Document                                                             | Phase Title                                           | Primary Scope                                                                                                                                                 | Status                             |
| :------------------------------------------------------------------------- | :---------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------ | :--------------------------------- |
| [`PHASE_00_GOVERNANCE.md`](PHASE_00_GOVERNANCE.md)                         | Project Governance & Development Environment          | Canonical rules, pre-commit/pre-push hooks, agent sync, linters, CI checks                                                                                    | Implemented locally; CI unverified |
| [`PHASE_01_FOUNDATION.md`](PHASE_01_FOUNDATION.md) | Core Infrastructure & Application Foundation | Foundation and S1/S2 measured; real SDK clients mock-tested; live Gemini owner-deferred | Implemented/Tested locally; original live acceptance deferred |
| [`PHASE_02_AUTH_DATABASE_SECURITY.md`](PHASE_02_AUTH_DATABASE_SECURITY.md) | Authentication, Users, Workspaces & Database Security | Multi-tenant schema and RLS, own-account authentication, OTP flows                                                                                            | NOT_STARTED                        |
| [`PHASE_03_GEMINI_MODEL_GATEWAY.md`](PHASE_03_GEMINI_MODEL_GATEWAY.md)     | Gemini AI Integration & Model Gateway                 | Model role aliases, startup discovery, bounded retries/fallbacks, usage metering, owner-approved budgets                                                      | NOT_STARTED                        |
| [`PHASE_04_CHAT_CONVERSATIONS.md`](PHASE_04_CHAT_CONVERSATIONS.md)         | Chat & Conversation System                            | Persisted messages and authenticated streaming, subject to owner-approved API/session contract                                                                | NOT_STARTED                        |
| [`PHASE_05_ORCHESTRATOR_AGENTS.md`](PHASE_05_ORCHESTRATOR_AGENTS.md)       | Orchestrator & Agent Framework                        | LangGraph `StateGraph`, DAG planning, cycle detection, PostgreSQL checkpointer, subtask projections, state transitions                                        | NOT_STARTED                        |
| [`PHASE_06_BACKGROUND_TASK_ENGINE.md`](PHASE_06_BACKGROUND_TASK_ENGINE.md) | Background Task Execution Engine                      | Queue worker runtime (ARQ/Celery spike resolution), transactional outbox, per-tenant idempotency keys, saga compensations, abort handling                     | NOT_STARTED                        |
| [`PHASE_07_SECURITY_APPROVALS.md`](PHASE_07_SECURITY_APPROVALS.md)         | Security, Permissions & Human Approval                | Deny-by-default 4-tier permission gate, canonical SHA-256 payload-bound tokens, atomic consumption, step-up auth for RESTRICTED, Approval Center API          | NOT_STARTED                        |
| [`PHASE_08_MCP_INTEGRATIONS.md`](PHASE_08_MCP_INTEGRATIONS.md)             | MCP Gateway & Integrations                            | Subprocess stdio & HTTP connection pooling, supervisor, schema normalizer, schema hash alarms, pinned servers (Git, GitHub, Brave, Notion ro, Google adapter) | NOT_STARTED                        |
| [`PHASE_09_SPECIALIST_AGENTS.md`](PHASE_09_SPECIALIST_AGENTS.md)           | Specialist AI Agents                                  | Research, Coding (Sandbox Runner), Document, Email & Comms, PM (read-only) specialist agents, typed Pydantic payloads, tool allowlists                        | NOT_STARTED                        |
| [`PHASE_10_FILE_PROCESSING.md`](PHASE_10_FILE_PROCESSING.md)               | File Processing & Document Generation                 | XLSX (`openpyxl`), DOCX (`python-docx`), PPTX (`python-pptx`), PDF (`weasyprint`), deterministic verifiers, `/artifacts` sandbox storage                      | NOT_STARTED                        |
| [`PHASE_11_MEMORY_KNOWLEDGE.md`](PHASE_11_MEMORY_KNOWLEDGE.md)             | Memory & Knowledge System                             | Redis working memory, PostgreSQL profile prefs, pgvector 768-dim embeddings, hybrid dense/sparse search, PII scrubber, time decay, GDPR purge                 | NOT_STARTED                        |
| [`PHASE_12_REALTIME_VOICE.md`](PHASE_12_REALTIME_VOICE.md)                 | Real-Time Voice System                                | Gemini Live API WebSocket bridge, WebAudio 16 kHz worklet, Silero VAD, barge-in <= 150 ms, session resumption, voice-task decoupling                          | NOT_STARTED                        |
| [`PHASE_13_BROWSER_AUTOMATION.md`](PHASE_13_BROWSER_AUTOMATION.md)         | Browser & Computer Automation                         | Playwright container sandbox, strict egress proxy (anti-DNS rebinding), vault-injected credentials, human handoff for CAPTCHA/bot gates                       | NOT_STARTED                        |
| [`PHASE_14_FRONTEND_EXPERIENCE.md`](PHASE_14_FRONTEND_EXPERIENCE.md)       | Frontend Product Experience                           | Ambient workspace, voice orb, deliverable canvas preview, task center & live work drawer, approval center, responsive design                                  | NOT_STARTED                        |
| [`PHASE_15_TESTING_HARDENING.md`](PHASE_15_TESTING_HARDENING.md)           | Testing, Security Hardening & Evaluation              | 5 acceptance workflows (#1, #3, #5, #6, #7), multi-tenant isolation tests, MCP gate safety property tests, prompt-injection defense, chaos recovery           | NOT_STARTED                        |
| [`PHASE_16_PRODUCTION_DEPLOYMENT.md`](PHASE_16_PRODUCTION_DEPLOYMENT.md)   | Production Deployment & Operations                    | Production Docker/K8s manifests, TLS ingress, OpenTelemetry distributed tracing, Prometheus/Grafana alerts, backup policies, operational runbooks             | NOT_STARTED                        |

---

## 1.1 Relationship to the master-plan roadmap

The numbered implementation documents are detailed work packages; they do not have a one-to-one numbering relationship with the P1–P8 milestones in section 19 of `MAYA_AI_FINAL_PLAN.md`. Master-plan P1 Foundation spans implementation Phases 01–04: infrastructure and spikes (01), database/auth foundation (02), model gateway (03), and persisted basic chat (04). P1 is not complete until its combined exit criteria pass. The later master-plan milestones group additional implementation phases as described in the roadmap.

The calendar in the master plan is explicitly unvalidated. Do not treat the target weeks in the phase documents as a committed schedule until the owner re-baselines it.

---

## 2. Canonical Policies

The canonical agent instructions and repository-wide policies are in [`AGENTS.md`](../../../AGENTS.md) and [`governance/`](../../../governance/README.md). This phase index does not duplicate those rules. Phase-specific prerequisites and acceptance criteria are recorded in each phase document and the [acceptance checklist](../PHASE_ACCEPTANCE_CHECKLIST.md).

---

## 3. Phase Status Lifecycle

Every phase document and task tracks its lifecycle through distinct, validated statuses:

```
               ┌───────────────┐
               │  NOT_STARTED  │
               └───────┬───────┘
                       │ Prerequisites verified
                       ▼
               ┌───────────────┐
       ┌──────►│     READY     │
       │       └───────┬───────┘
       │               │ Implementation started
       │               ▼
       │       ┌───────────────┐
       │       │  IN_PROGRESS  ├──────────────┐
       │       └───────┬───────┘              │
       │               │                      │ Blocker encountered
       │ Work resumed  ▼                      ▼
       │       ┌───────────────┐      ┌───────────────┐
       └───────┤   IN_REVIEW   │      │    BLOCKED    │
               └───────┬───────┘      └───────────────┘
                       │ Tests & validation run
                       ▼
               ┌───────────────┐
               │    TESTING    │
               └───────┬───────┘
                       │ All exit criteria met
                       ▼
               ┌───────────────┐
               │   COMPLETED   │
               └───────────────┘
```

- **NOT_STARTED:** Prerequisites in preceding phases have not yet been completed or verified.
- **READY:** All prerequisite phases and technical dependencies are satisfied; implementation can begin.
- **IN_PROGRESS:** Active development of tasks is underway.
- **BLOCKED:** Unresolved architectural ambiguity, failed upstream dependency, or missing third-party capability prevents progress.
- **IN_REVIEW:** Code, tests, and documentation are written and undergoing automated linting, typechecking, and peer/agent review.
- **TESTING:** Automated unit, integration, and security test suites are executing against the implementation.
- **COMPLETED:** All tasks pass their acceptance criteria, full local validation (`npm run validate`) passes, documentation is updated, and exit gate conditions are verified.

---

## 4. How to Execute a Phase

To execute a phase:

1. Open the phase document and verify that all items in **Section B (Prerequisites)** are satisfied.
2. Review the task list in **Section D (Implementation Breakdown)** and assign the recommended agent role ([`AGENT_TASK_ASSIGNMENTS.md`](../AGENT_TASK_ASSIGNMENTS.md)).
3. Follow the test-driven development workflow:
   - Create failing tests matching the specified test commands.
   - Implement the minimal required logic to pass the tests.
   - Run local validation commands.
4. Execute security checks and verify that data isolation and permission boundaries hold.
5. Update required documentation files identified in **Section E** and **Section F**.
6. Review against **Section I (Definition of Done)** and **Section J (Phase Exit Gate)**.
7. Update [`IMPLEMENTATION_STATUS.md`](../IMPLEMENTATION_STATUS.md) upon passing the exit gate.
