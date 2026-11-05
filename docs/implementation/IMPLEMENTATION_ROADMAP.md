# MAYA AI — Implementation Roadmap

## 1. Executive Summary and Roadmap Architecture

This document defines the master implementation roadmap for building **MAYA AI (MYAI)**, a voice-first, multi-agent autonomous digital employee, decomposed from [`MAYA_AI_FINAL_PLAN.md`](../../MAYA_AI_FINAL_PLAN.md).

To ensure deterministic execution by multiple AI coding agents (Claude Code, GitHub Copilot, Google Antigravity, OpenAI Codex, Cursor, and Gemini CLI) and human software engineers, the architecture is partitioned into 17 sequential and parallel phases. Each phase establishes strict API, database, and security contracts before dependent modules are activated.

The detailed implementation phases are work packages, not one-to-one equivalents of the P1–P8 roadmap milestones in the master plan. In particular, master-plan P1 Foundation is delivered across implementation Phases 01–04: infrastructure and spikes, database/authentication, the model gateway, and persisted basic chat. P1 is complete only after all four detailed phase gates pass. The master plan's 22-week calendar is explicitly unvalidated, and the detailed phase schedule contains conflicts; all target weeks below are inherited estimates, not an approved baseline.

---

## 2. Master Phase Schedule and Priority Matrix

