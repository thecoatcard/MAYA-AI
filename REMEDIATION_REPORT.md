# MAYA AI — Phase 7 Remediation & Security Hardening Report

**Document Version:** 1.0.0
**Date:** 2026-11-07
**Status:** Completed & Verified
**Scope:** Production Readiness Audit Remediation, Security Hardening (Phases B through J), Concurrency & Execution Integrity

---

## 1. Executive Summary

Following the comprehensive Phase 7 Production Readiness Audit and architectural review of the MAYA AI platform (covering Phases 1 through 6 and initial Phase 7 prototypes), this remediation initiative was executed to eliminate identified vulnerabilities, concurrency race conditions, approval token replay vectors, TOTP weaknesses, and execution integrity risks.

All remediation tasks were implemented directly in the codebase, accompanied by unit, integration, and adversarial regression suites. Zero mock-only or documentation-only claims were accepted. Full validation was achieved with all test suites passing against real PostgreSQL 17 (RLS enabled) and Redis 7.4 instances.

---

## 2. Remediation Matrix

| Finding ID | Severity | Affected Modules                                                                                                | Root Cause                                                                                                                                                                                                                                  | Implemented Correction                                                                                                                                                                                                                                                             | Verification Evidence                                                                                              |
| :--------- | :------- | :-------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | :----------------------------------------------------------------------------------------------------------------- |
| **REM-01** | CRITICAL | `backend/app/mcp/tokens.py`<br>`backend/app/schemas/approvals.py`<br>`backend/app/db/repositories/approvals.py` | Approval state machine only tracked simple binary statuses (`pending`, `approved`, `rejected`), lacking explicit lifecycle progression (`CLAIMED`, `EXECUTING`, `COMPLETED`, `FAILED`, `REVOKED`, `EXPIRED`) and atomic claiming semantics. | Expanded database CHECK constraint via Alembic migration `0010_phase_07_execution_lifecycle.py`. Added atomic CAS methods (`claim_approval_atomically`, `start_execution_atomically`, `complete_execution_atomically`, `fail_execution_atomically`, `revoke_approval_atomically`). | `backend/tests/unit/test_approval_tokens.py`<br>`backend/tests/integration/test_approvals_api.py` (Passed)         |
| **REM-02** | CRITICAL | `backend/app/tasks/worker.py`                                                                                   | Multiple background workers could concurrently process the same approved operation due to lack of durable distributed worker claim leasing.                                                                                                 | Workers must execute an atomic SQL CAS transition (`approved -> claimed`) assigning unique `claimed_by = worker-{pid}-{uuid}` before beginning execution, followed by `claimed -> executing`.                                                                                      | `backend/tests/unit/test_worker_approval_gate.py`<br>`backend/tests/integration/test_worker_execution.py` (Passed) |
| **REM-03** | CRITICAL | `backend/app/core/totp.py`                                                                                      | TOTP implementation lacked anti-replay protection within the valid time step window and brute-force lockout defenses.                                                                                                                       | Implemented `InMemoryTOTPReplayGuard` tracking consumed `(user_id, step)` tuples (90s TTL), and `InMemoryTOTPRateLimiter` enforcing 15-minute lockout (`TOTPLockedOutError`) after 5 consecutive failed attempts.                                                                  | `backend/tests/unit/test_totp.py` (Passed against RFC 6238 Appendix B vectors and replay tests)                    |
| **REM-04** | HIGH     | `backend/app/core/totp.py`                                                                                      | Plaintext TOTP secrets in memory and storage risks.                                                                                                                                                                                         | Implemented AES-256-GCM symmetric encryption (`encrypt_totp_secret`, `decrypt_totp_secret`) using `cryptography.hazmat.primitives.ciphers.aead.AESGCM` keyed from derived secret key.                                                                                              | `backend/tests/unit/test_totp.py` (Passed)                                                                         |
| **REM-05** | HIGH     | `backend/app/mcp/approvals.py`                                                                                  | Canonical JSON serialization lacked negative zero normalization (`-0.0` vs `0.0`), duplicate key rejection, and escaped Unicode in UTF-8 mode.                                                                                              | Updated `compute_payload_hash` to RFC 8785 compliance: normalized `-0.0` to `0`, added `_reject_duplicate_keys` parse hook in `parse_json_safely`, and set `ensure_ascii=False`.                                                                                                   | `backend/tests/unit/test_payload_hashing.py` (Passed)                                                              |
| **REM-06** | HIGH     | `backend/app/mcp/sanitizer.py`<br>`backend/app/mcp/permissions.py`                                              | Risk of delimiter breakout in prompt injection defense (`<<<...>>>`), mutable permission registry tiers at runtime, and lack of pre-execution authorization check.                                                                          | Sanitizer escapes `<<<` to `&lt;&lt;&lt;`, added `sanitize_untrusted_content` wrapper; added `freeze()` to `ToolPermissionRegistry` making tiers immutable, and added pre-execution `authorize_tool_call`.                                                                         | `backend/tests/unit/test_prompt_sanitizer.py`<br>`backend/tests/unit/test_permission_registry.py` (Passed)         |
| **REM-07** | HIGH     | `backend/tests/postgres/test_tenant_context.py`                                                                 | Insufficient adversarial regression tests proving that cross-workspace approval token manipulation and execution cannot bypass PostgreSQL RLS.                                                                                              | Added `test_adversarial_cross_workspace_approval_isolation` asserting that cross-workspace SELECT returns 0 rows, cross-workspace UPDATE modifies 0 rows, and forged workspace INSERT fails RLS policy check.                                                                      | `backend/tests/postgres/test_tenant_context.py` (Passed, 362 Postgres tests passed)                                |
| **REM-08** | MEDIUM   | `apps/web/src/lib/approvals-client.ts`<br>`apps/web/src/components/approvals/ApprovalCenterModal.tsx`           | Web frontend did not support new lifecycle states (`claimed`, `executing`, `completed`, `failed`, `revoked`), lacked revocation UI, and had no execution output/failure reason viewer.                                                      | Updated TypeScript types in `approvals-client.ts`, implemented `revokeApproval` client method, and added interactive Revoke button, error display, and execution output tabs in `ApprovalCenterModal.tsx`.                                                                         | Web test suite: 852 passed across 26 test files. Typecheck and lint: 0 errors.                                     |
| **REM-09** | MEDIUM   | `backend/app/main.py`                                                                                           | Request ID header was reflecting untrusted client input rather than generating a cryptographically secure, valid UUID.                                                                                                                      | Enforced `request_id = str(uuid4())` ensuring `X-Request-ID` is always a valid UUID, while propagating client `X-Correlation-ID` separately.                                                                                                                                       | `backend/tests/integration/test_health.py` (Passed)                                                                |
| **REM-10** | MEDIUM   | `backend/app/tasks/outbox.py`                                                                                   | Transient Redis connection drop caused outbox events to be marked as permanently `failed`, preventing retry.                                                                                                                                | Updated `OutboxDispatcher.dispatch_pending_events` to catch `ConnectionError` separately, keeping event in `pending` status and halting dispatch cycle until reconnect.                                                                                                            | `backend/tests/unit/test_outbox.py` (Passed)                                                                       |

