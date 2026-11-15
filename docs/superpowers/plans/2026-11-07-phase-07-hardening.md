# Phase 7 Security Hardening, Production Readiness Remediation & Phase 8 Preparation Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

## Overview

This plan implements the comprehensive engineering remediation of Maya AI's Phase 7 implementation and Phase 1-6 foundations. It eliminates concurrency risks in approval execution, adds full explicit execution lifecycle states (`PENDING`, `APPROVED`, `REJECTED`, `EXPIRED`, `REVOKED`, `CLAIMED`, `EXECUTING`, `COMPLETED`, `FAILED`), hardens RFC 6238 TOTP with replay guard and lockout, strengthens prompt injection and tool authorization defenses, audits PostgreSQL RLS tenant isolation, refines RFC 8785 canonical JSON serialization, and prepares the operational foundation for Phase 8.

---

## Task 1: Baseline Verification and Audit Discovery (Phase A)

- [ ] Run fast validation: `npm run validate:fast`
- [ ] Run governance test suite: `npm test`
- [ ] Run backend postgres test suite: `pytest backend/tests/postgres`
- [ ] Run web frontend suite: `npm --prefix apps/web test`
- [ ] Record exact baseline metrics and findings into `REMEDIATION_REPORT.md`

## Task 2: Database Migration 0010 for Execution Lifecycle & Replay Protection (Phase B & C)

- [ ] Create `backend/app/db/migrations/versions/0010_phase_07_execution_lifecycle.py`:
  - Expand `approval_requests.status` CHECK constraint to include:
    `'pending', 'approved', 'rejected', 'expired', 'revoked', 'claimed', 'executing', 'completed', 'failed', 'denied', 'consumed'`
  - Add execution tracking columns to `approval_requests`:
    `claimed_by VARCHAR(100)`, `claimed_at TIMESTAMPTZ`, `started_at TIMESTAMPTZ`, `completed_at TIMESTAMPTZ`, `failed_at TIMESTAMPTZ`, `failure_reason TEXT`, `idempotency_key VARCHAR(128)`, `execution_output JSONB`
  - Add composite index `idx_approval_requests_execution` on `(workspace_id, status, expires_at)`
- [ ] Update `backend/tests/postgres/test_migrations.py` and `backend/tests/postgres/test_migration_cli.py` to cover revision 0010 bidirectional upgrade/downgrade.
- [ ] Run migration upgrade and verify database schema symmetry.

## Task 3: Approval Lifecycle State Machine & Worker CAS Engine (Phase B)

- [ ] Update `ApprovalStatus` enum in `backend/app/schemas/approvals.py` to include:
      `PENDING`, `APPROVED`, `REJECTED`, `EXPIRED`, `REVOKED`, `CLAIMED`, `EXECUTING`, `COMPLETED`, `FAILED` (with backwards compatibility aliases).
- [ ] In `backend/app/mcp/tokens.py`, implement atomic lifecycle CAS functions:
  - `claim_approval_for_execution`: Atomic CAS `status = 'approved'` $\rightarrow$ `'claimed'`, setting `claimed_by` and `claimed_at`.
  - `start_execution`: Atomic CAS `status = 'claimed'` $\rightarrow$ `'executing'`, setting `started_at`.
  - `complete_execution`: Atomic CAS `status = 'executing'` $\rightarrow$ `'completed'`, setting `completed_at` and `execution_output`.
  - `fail_execution`: Atomic CAS `status = 'executing'` $\rightarrow$ `'failed'`, setting `failed_at` and `failure_reason`.
  - `revoke_approval_request`: Atomic CAS `status = 'pending' | 'approved'` $\rightarrow$ `'revoked'`.
- [ ] In `backend/app/db/repositories/approvals.py`, add repository methods for claim, start, complete, fail, and revoke.
- [ ] In `backend/app/api/v1/approvals.py`, add `POST /api/v1/approvals/{id}/revoke` endpoint.
- [ ] In `backend/app/tasks/worker.py`, integrate full lifecycle transitions:
  - When worker consumes token, transition approval to `CLAIMED` by `worker_id`.
  - If claim fails (already claimed or revoked), abort execution and mark subtask.
  - When tool execution begins, transition approval to `EXECUTING`.
  - When tool execution finishes, transition approval to `COMPLETED` or `FAILED`.
- [ ] Add unit and integration tests in `backend/tests/unit/test_approval_tokens.py` and `backend/tests/unit/test_worker_approval_gate.py`.

## Task 4: RFC 6238 TOTP Security Hardening (Phase C)