| Phase                                                                  | Title                                                 | Priority | Complexity | Est. Duration | Target Weeks | Primary Focus                                                                                             |
| :--------------------------------------------------------------------- | :---------------------------------------------------- | :------- | :--------- | :------------ | :----------- | :-------------------------------------------------------------------------------------------------------- |
| [`Phase 00`](IMPLEMENTATION_PHASES/PHASE_00_GOVERNANCE.md)             | Project Governance & Development Environment          | Critical | Medium     | 3 days        | Week 0       | Canonical rules, pre-commit/pre-push hooks, agent sync, linters (COMPLETED)                               |
| [`Phase 01`](IMPLEMENTATION_PHASES/PHASE_01_FOUNDATION.md)             | Core Infrastructure & Application Foundation          | Critical | High       | 15 days       | Unvalidated  | FastAPI and Next.js scaffolds, PostgreSQL 17 + pgvector, Redis 7.4 AOF, health probes, four spikes        |
| [`Phase 02`](IMPLEMENTATION_PHASES/PHASE_02_AUTH_DATABASE_SECURITY.md) | Authentication, Users, Workspaces & Database Security | Critical | High       | 10 days       | Unvalidated  | Final schema and RLS, owner-decided email/username/password/OTP auth                                      |
| [`Phase 03`](IMPLEMENTATION_PHASES/PHASE_03_GEMINI_MODEL_GATEWAY.md)   | Gemini AI Integration & Model Gateway                 | Critical | High       | 8 days        | Unvalidated  | MVP model aliases, startup availability checks, streaming/tool/embedding contracts, metering and fallback |
| [`Phase 04`](IMPLEMENTATION_PHASES/PHASE_04_CHAT_CONVERSATIONS.md)     | Chat & Conversation System                            | High     | Medium     | 8 days        | Unvalidated  | Persisted basic chat and web client; stream contract and additional session APIs require owner decision   |
| [`Phase 05`](IMPLEMENTATION_PHASES/PHASE_05_ORCHESTRATOR_AGENTS.md)    | Orchestrator & Agent Framework                        | Critical | High       | 12 days       | Weeks 5–7    | LangGraph `StateGraph`, DAG planner, cycle check, PostgreSQL checkpointer (D6), projections               |
| [`Phase 06`](IMPLEMENTATION_PHASES/PHASE_06_BACKGROUND_TASK_ENGINE.md) | Background Task Execution Engine                      | Critical | High       | 12 days       | Weeks 6–8    | Queue workers, per-tenant idempotency keys, transactional outbox, sagas, compensations                    |
| [`Phase 07`](IMPLEMENTATION_PHASES/PHASE_07_SECURITY_APPROVALS.md)     | Security, Permissions & Human Approval                | Critical | High       | 10 days       | Weeks 8–10   | Deny-by-default 4-tier gate, SHA-256 payload-bound tokens, single-use atomic consumption, TOTP            |
| [`Phase 08`](IMPLEMENTATION_PHASES/PHASE_08_MCP_INTEGRATIONS.md)       | MCP Gateway & Integrations                            | Critical | High       | 15 days       | Weeks 10–12  | Stdio/HTTP connection pooling, supervisor, schema normalizer, schema hash alarms, 6 MVP tools             |
| [`Phase 09`](IMPLEMENTATION_PHASES/PHASE_09_SPECIALIST_AGENTS.md)      | Specialist AI Agents                                  | High     | High       | 15 days       | Weeks 12–14  | Research, Coding (Sandbox Runner), Document, Email & Comms, PM (read-only) agents                         |
| [`Phase 10`](IMPLEMENTATION_PHASES/PHASE_10_FILE_PROCESSING.md)        | File Processing & Document Generation                 | High     | Medium     | 10 days       | Weeks 14–16  | XLSX, DOCX, PPTX, PDF generation + deterministic programmatic verifiers, `/artifacts` S3                  |
| [`Phase 11`](IMPLEMENTATION_PHASES/PHASE_11_MEMORY_KNOWLEDGE.md)       | Memory & Knowledge System                             | High     | High       | 10 days       | Weeks 16–18  | Redis working memory, pgvector 768-dim embeddings, hybrid search, PII scrubber, decay, purge              |
| [`Phase 12`](IMPLEMENTATION_PHASES/PHASE_12_REALTIME_VOICE.md)         | Real-Time Voice System                                | High     | High       | 15 days       | Weeks 14–17  | Gemini Live WS bridge, WebAudio 16 kHz worklet, Silero VAD, barge-in <= 150 ms, decoupling                |
| [`Phase 13`](IMPLEMENTATION_PHASES/PHASE_13_BROWSER_AUTOMATION.md)     | Browser & Computer Automation                         | Medium   | High       | 12 days       | Weeks 17–19  | Containerized Playwright, anti-SSRF egress proxy, vault credentials, CAPTCHA human handoff                |
| [`Phase 14`](IMPLEMENTATION_PHASES/PHASE_14_FRONTEND_EXPERIENCE.md)    | Frontend Product Experience                           | High     | High       | 12 days       | Weeks 17–19  | Ambient Workspace (Voice Orb, Canvas), Task Center DAG drawer, Approval Center diff cards                 |
| [`Phase 15`](IMPLEMENTATION_PHASES/PHASE_15_TESTING_HARDENING.md)      | Testing, Security Hardening & Evaluation              | Critical | High       | 15 days       | Weeks 20–22  | 5 acceptance workflows, gate property testing, tenant isolation suite, latency tests, chaos               |
| [`Phase 16`](IMPLEMENTATION_PHASES/PHASE_16_PRODUCTION_DEPLOYMENT.md)  | Production Deployment & Operations                    | High     | High       | 15 days       | Weeks 21–22  | Production Docker/K8s, TLS reverse proxy, OpenTelemetry tracing, Prometheus/Grafana, runbooks             |

---

## 3. Major Project Milestones

```
[M0: Governance] ──► [M1: Foundation] ──► [M2: Agent Engine] ──► [M3: Tool Security] ──► [M4: Multimodal MVP] ──► [M5: Launch]
     (Week 0)            (Weeks 1–4)          (Weeks 5–8)            (Weeks 8–12)          (Weeks 12–19)          (Weeks 20–22)
```