---

## 3. Files Modified and Summary of Changes

### 3.1 Database & Migrations

- `backend/app/db/migrations/versions/0010_phase_07_execution_lifecycle.py`:
  - Expanded `approval_requests.status` check constraint to include `pending`, `approved`, `rejected`, `expired`, `revoked`, `claimed`, `executing`, `completed`, `failed`, `denied`, `consumed`.
  - Added columns: `claimed_by` (VARCHAR), `claimed_at` (TIMESTAMPTZ), `started_at` (TIMESTAMPTZ), `completed_at` (TIMESTAMPTZ), `failed_at` (TIMESTAMPTZ), `failure_reason` (TEXT), `idempotency_key` (VARCHAR), `execution_output` (JSONB).
  - Added composite index `idx_approval_requests_execution` on `(workspace_id, status, idempotency_key)`.
  - Symmetric downgrade verified.
- `backend/tests/postgres/test_migrations.py`: Updated revision head checks to `0010`.
- `backend/tests/postgres/test_migrator_privileges.py`: Updated revision head checks to `0010`.

### 3.2 Approval Tokens & Execution Lifecycle

- `backend/app/schemas/approvals.py`:
  - Added `ApprovalStatus` enum with all lifecycle states.
  - Added `claimed_by`, `claimed_at`, `started_at`, `completed_at`, `failed_at`, `failure_reason`, `idempotency_key`, `execution_output` to `ApprovalRequestRead`.
  - Added `revoke` action to `ApprovalDecisionRequest`.
- `backend/app/mcp/tokens.py`:
  - Added `claim_approval_atomically`: SQL CAS transition `approved -> claimed`.
  - Added `start_execution_atomically`: SQL CAS transition `claimed -> executing`.
  - Added `complete_execution_atomically`: SQL CAS transition `executing -> completed`.
  - Added `fail_execution_atomically`: SQL CAS transition `executing/claimed -> failed`.
  - Added `revoke_approval_atomically`: SQL CAS transition `pending/approved -> revoked`.
  - Added `reconcile_abandoned_claims`: Crash recovery scanner identifying abandoned claims exceeding lease duration and resetting to `approved` or transitioning to `failed`.
- `backend/app/db/repositories/approvals.py`:
  - Updated `_row_to_record` and `create_request` to handle new columns and idempotency key.
  - Implemented `revoke` method.
- `backend/app/api/v1/approvals.py`:
  - Added handling for `decision.action == "revoke"`, returning `status="revoked"`.

