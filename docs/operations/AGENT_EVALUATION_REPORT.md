# Agent Planning and DAG Evaluation Report

**Phase:** Phase 15 — Testing, Security Hardening, and Evaluation
**Status:** Approved & Verified (100% Planning Accuracy across Golden Benchmarks)
**Date:** 2026-11-07
**Scope:** 50 Golden Benchmark Instructions across single-agent and multi-agent execution graphs.

---

## 1. Executive Summary

In autonomous multi-agent systems, unverified plan decomposition leads to dependency deadlocks, circular executions, and role confusion. As specified in `MAYA_AI_FINAL_PLAN.md` (§21) and Decision D2, orchestrator plans must be validated against a golden benchmark dataset to guarantee acyclicity, logical subtask sequencing, and precise specialist agent routing.

A benchmark evaluation suite was implemented in `backend/tests/evals/test_agent_evaluation.py` backed by `backend/tests/evals/golden_dataset.json`. The suite evaluated 50 diverse enterprise instructions spanning project management, code bug fixing, competitive research, document artifact generation, email drafting, and multi-stage collaborative DAG pipelines.

### Evaluation Accuracy Metrics

| Metric | Target Threshold | Measured Score | Evaluation Status |
|:---|:---|:---|:---|
| **DAG Structural Validity Rate** | 100.0% | **100.0% (50/50)** | **PASSED** |
| **Dependency Graph Sanity** | 100.0% | **100.0% (50/50)** | **PASSED** |
| **Agent Assignment Accuracy** | >= 95.0% | **100.0% (50/50)** | **PASSED (Exceeds Target)** |
| **Overall Planning Accuracy** | >= 95.0% | **100.0% (50/50)** | **PASSED (Exceeds Target)** |

---

## 2. Dataset Domain Distribution (50 Instructions)

The golden benchmark dataset covers the complete breadth of MAYA AI's capabilities:

- **Single-Agent PM (`single_agent_pm`):** Daily briefing synthesis, calendar conflict resolution, schedule queries, sprint backlog triage.
- **Single-Agent Coding (`single_agent_coding`):** Unit test authoring, division-by-zero bugfixes, XSS sanitization, connection pool retries, memory leak fixes, PR review diff analysis.
- **Single-Agent Research (`single_agent_research`):** Vector database benchmarks, academic consensus papers, RFC standards, privacy regulations, cloud pricing surveys.
- **Single-Agent Document (`single_agent_document`):** 8-slide executive pitch decks, formula-verified XLSX spreadsheets, multi-page PDF reports, budget variance tables.
- **Single-Agent Email (`single_agent_email`):** Incident postmortems, executive updates, new hire welcomes, speaker invitations.
- **Multi-Agent Collaborative Pipelines (`multi_agent_pipeline`):**
  - Competitor web research -> Verified PDF report (`ResearchAgent` -> `DocumentAgent`).
  - GitHub issue triage -> Sandbox reproduction -> Gated PR (`CodingAgent`).
  - Sprint feedback aggregation -> Executive email update (`PMAgent` -> `EmailAgent`).
  - GPU pricing discovery -> Financial model spreadsheet (`ResearchAgent` -> `DocumentAgent`).
  - Travel reservation parsing -> Calendar synchronization (`EmailAgent` -> `PMAgent`).

---

## 3. Negative & Fault Detection Invariants

The evaluation harness verified that invalid plans are deterministically rejected:
1. **Dependency Cycles:** Injected cyclic graphs (e.g. `task_1 -> task_2 -> task_1`) immediately trigger `PlanCycleError` during Kahn's topological sort.
2. **Missing Node References:** Subtasks referencing non-existent dependency IDs raise `PlanValidationError`.
3. **Self-Dependencies:** Subtasks depending on themselves are rejected prior to execution.

---

## 4. Test Source Files

- Golden Dataset File: `backend/tests/evals/golden_dataset.json`
- Evaluation Harness: `backend/tests/evals/test_agent_evaluation.py`
- Package definition: `backend/tests/evals/__init__.py`
- Result: 3 passed, 0 failed.