- **Milestone 0 (Project Governance Established):** Multi-agent instructions, validation scripts, native git hooks, and CI workflows active. Status: COMPLETED.
- **Milestone 1 (Application Foundation & Spikes Proven):** Complete only when implementation Phases 01–04 provide the FastAPI/Next.js foundation, PostgreSQL 17 with RLS, Redis 7.4, owner-approved account authentication, a working model gateway, persisted basic chat, and documented S1–S4 go/no-go results.
- **Milestone 2 (Agent Orchestration & Task Bus):** LangGraph checkpointer persists execution graphs to PostgreSQL; background workers execute subtasks idempotently.
- **Milestone 3 (Cryptographic Governance & Hardened Tools):** 4-tier permission gate enforces payload-bound single-use approvals; MCP server pool active without direct Docker socket exposure.
- **Milestone 4 (Multimodal Specialist Capability):** Coding Agent patches bugs in sandbox (Workflow 3); Document Agent outputs formula-verified spreadsheets and PDFs (Workflows 5, 6, 7); Real-time voice streams with sub-150 ms barge-in (Workflow 1).
- **Milestone 5 (Production Launch Readiness):** Full compliance across all 5 MVP acceptance workflows; property tests prove zero permission leaks; OTel tracing and Prometheus alerting active; Production Readiness Review signed.

---

## 4. Requirements Traceability Matrix

This matrix maps every functional requirement (FR), non-functional requirement (NFR), architectural decision (D), and acceptance workflow from [`MAYA_AI_FINAL_PLAN.md`](../../MAYA_AI_FINAL_PLAN.md) directly to its implementing phase, task, verification test, and acceptance criterion.