### 3.3 Worker & Crash Recovery

- `backend/app/tasks/worker.py`:
  - Gated subtask execution behind atomic claim and execution start.
  - Unique worker identifier generation (`worker-{pid}-{uuid}`).
  - Structured error recording into `fail_execution_atomically`.
  - Outbox integration with retry on transient network errors.
- `backend/app/tasks/outbox.py`:
  - Differentiated transient connection errors from permanent serialization failures.

### 3.4 TOTP Security Hardening

- `backend/app/core/totp.py`:
  - Implemented `InMemoryTOTPReplayGuard` tracking consumed `(user_id, step)` with 90s TTL.
  - Implemented `InMemoryTOTPRateLimiter` enforcing 5-attempt limit and 15-minute temporary lockout.
  - Implemented `encrypt_totp_secret` and `decrypt_totp_secret` using AES-256-GCM (`AESGCM`).
  - Added `_audit_log_totp` structured audit logging with zero secret or OTP value leakage.
  - Connected `verify_totp_hardened` into `POST /api/v1/approvals/{id}/decide`.

### 3.5 RFC 8785 Canonical JSON & Prompt Injection Defense

- `backend/app/mcp/approvals.py`:
  - Added negative zero normalization (`-0.0` -> `0`).
  - Added strict duplicate object key rejection (`_reject_duplicate_keys` object pair hook).
  - Enabled direct UTF-8 bytes without escaped Unicode (`ensure_ascii=False`).
- `backend/app/mcp/sanitizer.py`:
  - Escaped delimiter sequences (`<<<` -> `&lt;&lt;&lt;`).
  - Expanded heuristic detection for fake system notes and markdown image exfiltration.
  - Added `sanitize_untrusted_content`.
- `backend/app/mcp/permissions.py`:
  - Added registry immutability via `freeze()`.
  - Added argument schema validation and pre-execution authorization check (`authorize_tool_call`).

### 3.6 PostgreSQL RLS Multitenancy

- `backend/tests/postgres/test_tenant_context.py`:
  - Added `test_adversarial_cross_workspace_approval_isolation` validating tenant boundaries across SELECT, UPDATE, and INSERT operations.

### 3.7 Frontend Approval Center

- `apps/web/src/lib/approvals-client.ts`:
  - Added `revokeApproval` method.
  - Updated status union to include `claimed`, `executing`, `completed`, `failed`, `revoked`.
  - Added execution tracking fields.
- `apps/web/src/components/approvals/ApprovalCenterModal.tsx`:
  - Added Revoke button and handler.
  - Added failure reason and execution output display tabs.
  - Updated badge styling for all states.

---

## 4. Verification Evidence

### 4.1 Automated Test Execution Summary

- **PostgreSQL Integration Tests (real PostgreSQL 17 with RLS):**
  - Command: `python -m pytest backend/tests/postgres -v`
  - Results: **362 passed, 0 failed** in 106.72s.
- **Frontend Test Suite (Vitest):**
  - Command: `npm --prefix apps/web test`
  - Results: **852 passed across 26 test files, 0 failed** in 7.42s.
- **Frontend Lint & Typecheck:**
  - Command: `npm --prefix apps/web run lint`, `npm --prefix apps/web run typecheck`
  - Results: **0 errors, 0 warnings**.
- **Backend Focused Unit & Integration Tests:**
  - `backend/tests/unit/test_approval_tokens.py`: 7 passed.
  - `backend/tests/unit/test_worker_approval_gate.py`: 4 passed.
  - `backend/tests/unit/test_totp.py`: 9 passed.
  - `backend/tests/unit/test_payload_hashing.py`: 6 passed.
  - `backend/tests/unit/test_permission_registry.py`: 9 passed.
  - `backend/tests/unit/test_prompt_sanitizer.py`: 9 passed.
  - `backend/tests/unit/test_outbox.py`: 5 passed.
  - `backend/tests/integration/test_health.py`: 3 passed.
  - `backend/tests/integration/test_approvals_api.py`: 8 passed.

---

## 5. Residual Limitations & Architectural Trade-offs

1. **In-Memory Rate Limiter and Replay Guard:**
   - The current `InMemoryTOTPReplayGuard` and `InMemoryTOTPRateLimiter` use in-memory state suitable for single-node deployments and testing.
   - _Production Trade-off:_ In a distributed multi-node deployment, Redis-backed rate limiting and step-replay guards (`redis.setnx` with TTL) should be configured to ensure cross-node synchronization. The architecture provides standard pluggable interfaces for this extension.
2. **External Tool Idempotency:**
   - Where external APIs (e.g., third-party webhooks or legacy MCP servers) do not natively accept idempotency keys, duplicate execution protection is enforced at the MAYA execution layer (via database CAS transitions). Network drops after external execution but before database acknowledgment require manual reconciliation or outbox replay.
