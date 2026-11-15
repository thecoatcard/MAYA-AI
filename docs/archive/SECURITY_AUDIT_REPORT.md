> **Historical document (archived 2026-11-09).** This report describes the state of the project at the time it was written and is kept for the record only. It is not current: some of its readiness claims were later found to be incomplete (see `docs/implementation/IMPLEMENTATION_STATUS.md` and the Unreleased section of `CHANGELOG.md` for the current state).

# MAYA AI — Phase 7 Comprehensive Security Audit Report

**Document Version:** 1.0.0
**Date:** 2026-11-07
**Classification:** Internal Technical Audit
**Scope:** Threat Model, Approval Token Cryptography, TOTP Hardening, Prompt Injection & MCP Isolation, PostgreSQL RLS Multi-Tenancy

---

## 1. Executive Summary

This comprehensive security audit assesses the security architecture, threat model, and defense-in-depth posture of MAYA AI following the Phase 7 security hardening initiative. The audit covers the entire pipeline across Phases 1 through 7, with focused evaluation of the Human-in-the-Loop (HITL) approval protocol, execution integrity, cryptographic payload verification, TOTP step-up authentication, prompt injection defenses, and database multi-tenant isolation.

The system enforces a **Zero-Trust AI Agent** security model: LLM outputs and agent actions are treated as untrusted and unprivileged until validated against strict schemas, authorized against immutable permission registries, and explicitly approved by authenticated human operators via out-of-band or step-up channels when modifying state or executing external side effects.

---

## 2. Threat Modeling & Attack Surface Analysis

The threat model identifies five primary adversarial threat vectors:

### 2.1 Adversarial Prompt Injection & Delimiter Breakout (STRIDE: Tampering / Elevation of Privilege)

- **Threat:** Malicious inputs from untrusted external sources (scraped web pages, document uploads, third-party API responses, or malicious user prompts) containing instructions designed to override agent system prompts, manipulate tool calls, or exfiltrate private context.
- **Defenses Implemented:**
  1. _Multi-stage sanitization:_ Stripping NULL bytes, ANSI escape sequences, BiDi directional control characters, and Unicode NFKC normalization.
  2. _Structural escaping:_ Replacing `<<<` with `&lt;&lt;&lt;` preventing delimiter escaping.
  3. _Structural wrapping:_ Encapsulating external content within `<untrusted_external_content>` tags.
  4. _Deterministic Heuristic Scanners:_ Regex pattern scanning targeting jailbreak indicators, fake system notes, and Markdown image exfiltration URLs.
  5. _Authorization Independence:_ Tool execution decisions are strictly evaluated by server-side permission registries and human approval tokens; LLM confidence or prompt instructions never bypass server gates.

### 2.2 Approval Token Forgery, Replay, and Race Conditions (STRIDE: Spoofing / Tampering / Repudiation)

- **Threat:** An attacker intercepting an approval token, replaying it after expiration, forging claims for a different workspace/tool, or racing multiple worker nodes to trigger duplicate side effects.
- **Defenses Implemented:**
  1. _Cryptographic Payload Verification:_ Deterministic SHA-256 payload hashing adhering to RFC 8785 JSON canonicalization (sorted keys, no whitespace, negative zero normalization, strict duplicate key rejection, direct UTF-8).
  2. _Short-Lived Signed JWTs:_ HS256 JWTs with a maximum lifetime of 300 seconds (5 minutes), containing workspace, tool, and payload hash claims.
  3. _PostgreSQL Atomic CAS Transitions:_ Workers must execute single-row Compare-And-Swap transitions (`UPDATE ... WHERE status = 'approved' AND id = :id`).
  4. _Explicit Lifecycle State Machine:_ Rigid status progression (`pending -> approved -> claimed -> executing -> completed / failed / revoked / expired`). Once claimed or revoked, a token cannot be re-consumed.
  5. _Idempotency Keys:_ Durable recording of idempotency keys in `approval_requests.idempotency_key` indexed with workspace isolation.

### 2.3 TOTP Step-Up Authentication Weaknesses (STRIDE: Spoofing / Information Disclosure)

- **Threat:** Brute-force attacks against the 6-digit TOTP code, token replay within the 30-second time window, or compromise of TOTP shared secrets at rest.
- **Defenses Implemented:**
  1. _RFC 6238 Appendix B Compliance:_ Validated against standard test vectors using standard-library HMAC-SHA1 and dynamic truncation.
  2. _Anti-Replay Guard:_ Durable tracking of consumed `(user_id, step)` tuples preventing reuse within the valid time step window.
  3. _Progressive Rate Limiting & Lockout:_ Immediate 15-minute temporary lockout (`TOTPLockedOutError`) upon 5 consecutive failed attempts.
  4. _AES-256-GCM Encryption at Rest:_ Shared secrets encrypted using AES-256-GCM authenticated encryption before persistence; plaintext secrets never stored.
  5. _Zero Credential Leakage Logging:_ Structured audit logs redact OTP values and secret material, logging only hashed user IDs, event types, and timestamps.