| Original Requirement ID | Requirement Summary                                                       | Phase    | Task ID     | Verification Test                | Acceptance Criterion                                                         |
| :---------------------- | :------------------------------------------------------------------------ | :------- | :---------- | :------------------------------- | :--------------------------------------------------------------------------- |
| **FR-01**               | Bidirectional streaming voice with live transcription                     | Phase 12 | P12-T002    | `test_voice_websocket.py`        | 16 kHz input stream yields 24 kHz response with live transcripts             |
| **FR-02**               | Barge-in: playback stops <= 150 ms of user speech                         | Phase 12 | P12-T003    | `test_barge_in.py`               | Audio output flushes and halts <= 150 ms on speech detection                 |
| **FR-03**               | Compound instructions become an inspectable, cycle-free DAG               | Phase 05 | P05-T002    | `test_dag_planner.py`            | Decomposes tasks into acyclic graphs; Kahn's algorithm catches cycles        |
| **FR-04**               | Long tasks run in background without blocking chat/voice                  | Phase 06 | P06-T001    | `test_worker_lifecycle.py`       | Workers execute subtask jobs asynchronously; voice remains responsive        |
| **FR-05**               | Dynamic MCP discovery & Gemini function declaration mapping               | Phase 08 | P08-T002    | `test_schema_normalizer.py`      | MCP tool schemas convert cleanly to Gemini function declarations             |
| **FR-06**               | State-changing actions blocked until valid, payload-bound approval exists | Phase 07 | P07-T003    | `test_approval_tokens.py`        | Replayed, expired, or tampered tokens fail; mutating call refused            |
| **FR-07**               | Generate and deterministically validate PDF, DOCX, XLSX, PPTX             | Phase 10 | P10-T001..4 | `test_*_verifier.py`             | Deterministic verifiers detect broken formulas, XML, overflow, or empty text |
| **FR-08**               | Repo analysis, bug repro, patch, test in sandbox, gated PR                | Phase 09 | P09-T004    | `test_coding_agent.py`           | Workflow 3 executes end-to-end; sandbox tests pass; PR paused for approval   |
| **FR-09**               | Hybrid (vector + full-text) memory with citations                         | Phase 11 | P11-T003    | `test_hybrid_retrieval.py`       | Dense (0.7) + sparse (0.3) search returns relevant items with source IDs     |
| **FR-10**               | Sandboxed browser automation with anti-bot handoff                        | Phase 13 | P13-T001..3 | `test_browser_sandbox.py`        | Playwright runs in isolated container; anti-SSRF proxy blocks metadata IPs   |
| **FR-11**               | GDPR/CCPA purge of user memory and history                                | Phase 11 | P11-T004    | `test_memory_api.py`             | `DELETE /api/v1/memory/purge` erases 100% of target user rows across tables  |
| **NFR-Voice**           | Voice latency p95 <= 850 ms; barge-in <= 150 ms                           | Phase 12 | P12-T003..4 | `test_voice_latency.py`          | Measured latency benchmarks meet p95 <= 850 ms under load                    |
| **NFR-TTFT**            | Text time-to-first-token <= 600 ms on `orchestrator`                      | Phase 04 | P04-T003    | `test_chat_streaming.py`         | SSE first token emitted within 600 ms of request submission                  |
| **NFR-Isolation**       | Multi-tenant Row-Level Security on every tenant table                     | Phase 02 | P02-T002    | `tests/postgres/test_rls.py`     | Cross-tenant queries return 0 rows for unauthorized workspace                |
| **NFR-Secrets**         | AES-256-GCM encryption at rest; keys rotated <= 90 days                   | Phase 07 (deferred from Phase 02) | P02-T005 | to be defined in Phase 07 | Auth tag mismatch prevents decryption; plaintext never stored on disk        |
| **NFR-Audit**           | Tamper-evident, hash-chained append-only audit trail                      | Phase 07 (deferred from Phase 02) | P02-T006 | to be defined in Phase 07 | Cryptographic hash chain unbroken; UPDATE/DELETE revoked from app (the revoke exists since Phase 02) |
| **NFR-Recovery**        | Resumes from last checkpoint after worker crash (idempotent)              | Phase 06 | P06-T002    | `test_task_idempotency.py`       | Worker crash mid-subtask resumes without duplicated side effects             |
| **D1**                  | Consolidated plan base with restored specs                                | Phase 00 | P00-T001    | `npm run agents:check`           | Architecture adheres strictly to final consolidated specifications           |
| **D2**                  | Modular monolith (FastAPI + Celery/ARQ + Postgres + Redis)                | Phase 01 | P01-T002    | `test_db_redis_connection.py`    | Single deployable backend unit with internal package modularity              |
| **D3**                  | Voice and task runtimes decoupled via Redis event bus                     | Phase 12 | P12-T005    | `test_voice_task_bridge.py`      | Voice model holds strictly 2 tools; tasks execute in worker pool             |
| **D4**                  | 4-tier permission gate; deny-by-default; server hints cannot lower        | Phase 07 | P07-T001    | `test_permission_registry.py`    | Unclassified tools disabled; server hints cannot downgrade tiers             |
| **D5**                  | Approvals bound to payload SHA-256; UI grant only; voice deny-only        | Phase 07 | P07-T004    | `test_approvals_api.py`          | Spoken approval rejected with HTTP 403; UI grant mints valid token           |
| **D6**                  | Single state source: LangGraph Postgres checkpointer                      | Phase 05 | P05-T003    | `test_langgraph_checkpointer.py` | Relational `tasks`/`subtasks` project accurately from checkpointer           |
| **D7**                  | Memory: 3 tiers (Redis, Postgres profile, Postgres pgvector)              | Phase 11 | P11-T002    | `test_memory_ingestion.py`       | Working, profile, and semantic tiers function without cold archive bloat     |
| **D8**                  | Short jobs; workers never block on humans; Celery/ARQ spike               | Phase 06 | P06-T001    | `test_worker_lifecycle.py`       | One subtask per message; `awaiting_approval` exits worker immediately        |
| **D9**                  | Single-node Redis with AOF persistence for MVP                            | Phase 01 | P01-T001    | `docker-compose config`          | Redis starts with `appendonly yes`; cluster deferred to post-MVP             |
| **D10**                 | Governance & approval gate implemented before write tools                 | Phase 07 | P07-T003    | `test_approval_tokens.py`        | Phase 07 precedes Phase 08 write connectors and Phase 09 agents              |
| **D11**                 | Pinned MCP catalogue baked into images; no runtime npx/uvx                | Phase 08 | P08-T001    | `test_mcp_client_manager.py`     | Subprocesses launch from pre-baked container binaries; zero dynamic pulls    |
| **D12**                 | NFRs: Voice p95 <= 850 ms; barge-in <= 150 ms; availability 99.9%         | Phase 15 | P15-T004    | `test_voice_latency.py`          | Automated benchmark asserts p95 latency and interruption times               |
| **D13**                 | Model role aliases with startup check and fallbacks                       | Phase 03 | P03-T002    | `test_model_startup_check.py`    | `models.list` verifies aliases; missing models trigger clean fallback        |
| **D14**                 | No Docker socket exposure; fixed-function Sandbox Runner                  | Phase 09 | P09-T003    | `test_sandbox_runner.py`         | Ephemeral containers run code without host `/var/run/docker.sock`            |
| **Workflow 1**          | Daily briefing & agenda (Orchestrator, Email, PM read-only)               | Phase 09 | P09-T007    | `test_pm_agent.py`               | Aggregates Calendar and Notion into morning markdown brief                   |
| **Workflow 3**          | GitHub issue -> root cause -> fix PR in sandbox                           | Phase 09 | P09-T004    | `test_coding_agent.py`           | Coding agent replicates bug in sandbox, patches code, requests PR approval   |
| **Workflow 5**          | Deep competitive research -> verified PDF                                 | Phase 10 | P10-T004    | `test_pdf_verifier.py`           | Research Agent output compiled to PDF with extractable text layer            |
| **Workflow 6**          | PDF invoices -> formatted, formula-verified Excel                         | Phase 10 | P10-T001    | `test_xlsx_verifier.py`          | Multi-tab Excel generated; zero `#REF!` or formula evaluation errors         |
| **Workflow 7**          | Brief -> 8-slide PowerPoint without overflow                              | Phase 10 | P10-T003    | `test_pptx_verifier.py`          | Exactly 8 slides generated; bounding boxes remain within 16:9 canvas         |