- [ ] In `backend/app/core/totp.py`:
  - Add RFC 6238 Appendix B test vectors in `backend/tests/unit/test_totp.py` (verifying official 8-digit and 6-digit test vectors).
  - Implement `TOTPReplayGuard`:
    Track consumed time-steps per user (`totp:replay:{user_id}:{step}`) with 90s TTL to prevent token replay within the $\pm 1$ step clock-skew window.
  - Implement `TOTPRateLimiter`:
    Track consecutive failures per user with progressive backoff and 15-minute temporary lockout after 5 failed attempts.
  - Implement encrypted secret helpers `encrypt_totp_secret` and `decrypt_totp_secret` using AES-256-GCM authenticated encryption.
  - Add audit logging for TOTP verification attempts with zero disclosure of codes or secrets.
- [ ] Write unit tests for replay prevention, lockout, encryption, and skew in `backend/tests/unit/test_totp.py`.

## Task 5: RFC 8785 Canonical JSON & Payload Integrity (Phase F)

- [ ] In `backend/app/mcp/approvals.py`:
  - Implement negative zero normalization: `-0.0` $\rightarrow$ `0`.
  - Implement float exponential formatting canonicalization matching RFC 8785.
  - Add duplicate key rejection in payload parsing.
  - Verify deterministic key sorting, nested object ordering, and UTF-8 handling.
- [ ] In `backend/tests/unit/test_payload_hashing.py`:
  - Add test vectors for negative zero, nested arrays, unicode normalization, decimal formatting, and duplicate keys.
  - Add cross-language compatibility verification test.

## Task 6: Prompt Injection Defense & Server-Side Tool Authorization (Phase D)

- [ ] In `backend/app/mcp/permissions.py`:
  - Make `ToolPermissionRegistry` immutable at runtime after initialization.
  - Add strict argument schema validator hook for registered tools.
  - Add independent pre-execution authorization check.
- [ ] In `backend/app/mcp/sanitizer.py`:
  - Add defense against nested delimiters and delimiter spoofing.
  - Add tool output validation before returning to model gateway or agent loop.
  - Add audit log emission for detected prompt injection attempts.
- [ ] In `backend/tests/unit/test_prompt_sanitizer.py` and `backend/tests/unit/test_permission_registry.py`:
  - Add adversarial tests for Unicode homoglyphs, BiDi overrides, nested injection tags, and privilege escalation attempts.

## Task 7: PostgreSQL RLS Multitenancy Isolation Audit & Adversarial Tests (Phase E)

- [ ] In `backend/tests/postgres/test_tenant_context.py` and `test_roles.py`:
  - Add adversarial tests verifying empty, unset, or malicious `app.workspace_id` yields 0 rows and rejects inserts.
  - Add test verifying that connection pooling does not leak workspace context across clients.
  - Add test verifying that the runtime role (`maya_app`) lacks `BYPASSRLS`.
  - Add test verifying cross-tenant foreign key cascade isolation.

## Task 8: Observability, Metrics & Queue Reliability (Phase G & H)

- [ ] Add request correlation ID header (`X-Correlation-ID`) handling in API and worker logging.
- [ ] In `backend/app/tasks/worker.py`:
  - Add bounded exponential backoff and dead-letter handling for poison tasks.
  - Add worker crash recovery routine that detects abandoned claims (`claimed` > 5 min without heartbeat).
- [ ] In `backend/app/core/config.py`:
  - Add key version identifier support (e.g. `v1:secret`) for `MAYA_JWT_SIGNING_KEY` and `MAYA_OTP_HMAC_KEY`.
  - Ensure fail-closed behavior on missing production keys.

## Task 9: Frontend Approval Center Hardening (Phase I)

- [ ] In `apps/web/src/lib/approvals-client.ts`:
  - Update status types to support `pending`, `approved`, `rejected`, `expired`, `revoked`, `claimed`, `executing`, `completed`, `failed`.
  - Add `revokeApproval(id)` client method.
- [ ] In ApprovalCenterModal (removed; approvals are now inline on the task card):
  - Display full status badges with appropriate visual semantics.
  - Add Revoke button for pending and approved requests.
  - Show execution output or failure reason when available.
  - Enhance accessibility (ARIA live regions, focus trap, keyboard navigation).
- [ ] Update frontend tests in `ApprovalCenterModal.test.tsx` and `approvals-client.test.ts`.

## Task 10: Regression Testing, Verification & Governance Gates (Phase K & M)

- [ ] Run `backend/.venv/Scripts/python -m pytest backend/tests/postgres`
- [ ] Run `backend/.venv/Scripts/python -m pytest backend -q`
- [ ] Run `npm --prefix apps/web test`
- [ ] Run `npm --prefix apps/web run lint`
- [ ] Run `npm --prefix apps/web run typecheck`
- [ ] Run `npm run validate:fast`
- [ ] Run `npm test`

## Task 11: Production Readiness Deliverables (Phase L & N)

- [ ] Generate `REMEDIATION_REPORT.md`
- [ ] Generate `SECURITY_AUDIT_REPORT.md`
- [ ] Generate `PRODUCTION_READINESS_REPORT.md`
- [ ] Generate `PHASE8_READINESS.md`
- [ ] Update `CHANGELOG.md` and documentation
