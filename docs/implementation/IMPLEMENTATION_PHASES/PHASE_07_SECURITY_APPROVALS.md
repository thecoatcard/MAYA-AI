# Phase 07 — Security, Permissions, and Human Approval

## A. Phase Overview

- **Phase Number:** Phase 07
- **Phase Name:** Security, Permissions, and Human Approval
- **Objective:** Implement the central four-tier permission authorization gate, the cryptographic payload-bound human approval protocol, atomic single-use token consumption, step-up authentication for RESTRICTED operations, the Approval Center API, and the multi-layered prompt-injection defense barrier.
- **Why This Phase is Necessary:** As established in [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §11 and Decisions D4 and D5, autonomous agents must never be permitted to execute outward-facing or destructive actions without explicit, verifiable human authorization. Prompt delimiters and model instruction alone are advisory; true security boundaries must be enforced in deterministic backend code that cryptographically binds approvals to the exact action payload.
- **Expected Outcome:** A deny-by-default permission gate intercepting all tool invocations; canonical SHA-256 payload hashing; signed single-use approval tokens (JWT, exp <= 5 min); atomic `UPDATE ... RETURNING` consumption preventing TOCTOU races; TOTP step-up authentication for RESTRICTED actions; and the Approval Center REST API.
- **Priority:** Critical
- **Estimated Complexity:** High
- **Estimated Duration:** 10 business days (Weeks 10–11 of master schedule).

---

## B. Prerequisites

- **Required Completed Phases:** Phase 02 (Authentication, Users, Workspaces, and Database Security) and Phase 06 (Background Task Execution Engine).
- **Required Infrastructure:** PostgreSQL 17 with `approval_requests` and `audit_log` tables; Redis 7.4.
- **Required Credentials or External Services:** JWT signing secret; TOTP authenticator secret generator.
- **Required Architectural Decisions:** [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) Decisions D4 (Four permission tiers, deny-by-default, server hints cannot lower tier) and D5 (Approvals bound to exact payload hash, UI-only grant, voice deny-only).
- **Required Documentation:** [`governance/SECURITY_POLICY.md`](../../../governance/SECURITY_POLICY.md), [`governance/TOOL_PERMISSIONS.md`](../../../governance/TOOL_PERMISSIONS.md).

---

## C. Scope

### Included Features

- Four permission tiers implemented in code:
  - 🟢 **READ**: Autonomous (read workspace files, git status/log, `SELECT`, web search, read email).
  - 🔵 **WRITE**: Autonomous, reversible, strictly scoped to `/artifacts` sandbox (local branch, draft email, document generation).
  - 🟡 **APPROVAL_REQUIRED**: Human approval required for each exact payload (send email, push commits, open PR, modify user files outside `/artifacts`).
  - 🔴 **RESTRICTED**: UI approval plus step-up authentication (DDL `DROP/ALTER`, bulk delete, force-push, token creation, payment).
- Deny-by-default policy: unclassified tools are automatically disabled. Server hints (`readOnlyHint`, `destructiveHint`) can only elevate a tier, never lower it.
- Exact-Payload Canonicalization and Hashing: `payload_hash = SHA256(canonical_json{tool, args, workspace_id, user_id, idempotency_key})`.
- Human Approval Protocol:
  - State pauses; `approval_requests` row created with human-readable diff.
  - User reviews exact payload in UI and approves via `POST /api/v1/approvals/{id}`.
  - Server mints single-use signed token (`exp` <= 5 minutes, claims: `request_id`, `tool_name`, `payload_hash`, `user_id`, `jti`).
- Atomic Consumption & TOCTOU Defense:
  - Gate recomputes SHA-256 hash from incoming tool execution arguments.
  - Executes atomic state transition:
    ```sql
    UPDATE approval_requests SET status = 'consumed', consumed_at = now()
    WHERE id = $1 AND status = 'approved' AND expires_at > now() AND payload_hash = $2
    RETURNING id;
    ```
- Voice Approval Prohibition (Decision D5): Voice channel can announce, read back, or deny/cancel; it is blocked in code from granting approvals.
- Step-Up Authentication: TOTP / password re-entry required for RESTRICTED tier actions.
- Multi-Action Batch Confirmations: One user click mints N individual signed tokens, each bound to its distinct payload hash.

### Excluded Features

- Biometric or speaker verification voice approvals (explicitly deferred to post-MVP).
- Physical MCP subprocess connection management (deferred to Phase 08).
- Specialist agent workflow execution (deferred to Phase 09).

### Deferred Features

- Multi-party quorum approvals (e.g., 2 of 3 admins required for enterprise deployments).
- Hardware security key (WebAuthn/FIDO2) integration for step-up auth (post-MVP).

### Dependencies on Future Phases

- Phase 08 (MCP Gateway) and Phase 09 (Specialist Agents) route every mutating tool invocation directly through this permission gate.

---

## D. Implementation Breakdown

### Task P07-T001: Central Tool Permission Registry

- **Task ID:** P07-T001
- **Task Name:** Implement Permission Registry and Tier Classifier
- **Detailed Description:** Create backend/app/mcp/permissions.py (planned artifact). Define `ToolPermissionRegistry` storing tier classifications for all available tools. Implement method `evaluate_tool_tier(tool_name: str, server_hints: dict | None = None) -> PermissionTier`:
  - Look up tool in registry. If not registered, return `DISABLED` (deny-by-default).
  - If server hints specify higher criticality than registry, elevate tier.
  - If server hints specify lower criticality, ignore hints and preserve registry tier.
- **Expected Files:** backend/app/mcp/permissions.py (planned artifact), backend/tests/unit/test_permission_registry.py (planned artifact).
- **Dependencies:** Phase 02.
- **Assigned Agent Role:** Security & Compliance Architect.
- **Required Tests:** `pytest backend/tests/unit/test_permission_registry.py`.
- **Documentation Updates:** docs/security/PERMISSION_GATE.md (planned artifact).
- **Acceptance Criteria:** Unregistered tools return disabled; server cannot downgrade an APPROVAL_REQUIRED tool to READ; elevation hints function correctly.

### Task P07-T002: Canonical Payload Serializer and SHA-256 Hasher

- **Task ID:** P07-T002
- **Task Name:** Implement Canonical JSON Payload Hashing
- **Detailed Description:** Implement `hash_tool_payload(tool_name: str, args: dict, workspace_id: UUID, user_id: UUID, idempotency_key: str) -> str` in backend/app/mcp/approvals.py (planned artifact). Normalize dictionary keys recursively in lexicographical order, format numbers and strings deterministically, and compute the SHA-256 hex digest. Implement diff generator formatting JSON/text diffs for human review.
- **Expected Files:** backend/app/mcp/approvals.py (planned artifact), backend/tests/unit/test_payload_hashing.py (planned artifact).
- **Dependencies:** P07-T001.
- **Assigned Agent Role:** Cryptography Specialist.
- **Required Tests:** `pytest backend/tests/unit/test_payload_hashing.py` (whitespace variations, key order independence, nested structures).
- **Documentation Updates:** docs/security/APPROVAL_PROTOCOL.md (planned artifact).
- **Acceptance Criteria:** Identical logical payloads produce identical SHA-256 hashes regardless of dictionary key ordering; any mutation in argument values alters the hash.

### Task P07-T003: Approval Token Minting and Atomic Verification Engine

- **Task ID:** P07-T003
- **Task Name:** Implement Single-Use Signed Token Lifecycle
- **Detailed Description:** Implement token minting using HMAC-SHA256 with JWT structure containing claims: `request_id`, `tool_name`, `payload_hash`, `user_id`, `jti`, and `exp` (<= 5 minutes). Implement `consume_approval_token(db, token: str, actual_tool: str, actual_args: dict)`:
  - Recompute SHA-256 hash from incoming execution payload.
  - Verify token signature, expiration, and payload hash match.
  - Execute atomic database update setting `status = 'consumed'`.
  - Record audit log entry.
- **Expected Files:** backend/app/mcp/approvals.py (planned artifact), backend/tests/integration/test_approval_tokens.py (planned artifact).
- **Dependencies:** P07-T002, Phase 02.
- **Assigned Agent Role:** Security Systems Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_approval_tokens.py` (replay attack, tampered payload, expired token, concurrent consumption).
- **Documentation Updates:** docs/security/APPROVAL_PROTOCOL.md (planned artifact).
- **Acceptance Criteria:** Replayed tokens are rejected; tampered arguments fail hash check; expired tokens are denied; concurrent consume attempts result in exactly 1 success.

### Task P07-T004: Approval Center API and Step-Up TOTP Authentication

- **Task ID:** P07-T004
- **Task Name:** Build Approval Center Endpoints and Step-Up Verification
- **Detailed Description:** Create backend/app/api/v1/approvals.py (planned artifact):
  - `GET /api/v1/approvals`: List pending approval requests for user's workspace.
  - `GET /api/v1/approvals/{id}`: Detailed request view with `payload_diff`.
  - `POST /api/v1/approvals/{id}`: Approve or deny with decision.
    - If `permission_level == 'RESTRICTED'`, require `totp_code` or password verification.
    - Validate that caller is an authenticated web session (reject requests originating from voice audio channel).
  - On approval, transition status to `approved` and mint signed token.
  - On denial, transition status to `denied`, mark subtask failed, and invoke saga compensations.
- **Expected Files:** backend/app/api/v1/approvals.py (planned artifact), backend/app/schemas/approvals.py (planned artifact), backend/app/core/totp.py (planned artifact).
- **Dependencies:** P07-T003.
- **Assigned Agent Role:** API Developer & Security Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_approvals_api.py`.
- **Documentation Updates:** docs/api/APPROVALS_SPECIFICATION.md (planned artifact).
- **Acceptance Criteria:** API returns pending requests; voice channel origin header is rejected; RESTRICTED requires valid TOTP code; approval mints valid single-use token.

### Task P07-T005: Layered Prompt-Injection Guardrail Middleware

- **Task ID:** P07-T005
- **Task Name:** Implement Prompt Injection Sanitizer and Output Delimiters
- **Detailed Description:** Implement backend/app/mcp/sanitizer.py (planned artifact). Wrap all external tool outputs (web scrape, email bodies, file contents) in `<<<UNTRUSTED_EXTERNAL_INPUT>>>` delimiters. Strip null bytes, ANSI escape codes, and hidden Unicode directional markers. Implement lightweight regex and structural scanner detecting system prompt override attempts (e.g., "Ignore previous instructions").
- **Expected Files:** backend/app/mcp/sanitizer.py (planned artifact), backend/tests/unit/test_prompt_sanitizer.py (planned artifact).
- **Dependencies:** P07-T001.
- **Assigned Agent Role:** AI Security Researcher.
- **Required Tests:** `pytest backend/tests/unit/test_prompt_sanitizer.py` (testing diverse prompt injection benchmarks).
- **Documentation Updates:** docs/security/PROMPT_INJECTION_DEFENSE.md (planned artifact).
- **Acceptance Criteria:** Control characters stripped; untrusted data wrapped in delimiters; attempts to inject prompt overrides are flagged and sanitized.

---

## E. File and Folder Changes

- **Files to Create:**
  - backend/app/mcp/__init__.py (planned artifact)
  - backend/app/mcp/permissions.py (planned artifact)
  - backend/app/mcp/approvals.py (planned artifact)
  - backend/app/mcp/sanitizer.py (planned artifact)
  - backend/app/core/totp.py (planned artifact)
  - backend/app/api/v1/approvals.py (planned artifact)
  - backend/app/schemas/approvals.py (planned artifact)
  - backend/tests/unit/test_permission_registry.py (planned artifact)
  - backend/tests/unit/test_payload_hashing.py (planned artifact)
  - backend/tests/unit/test_prompt_sanitizer.py (planned artifact)
  - backend/tests/integration/test_approval_tokens.py (planned artifact)
  - backend/tests/integration/test_approvals_api.py (planned artifact)
  - docs/security/PERMISSION_GATE.md (planned artifact)
  - docs/security/APPROVAL_PROTOCOL.md (planned artifact)
  - docs/security/PROMPT_INJECTION_DEFENSE.md (planned artifact)
- **Files to Modify:**
  - backend/app/api/v1/router.py (planned artifact)
  - `backend/app/main.py`
- **Files to Avoid Modifying:**
  - `MAYA_AI_FINAL_PLAN.md`
- **Relevant Directories:** `backend/app/mcp/`, `backend/app/api/v1/`, `docs/security/`.

---

## F. API and Data Contracts

### 1. Approval Decision Contract

- **`POST /api/v1/approvals/{id}`**
  - Headers: `Authorization: Bearer <jwt>`, `X-Client-Channel: web`
  - Request:
    ```json
    {
      "decision": "approve",
      "totp_code": "123456"
    }
    ```
  - Response (`200 OK`):
    ```json
    {
      "approval_id": "3c90e29b-8422-4414-b659-1981cb60d703",
      "status": "approved",
      "approval_token": "dummy-approval-jwt-example",
      "expires_at": "2026-11-05T14:35:00Z"
    }
    ```

---

## G. Security Requirements

- **Voice Approval Ineligibility:** Header or session context indicating `channel: voice` must be rejected with HTTP 403 when calling approval decision endpoints (Decision D5).
- **TOCTOU Elimination:** Hash re-calculation occurs at the instant of execution immediately prior to tool dispatch.
- **Atomic Single-Use:** Token consumption executes in an atomic transaction; database constraints guarantee a token cannot be consumed twice.
- **Step-Up Authentication:** RESTRICTED operations require verified TOTP or password confirmation within the last 5 minutes.

---

## H. Testing Strategy

- **Property-Based Security Tests:** Generate 1,000 randomized tool calls with varying parameters; assert that zero calls with tier >= APPROVAL_REQUIRED reach execution without an approval token.
- **Replay & Concurrency Tests:** Simultaneously submit identical approval tokens across 20 threads; assert exactly one succeeds and 19 fail with HTTP 409/400.
- **Tampering Tests:** Modify one character in argument dictionary after approval; assert execution gate detects hash mismatch and halts execution.

---

## I. Definition of Done

1. Permission registry strictly enforces deny-by-default for unclassified tools.
2. Server hints cannot lower permission tiers.
3. Approval requests persist in PostgreSQL with canonical SHA-256 payload hashes.
4. Single-use approval tokens consume atomically with TOCTOU protection.
5. Voice channel requests to approve actions are rejected with HTTP 403.
6. RESTRICTED operations enforce valid TOTP step-up authentication.
7. Documentation in docs/security/APPROVAL_PROTOCOL.md (planned artifact) is complete.

---

## J. Phase Exit Gate

- **Demonstrated Proof:** `pytest backend/tests/integration/test_approval_tokens.py` passes 100%; property test verifies zero unapproved mutating actions reach execution.
- **Dependent Phase Unlocked:** Phase 08 (MCP Gateway and Integrations) is cleared to begin exposing write-capable tools.