---

## 5. Parallel Execution and Workstream Tracks

While the critical path is strictly ordered around infrastructure and security gates, several subsystems can execute concurrently once their contract dependencies stabilize:

```
Track 1 (Infrastructure & Core): Phase 00 ──► Phase 01 ──► Phase 02 ──► Phase 05 ──► Phase 06 ──► Phase 07 ──► Phase 08 ──► Phase 09 ──► Phase 15 ──► Phase 16
Track 2 (AI Models & Chat):                   Phase 03 ──► Phase 04 ─────────────────────────────────────────────┘
Track 3 (Files & Documents):                                                         Phase 10 ───────────────────┘
Track 4 (Memory & RAG):                                                              Phase 11 ───────────────────┘
Track 5 (Real-Time Voice):                                                           Phase 12 ───────────────────┘
Track 6 (Frontend Surfaces):                                                         Phase 14 ───────────────────┘
Track 7 (Browser Sandbox):                                                           Phase 13 ───────────────────┘
```

1. **Track 1 (Core Backbone):** Sequential execution of foundational infrastructure, database RLS, LangGraph orchestrator, background task engine, permission gate, and MCP supervisor.
2. **Track 2 (AI Gateway & Conversations):** Phase 03 begins in parallel with Phase 02 once Docker is active; Phase 04 builds upon Phase 02 and 03.
3. **Tracks 3, 4, 5 (Specialized Capabilities):** Phase 10 (Document Engines), Phase 11 (Memory), and Phase 12 (Voice) execute concurrently once Phase 07 and Phase 08 are complete.
4. **Track 6 (Frontend Experience):** Next.js UI development proceeds in parallel against established OpenAPI and WebSocket contracts.
5. **Track 7 (Browser Sandbox):** Post-MVP browser automation container and anti-SSRF proxy developed concurrently with advanced specialist capabilities.