### 2.4 Multi-Tenant Cross-Workspace Data Leaks (STRIDE: Information Disclosure / Elevation of Privilege)

- **Threat:** A compromised or malicious tenant attempting to access, approve, or execute operations in another tenant's workspace via crafted IDs or parameter tampering.
- **Defenses Implemented:**
  1. _Forced PostgreSQL Row-Level Security (RLS):_ Every table (including `approval_requests`, `tasks`, `sessions`, `workspaces`, `audit_log`) has active RLS policies enforcing `workspace_id = current_setting('app.workspace_id', true)::uuid`.
  2. _Composite Foreign Keys:_ All references strictly enforce `(workspace_id, id)` composite integrity.
  3. _Least-Privilege Database Roles:_ Runtime operations execute under `maya_app` with restricted DML grants and zero access to sensitive password hashes.
  4. _Adversarial Verification:_ Dedicated test suites verify cross-workspace SELECT returns 0 rows, cross-workspace UPDATE modifies 0 rows, and forged workspace INSERT fails RLS policy checks.

### 2.5 Voice Channel Bypass (STRIDE: Elevation of Privilege)

- **Threat:** An attacker using synthetic voice or audio-prompt injection to grant high-risk tool approvals without visual human oversight.
- **Defenses Implemented:**
  1. _Channel Isolation:_ `POST /api/v1/approvals/{id}/decide` inspects the `X-Maya-Channel` header. Any attempt to authorize via `voice` is rejected with `HTTP 403 Forbidden` (`Voice channel cannot authorize gated operations`).

---

## 3. Cryptographic and Implementation Review

### 3.1 RFC 8785 Canonical JSON Serialization

All payload hashes are calculated deterministically:

```python
def canonical_json(data: Any) -> bytes:
    # 1. Negative zero normalization: -0.0 -> 0
    # 2. Key sorting: sort_keys=True
    # 3. No extraneous whitespace: separators=(',', ':')
    # 4. Strict UTF-8: ensure_ascii=False
    # 5. Duplicate key rejection in deserializer
```

Adversarial tests confirm that key reordering, float normalization (`-0.0` vs `0.0`), and whitespace differences produce identical SHA-256 hashes, while payload modifications alter the hash and invalidate the approval token.

### 3.2 Worker Claiming & Crash Recovery Architecture

The worker execution gate follows an atomic two-phase lease:

```
[approved]
   | (claim_approval_atomically - worker-{pid}-{uuid})
   v
[claimed]
   | (start_execution_atomically)
   v
[executing]
   |
   +---> [completed] (on successful execution)
   |
   +---> [failed] (on caught exception with reason)
```

In the event of an ungraceful worker node crash during `claimed` or `executing`, the `reconcile_abandoned_claims` background process detects leases older than `CLAIM_TIMEOUT` (default: 300s) and safely transitions them to `failed` or resets to `approved` if idempotent, preventing orphaned tasks.

---

## 4. PostgreSQL RLS & Role Privilege Audit

| Entity / Role      | Configuration / Rule                                                                                            | Audit Finding                                                  |
| :----------------- | :-------------------------------------------------------------------------------------------------------------- | :------------------------------------------------------------- |
| `maya_migrator`    | Schema migrations only; owning role. Never used at runtime.                                                     | **Compliant:** Verified via `test_migrator_privileges.py`.     |
| `maya_app`         | Runtime execution role; lacks `SUPERUSER`, lacks table creation grants, lacks read access to `hashed_password`. | **Compliant:** Grants restricted to DML on specific columns.   |
| Row-Level Security | `FORCE ROW LEVEL SECURITY` enabled on all 15 relational tables.                                                 | **Compliant:** Verified across 362 PostgreSQL tests.           |
| Tenant Boundary    | `app.workspace_id` session configuration enforced inside transactions.                                          | **Compliant:** Verified via adversarial cross-workspace tests. |

---

## 5. Residual Risks and Future Hardening Recommendations

1. **Distributed Rate Limiting:**
   - Migrate in-memory rate limiting and replay guards to a Redis-backed Lua script cluster for multi-region or distributed deployments.
2. **Hardware Security Module (HSM) / KMS Integration:**
   - In enterprise production environments, integrate AWS KMS, HashiCorp Vault, or GCP Cloud KMS for wrapping TOTP secret encryption keys and JWT signing keys.
3. **FIDO2 / WebAuthn Hardware Tokens:**
   - Supplement RFC 6238 TOTP with FIDO2/WebAuthn hardware keys (YubiKey) for step-up authentication on `RESTRICTED` operations.
