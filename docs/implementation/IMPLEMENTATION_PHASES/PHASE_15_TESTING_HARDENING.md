# Phase 15 — Testing, Security Hardening, and Evaluation

## A. Phase Overview

- **Phase Number:** Phase 15
- **Phase Name:** Testing, Security Hardening, and Evaluation
- **Objective:** Execute the comprehensive verification, security hardening, and evaluation program across all MAYA AI subsystems: validate the 5 MVP acceptance workflows (#1, #3, #5, #6, #7); execute deterministic property-based safety tests at the MCP gate; run automated multi-tenant isolation tests; benchmark voice and token latencies; evaluate agent plan quality against golden datasets; and conduct chaos fault-injection tests.
- **Why This Phase is Necessary:** As established in [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §21 and Principle 2 ("No success without proof"), complex multi-agent architectures cannot be declared production-ready based on manual spot checks or LLM opinions. Deterministic test harnesses, property tests at permission boundaries, and chaos resilience tests are mandatory to guarantee that no unauthorized mutating calls occur, tenant boundaries never leak, and workers recover cleanly from abrupt infrastructure failures.
- **Expected Outcome:** 100% pass rate on the 5 MVP acceptance workflows; property tests proving zero mutating calls bypass human approval; cross-tenant automated isolation suite passing in CI; voice p95 latency confirmed <= 850 ms and barge-in <= 150 ms; agent evaluation baselines established; and chaos tests proving clean checkpoint resumption.
- **Priority:** Critical
- **Estimated Complexity:** High
- **Estimated Duration:** 15 business days (Weeks 20–22 of master schedule).

---

## B. Prerequisites

- **Required Completed Phases:** All preceding phases: Phase 01 through Phase 14.
- **Required Infrastructure:** Full stack running in test environment (FastAPI, Next.js, PostgreSQL 17 + pgvector, Redis 7.4, MinIO, MCP Supervisor, Sandbox Runner).
- **Required Credentials or External Services:** Gemini API test keys, mock MCP test harnesses, test email and repository sandboxes.
- **Required Architectural Decisions:** [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §17 (15 Workflows), §21 (Testing and Quality), §24 (Risks & Mitigations).
- **Required Documentation:** [`governance/TESTING_POLICY.md`](../../../governance/TESTING_POLICY.md), [`governance/SECURITY_POLICY.md`](../../../governance/SECURITY_POLICY.md).

---

## C. Scope

### Included Features

- MVP Acceptance Workflows Execution Suite:
  - Workflow 1: Daily Briefing & Agenda (Orchestrator, Email, PM read-only).
  - Workflow 3: GitHub Issue -> Root Cause Analysis -> Sandbox Fix -> Gated PR (Coding Agent).
  - Workflow 5: Deep Competitive Research -> Verified PDF Report (Research, Document Agents).
  - Workflow 6: PDF Invoices -> Formula-Verified XLSX Spreadsheet (Document Agent).
  - Workflow 7: Executive Brief -> 8-Slide PowerPoint Presentation without text overflow (Document Agent).
  - Stretch Workflows (evaluated if timeline permits): Workflow 2 (Email triage), Workflow 8 (Client meeting dossier), Workflow 11 (PR review).
- Deterministic Safety Gate Verification:
  - Automated property test generating 5,000 synthetic tool invocations; assert 100% of mutating calls without valid approval tokens are blocked.
- Multi-Tenant Isolation Suite:
  - Automated cross-tenant query matrix executed against all 11 database tables and vector memory endpoints.
- Performance & Latency Benchmarks:
  - Automated voice roundtrip latency test asserting p95 <= 850 ms.
  - Voice barge-in interruption test asserting audio stop <= 150 ms.
  - Text chat time-to-first-token (TTFT) benchmark asserting <= 600 ms on `orchestrator` alias.
- Agent Evaluation Suite (Golden Datasets):
  - 50 golden benchmark instructions evaluating DAG planning accuracy, tool selection precision/recall, and parameter fidelity.
- Chaos & Fault Injection Testing:
  - Abrupt worker kill (SIGKILL) mid-task; verify resumption from last checkpoint with zero duplicate side effects.
  - Redis connection drop during task update; verify transactional outbox durability.
  - Subprocess crash of `mcp-server-git`; verify supervisor backoff restart and route fallback.
- Security Hardening:
  - Indirect prompt-injection test battery (`<<<UNTRUSTED>>>` boundary tests, system prompt override attempts).
  - Secret scanner audit ensuring zero credential leakage in logs or database fields.

### Excluded Features

- Multi-region disaster recovery failover testing (post-MVP).
- Penetration testing of external third-party SaaS APIs (Google, GitHub).

### Deferred Features

- Automated synthetic voice stress-testing across 1,000 concurrent audio channels.

### Dependencies on Future Phases

- Phase 16 (Production Deployment) requires the exit gate artifacts, test reports, and baseline metrics produced here.

---

## D. Implementation Breakdown

### Task P15-T001: Acceptance Workflows End-to-End Test Suite

- **Task ID:** P15-T001
- **Task Name:** Automate 5 MVP Acceptance Workflows
- **Detailed Description:** Implement backend/tests/e2e/test_acceptance_workflows.py (planned artifact). Script end-to-end execution of:
  - Workflow 1: aggregate calendar and Notion into daily briefing markdown.
  - Workflow 3: analyze synthetic repo issue, repro failure in sandbox, patch code, verify green tests, pause for approval, and submit mock PR.
  - Workflow 5: research query -> synthesis -> Weasyprint PDF with verified text layer.
  - Workflow 6: parse PDF invoices -> populate openpyxl table -> verify formulas evaluate cleanly without `#REF!`.
  - Workflow 7: generate 8-slide PPTX -> verify bounding-box overflow passes.
- **Expected Files:** backend/tests/e2e/test_acceptance_workflows.py (planned artifact), `backend/tests/fixtures/acceptance_data/`.
- **Dependencies:** Phases 09, 10, 12, 14.
- **Assigned Agent Role:** Lead QA & Systems Test Engineer.
- **Required Tests:** Full execution of `pytest backend/tests/e2e/test_acceptance_workflows.py`.
- **Documentation Updates:** docs/operations/ACCEPTANCE_WORKFLOW_REPORT.md (planned artifact).
- **Acceptance Criteria:** All 5 acceptance workflows pass with zero unhandled exceptions; output deliverables pass their respective deterministic verifiers.

### Task P15-T002: Deterministic Safety Gate Property Testing

- **Task ID:** P15-T002
- **Task Name:** Property-Based Security Testing at MCP Boundary
- **Detailed Description:** Implement backend/tests/security/test_permission_gate_property.py (planned artifact) using `hypothesis` or parameterized permutations. Generate 5,000 randomized tool calls with varying arguments, server hints, and permission levels:
  - Attempt dispatch without approval tokens.
  - Attempt dispatch with replayed, expired, or tampered tokens.
  - Attempt dispatch with server hints claiming read-only status for destructive tools.
  - Assert with 100% certainty that zero mutating calls reach execution.
- **Expected Files:** backend/tests/security/test_permission_gate_property.py (planned artifact).
- **Dependencies:** Phase 07, Phase 08.
- **Assigned Agent Role:** Security & Penetration Testing Specialist.
- **Required Tests:** `pytest backend/tests/security/test_permission_gate_property.py`.
- **Documentation Updates:** docs/security/GATE_SECURITY_VERIFICATION.md (planned artifact).
- **Acceptance Criteria:** 5,000 randomized calls execute; 0 security breaches; 100% of unapproved mutating actions blocked and recorded in `audit_log`.

### Task P15-T003: Multi-Tenant Isolation and Data Leakage CI Suite

- **Task ID:** P15-T003
- **Task Name:** Implement Automated Cross-Tenant Isolation Test Matrix
- **Detailed Description:** Implement backend/tests/security/test_cross_tenant_isolation.py (planned artifact). Seed two independent tenants (Tenant Alpha, Tenant Beta) with users, sessions, messages, tasks, artifacts, audit logs, and vector memory items:
  - Execute exhaustive cross-read matrix: authenticate as Tenant Alpha, attempt to query, update, or delete Tenant Beta records across all 11 tables.
  - Execute vector search with Tenant Alpha credentials using queries identical to Tenant Beta knowledge items.
  - Assert that 0 records, 0 vector chunks, and 0 artifacts from Tenant Beta are exposed.
- **Expected Files:** backend/tests/security/test_cross_tenant_isolation.py (planned artifact).
- **Dependencies:** Phase 02, Phase 11.
- **Assigned Agent Role:** Database Security Specialist.
- **Required Tests:** `pytest backend/tests/security/test_cross_tenant_isolation.py`.
- **Documentation Updates:** docs/security/TENANT_ISOLATION_REPORT.md (planned artifact).
- **Acceptance Criteria:** 100% of cross-tenant access attempts return 0 rows or HTTP 404/403; test runs in continuous integration pipeline.

### Task P15-T004: Real-Time Audio Latency and Barge-In Verification

- **Task ID:** P15-T004
- **Task Name:** Execute Voice Performance and Barge-In Benchmarks
- **Detailed Description:** Implement backend/tests/benchmarks/test_voice_latency.py (planned artifact). Connect to `WS /api/v1/voice/live` using recorded 16 kHz audio speech samples. Measure:
  - End-to-end roundtrip latency: timestamp of last input audio chunk to timestamp of first received 24 kHz audio chunk across 50 iterations.
  - Barge-in interrupt latency: transmit interrupt signal during active audio playback; measure time until server emits `AUDIO_INTERRUPT`.
  - Compile latency distribution percentiles (p50, p90, p95, p99).
- **Expected Files:** backend/tests/benchmarks/test_voice_latency.py (planned artifact), docs/operations/VOICE_BENCHMARK_REPORT.md (planned artifact).
- **Dependencies:** Phase 12.
- **Assigned Agent Role:** Audio Performance Engineer.
- **Required Tests:** Benchmark execution script.
- **Documentation Updates:** docs/operations/VOICE_BENCHMARK_REPORT.md (planned artifact).
- **Acceptance Criteria:** First-audio latency meets SLA p95 <= 850 ms (target 450–600 ms); barge-in interrupt executes in <= 150 ms.

### Task P15-T005: Golden Dataset Agent Planning Evaluation

- **Task ID:** P15-T005
- **Task Name:** Build Agent Evaluation Harness with Golden Dataset
- **Detailed Description:** Implement backend/tests/evals/test_agent_evaluation.py (planned artifact). Assemble a golden dataset of 50 complex instructions with ground-truth DAG plans, assigned agents, and expected tool sequences. Run instructions through the Orchestrator; compute:
  - DAG structural validity (% cycle-free and logically ordered).
  - Agent assignment accuracy (% matching ground truth).
  - Parameter extraction precision.
  - Store baseline accuracy metrics to prevent model prompt regression.
- **Expected Files:** backend/tests/evals/test_agent_evaluation.py (planned artifact), backend/tests/evals/golden_dataset.json (planned artifact), docs/operations/AGENT_EVALUATION_REPORT.md (planned artifact).
- **Dependencies:** Phase 05, Phase 09.
- **Assigned Agent Role:** AI Evaluation & Alignment Specialist.
- **Required Tests:** Evaluation test harness execution.
- **Documentation Updates:** docs/operations/AGENT_EVALUATION_REPORT.md (planned artifact).
- **Acceptance Criteria:** DAG planning accuracy achieves >= 95% on golden dataset; zero cycles detected; evaluation harness runs deterministically.

### Task P15-T006: Chaos Resilience and Recovery Testing

- **Task ID:** P15-T006
- **Task Name:** Execute Chaos Fault-Injection Suite
- **Detailed Description:** Implement backend/tests/chaos/test_system_recovery.py (planned artifact). Inject faults into running workflows:
  - Send `SIGKILL` to background worker mid-task; verify task restarts from last checkpoint and completes with zero duplicate side effects.
  - Simulate Redis server restart during outbox relay; verify zero dropped events.
  - Terminate MCP subprocess abruptly; verify supervisor respawns process and recovers connection pool.
- **Expected Files:** backend/tests/chaos/test_system_recovery.py (planned artifact), docs/operations/CHAOS_RECOVERY_REPORT.md (planned artifact).
- **Dependencies:** Phase 06, Phase 08.
- **Assigned Agent Role:** Reliability Engineer.
- **Required Tests:** Chaos test runner.
- **Documentation Updates:** docs/operations/CHAOS_RECOVERY_REPORT.md (planned artifact).
- **Acceptance Criteria:** System recovers automatically from all injected single-point component failures without data corruption or orphan processes.

---

## E. File and Folder Changes

- **Files to Create:**
  - backend/tests/e2e/__init__.py (planned artifact)
  - backend/tests/e2e/test_acceptance_workflows.py (planned artifact)
  - backend/tests/security/__init__.py (planned artifact)
  - backend/tests/security/test_permission_gate_property.py (planned artifact)
  - backend/tests/security/test_cross_tenant_isolation.py (planned artifact)
  - backend/tests/benchmarks/__init__.py (planned artifact)
  - backend/tests/benchmarks/test_voice_latency.py (planned artifact)
  - backend/tests/evals/__init__.py (planned artifact)
  - backend/tests/evals/test_agent_evaluation.py (planned artifact)
  - backend/tests/evals/golden_dataset.json (planned artifact)
  - backend/tests/chaos/__init__.py (planned artifact)
  - backend/tests/chaos/test_system_recovery.py (planned artifact)
  - docs/operations/ACCEPTANCE_WORKFLOW_REPORT.md (planned artifact)
  - docs/security/GATE_SECURITY_VERIFICATION.md (planned artifact)
  - docs/security/TENANT_ISOLATION_REPORT.md (planned artifact)
  - docs/operations/VOICE_BENCHMARK_REPORT.md (planned artifact)
  - docs/operations/AGENT_EVALUATION_REPORT.md (planned artifact)
  - docs/operations/CHAOS_RECOVERY_REPORT.md (planned artifact)
- **Files to Modify:**
  - `docs/architecture/SYSTEM_OVERVIEW.md`
- **Files to Avoid Modifying:**
  - `MAYA_AI_FINAL_PLAN.md`
- **Relevant Directories:** `backend/tests/`, `docs/operations/`, `docs/security/`.

---

## F. API and Data Contracts

### 1. Test Harness Execution Report Schema

```python
class TestSuiteExecutionReport(BaseModel):
    suite_name: str
    total_tests: int
    passed: int
    failed: int
    duration_seconds: float
    coverage_percentage: float
    security_violations: int = 0
    p95_latency_ms: float | None = None
```

---

## G. Security Requirements

- **Zero-Tolerance Safety Gate:** Property test suite must pass with 100% assertions; a single unapproved mutating action constitutes a critical build failure.
- **Cross-Tenant Verification:** Automated isolation suite must run in CI on every pull request.
- **Sanitized Test Data:** Synthetic fixtures only; no production credentials or personal customer data in test files.

---

## H. Testing Strategy

- **Property Testing:** Exhaustive permutations of parameters, invalid tokens, and spoofed headers.
- **Benchmark Testing:** Multi-iteration latency distributions with statistical confidence intervals.
- **Chaos Fault Injection:** Real process termination and connection drops during active workloads.

---

## I. Definition of Done

1. 5 MVP acceptance workflows (#1, #3, #5, #6, #7) execute and pass with zero unhandled exceptions.
2. Property safety tests confirm 100% enforcement of the permission gate.
3. Multi-tenant isolation test suite passes with zero data leakage.
4. Voice latency benchmark confirms first-audio p95 <= 850 ms and barge-in <= 150 ms.
5. Chaos tests confirm automatic recovery from worker, Redis, and MCP crashes.
6. Evaluation and verification reports published in `docs/operations/` and `docs/security/`.

---

## J. Phase Exit Gate

- **Demonstrated Proof:** Clean run of all test suites in `backend/tests/`; published and verified `ACCEPTANCE_WORKFLOW_REPORT.md` and `GATE_SECURITY_VERIFICATION.md`.
- **Dependent Phase Unlocked:** Phase 16 (Production Deployment and Operations) is cleared to begin.
