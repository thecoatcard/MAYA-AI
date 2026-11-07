# Phase 15 — Testing, Security Hardening, and Evaluation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Execute the comprehensive verification, security hardening, and evaluation program across all MAYA AI subsystems: validate the 5 MVP acceptance workflows (#1, #3, #5, #6, #7); execute deterministic property-based safety tests at the MCP gate; run automated multi-tenant isolation tests; benchmark voice and token latencies; evaluate agent plan quality against golden datasets; and conduct chaos fault-injection tests.

**Architecture:** A comprehensive verification suite:
1. `AcceptanceWorkflows` (`backend/tests/e2e/test_acceptance_workflows.py`): end-to-end execution of Workflows 1, 3, 5, 6, and 7;
2. `PermissionGatePropertyTest` (`backend/tests/security/test_permission_gate_property.py`): randomized permutations verifying 100% rejection of unapproved mutating calls;
3. `CrossTenantIsolationSuite` (`backend/tests/security/test_cross_tenant_isolation.py`): exhaustive cross-read matrix across all tables, pgvector embeddings, and Redis keys;
4. `VoiceLatencyBenchmark` (`backend/tests/benchmarks/test_voice_latency.py`): measuring first-audio latency (p95 <= 850 ms) and barge-in interrupt latency (<= 150 ms);
5. `AgentEvaluationHarness` (`backend/tests/evals/test_agent_evaluation.py`, `golden_dataset.json`): 50 complex instructions testing DAG validity, agent assignment, and parameter fidelity;
6. `ChaosRecoverySuite` (`backend/tests/chaos/test_system_recovery.py`): worker crash resumption, Redis reconnection, MCP supervisor respawn.

**Tech Stack:** Python 3.12, Pytest, Asyncpg, Redis 7.4, PostgreSQL 17 (pgvector, RLS).

**Spec:** `MAYA_AI_FINAL_PLAN.md` (§17, §21, §24), `docs/implementation/IMPLEMENTATION_PHASES/PHASE_15_TESTING_HARDENING.md`.

## Global Constraints

- **Zero-Tolerance Safety Gate:** 100% of mutating actions without valid approval tokens must be blocked.
- **Cross-Tenant Guarantee:** 0 records, 0 vector chunks, and 0 artifacts of Tenant Alpha may ever be accessible to Tenant Beta.
- **Voice NFR SLA:** p95 first-audio <= 850 ms; barge-in interrupt <= 150 ms.
- **Deterministic Verifiers:** Verified output files must pass their respective verifiers without exception.
- **Zero Regressions:** All existing test suites across the entire monorepo must continue to pass.

---

### Task 1: 5 MVP Acceptance Workflows Automation

**Files:**
- Create: `backend/tests/e2e/__init__.py`
- Create: `backend/tests/e2e/test_acceptance_workflows.py`
- Create: `docs/operations/ACCEPTANCE_WORKFLOW_REPORT.md`

- [ ] **Step 1: Implement Workflow 1 (Daily Briefing via Calendar & Notion)**
- [ ] **Step 2: Implement Workflow 3 (GitHub Bug -> Sandbox Fix -> Gated PR)**
- [ ] **Step 3: Implement Workflow 5 (Competitive Research -> Verified PDF)**
- [ ] **Step 4: Implement Workflow 6 (PDF Invoices -> Formula-Verified XLSX)**
- [ ] **Step 5: Implement Workflow 7 (Executive Brief -> 8-Slide PPTX without overflow)**
- [ ] **Step 6: Run tests and author `ACCEPTANCE_WORKFLOW_REPORT.md`**

---

### Task 2: Deterministic Safety Gate Property Testing

**Files:**
- Create: `backend/tests/security/__init__.py`
- Create: `backend/tests/security/test_permission_gate_property.py`
- Create: `docs/security/GATE_SECURITY_VERIFICATION.md`

- [ ] **Step 1: Write property test generating 5,000 synthetic tool invocations testing tampering, replay, missing tokens, and spoofed hints**
- [ ] **Step 2: Run test with pytest and confirm 100% rejection**
- [ ] **Step 3: Author `GATE_SECURITY_VERIFICATION.md`**

---

### Task 3: Multi-Tenant Isolation and Data Leakage Suite

**Files:**
- Create: `backend/tests/security/test_cross_tenant_isolation.py`
- Create: `docs/security/TENANT_ISOLATION_REPORT.md`

- [ ] **Step 1: Write cross-tenant isolation test covering all database tables, pgvector hybrid search, and Redis session keys**
- [ ] **Step 2: Run test against live PostgreSQL 17 and Redis 7.4**
- [ ] **Step 3: Author `TENANT_ISOLATION_REPORT.md`**

---

### Task 4: Real-Time Audio Latency & Barge-In Benchmarks

**Files:**
- Create: `backend/tests/benchmarks/__init__.py`
- Create: `backend/tests/benchmarks/test_voice_latency.py`
- Create: `docs/operations/VOICE_BENCHMARK_REPORT.md`

- [ ] **Step 1: Implement benchmark harness measuring first-audio response latency (p50, p90, p95) and barge-in interruption timing**
- [ ] **Step 2: Run benchmark and assert p95 <= 850 ms and barge-in <= 150 ms**
- [ ] **Step 3: Author `VOICE_BENCHMARK_REPORT.md`**

---

### Task 5: Golden Dataset Agent Planning Evaluation

**Files:**
- Create: `backend/tests/evals/__init__.py`
- Create: `backend/tests/evals/golden_dataset.json`
- Create: `backend/tests/evals/test_agent_evaluation.py`
- Create: `docs/operations/AGENT_EVALUATION_REPORT.md`

- [ ] **Step 1: Author 50 golden benchmark instructions across single-agent and multi-agent tasks**
- [ ] **Step 2: Implement evaluation harness checking DAG validity, agent assignment, and tool accuracy**
- [ ] **Step 3: Run evaluation and assert >= 95% DAG planning accuracy**
- [ ] **Step 4: Author `AGENT_EVALUATION_REPORT.md`**

---

### Task 6: Chaos Resilience & Recovery Suite

**Files:**
- Create: `backend/tests/chaos/__init__.py`
- Create: `backend/tests/chaos/test_system_recovery.py`
- Create: `docs/operations/CHAOS_RECOVERY_REPORT.md`

- [ ] **Step 1: Implement chaos tests for worker crash resumption, Redis reconnection, and MCP supervisor process respawn**
- [ ] **Step 2: Run tests and assert 100% recovery without orphan state or dropped outbox events**
- [ ] **Step 3: Author `CHAOS_RECOVERY_REPORT.md`**

---

### Task 7: Documentation & Status Updates

**Files:**
- Modify: `docs/implementation/IMPLEMENTATION_STATUS.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Update `docs/implementation/IMPLEMENTATION_STATUS.md` (mark Phase 15 as Implemented & Verified locally)**
- [ ] **Step 2: Update `CHANGELOG.md`**
- [ ] **Step 3: Validate documentation with `npm run docs:check`**
- [ ] **Step 4: Run monorepo fast validation (`npm run validate:fast`)**
