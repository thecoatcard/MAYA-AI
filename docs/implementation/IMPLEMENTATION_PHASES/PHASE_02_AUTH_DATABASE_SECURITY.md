# Phase 02 — Authentication, Users, Workspaces, and Database Security

## A. Phase Overview

- **Phase Number:** Phase 02
- **Phase Name:** Authentication, Users, Workspaces, and Database Security
- **Objective:** Establish the multi-tenant database schema and PostgreSQL Row-Level Security (RLS), implement tenant isolation, and build own-account authentication using the owner's decisions: email verification by one-time code, username/password login, and emailed-code password recovery.
- **Why This Phase is Necessary:** The account and tenant foundations must be tested before chat and downstream data access are exposed. RLS and user-level ownership checks are required to prevent cross-tenant and cross-user access.
- **Expected Outcome:** Alembic migrations for the plan's schema and OTP challenge storage; RLS on the plan's 11 tenant-scoped tables; a restricted `maya_app` role; and the owner-approved account and OTP flows. The plan lists 12 core tables; report OTP storage and RLS coverage separately.
- **Priority:** Critical
- **Estimated Complexity:** High
- **Estimated Duration:** 10 business days.

---

## B. Prerequisites

- **Required Completed Phases:** Phase 01 (Core Infrastructure and Application Foundation).
- **Required Infrastructure:** Running PostgreSQL 17 container with `vector` and `pg_trgm` extensions enabled.
- **Required Credentials or External Services:** Safe development placeholders for JWT/OTP keys and SMTP configuration. Real credentials must remain in the untracked local `.env`, must never be read by an agent, and must not be used to contact an external service without explicit approval.
- **Required Architectural Decisions:** [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §14 (Final DDL), with the owner's account decisions in [`START_HERE.md`](../START_HERE.md) superseding Open Decision O2. Google OIDC is not the login provider.
- **Required Documentation:** The planned data-model and security documentation, the configuration reference and `.env.example`, and an ADR for material architecture decisions.

---

## C. Scope

### Included Features

- Alembic database migration setting up the 12 tables in the final plan plus an OTP challenge table; explicitly determine whether the OTP table is tenant-scoped and apply appropriate isolation.
- Row-Level Security (RLS) policies configured and forced across all 11 tenant-scoped tables using session parameter `app.workspace_id`.
- Dedicated application database role (`maya_app`) stripped of table ownership and `BYPASSRLS` privileges.
- A narrowly privileged `SECURITY DEFINER` function for account lookup before workspace context is bound, with a fixed safe `search_path`.
- Unique username, verified email, and non-null Argon2id password hash; no nullable password path for Google login.
- Registration, OTP verification, username/password login, forgot/reset password, refresh, and logout endpoints. Define their request/response behavior and revoke all sessions after password reset.
- OTPs are six digits, stored only as an HMAC under a server-side key, expire after 10 minutes, allow at most five attempts, have a resend cooldown, are single-use, invalidate prior codes on resend, and are never logged. Responses do not reveal whether an email exists; rate-limit by email and source address.
- JWT token generator and validator (claims: `sub`, `workspace_id`, `role`, `exp`, `jti`).

### Excluded Features

- LLM model invocation or routing (deferred to Phase 03).
- LangGraph task execution and queue workers (deferred to Phases 05 and 06).
- S3 artifact file uploads (deferred to Phase 10).
- Vector similarity search queries (deferred to Phase 11).
- Secrets-vault implementation and tamper-evident audit-chain enforcement (deferred to the governance phase; see Phase 07).

### Deferred Features

- Enterprise SAML/Okta SSO integration (explicitly post-MVP).
- Automated hardware security module (HSM) key rotation pipeline (deferred to Phase 16).

### Dependencies on Future Phases

- All future data persistence (chat messages in Phase 04, tasks in Phase 05, MCP servers in Phase 08, memory in Phase 11) directly relies on the database models and RLS context created here.

---

## D. Implementation Breakdown

### Task P02-T001: Alembic Setup and Core Schema Migration

- **Task ID:** P02-T001
- **Task Name:** Create Initial Alembic Migrations for the MAYA AI Schema
- **Detailed Description:** Initialize Alembic in `backend/app/db/alembic/`. Implement reversible migrations creating the extensions (`vector`, `pg_trgm`), 12 tables defined in [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §14, and a purpose-built OTP challenge table. Ensure `tasks` enforces `UNIQUE (workspace_id, idempotency_key)` and confirm the OTP table's RLS scope before implementation.
- **Expected Files:** backend/app/db/alembic/env.py (planned artifact), backend/app/db/alembic/versions/0001_initial_schema.py (planned artifact), `backend/app/db/models/*.py`.
- **Dependencies:** Phase 01 completion.
- **Assigned Agent Role:** Database Architect.
- **Required Tests:** `alembic upgrade head` followed by `alembic downgrade base` and re-upgrade.
- **Documentation Updates:** The data-model documentation, once created.
- **Acceptance Criteria:** Migrations run cleanly forward and backward; plan-defined tables, keys, foreign keys, indexes, OTP storage, and RLS scope are documented and tested.

### Task P02-T002: Row-Level Security (RLS) Configuration and Role Isolation

- **Task ID:** P02-T002
- **Task Name:** Implement RLS Policies and Restricted Application Role
- **Detailed Description:** In migration `0002_rls_policies.py`, enable and force RLS on all 11 plan-defined tenant tables. Create a non-owner `maya_app` role without `BYPASSRLS` and grant least privilege. Audit-table mutation policy belongs to the later governance phase. Any `SECURITY DEFINER` account-lookup function must use a fixed safe `search_path` and minimal rights.
- **Expected Files:** backend/app/db/alembic/versions/0002_rls_policies.py (planned artifact), `backend/app/db/session.py`.
- **Dependencies:** P02-T001.
- **Assigned Agent Role:** Database Security Specialist.
- **Required Tests:** `pytest backend/tests/integration/test_rls_isolation.py`.
- **Documentation Updates:** The database-isolation documentation, once created.
- **Acceptance Criteria:** Connecting as `maya_app` without `app.workspace_id` set returns 0 rows on all tenant queries; cross-workspace and same-workspace/different-user tests return no unauthorized rows.

### Task P02-T003: FastAPI Database Session Middleware with Tenant Context

- **Task ID:** P02-T003
- **Task Name:** Implement Workspace Context Middleware
- **Detailed Description:** Implement FastAPI dependency `get_db_session` in `backend/app/db/session.py` that extracts the authenticated user's `workspace_id` from request state, issues `SET LOCAL app.workspace_id = :ws_id` on the connection before yielding the session, and rolls back or resets on completion.
- **Expected Files:** `backend/app/db/session.py`, backend/app/core/dependencies.py (planned artifact).
- **Dependencies:** P02-T002.
- **Assigned Agent Role:** Backend Systems Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_db_session_middleware.py`.
- **Documentation Updates:** `docs/architecture/SYSTEM_OVERVIEW.md`.
- **Acceptance Criteria:** Every database query executed within an authenticated request automatically inherits the correct `app.workspace_id`; unauthenticated requests default to unset/empty (denying access to tenant rows).

### Task P02-T004: User Authentication and JWT Management

- **Task ID:** P02-T004
- **Task Name:** Implement Password Hashing, JWT Issuance, and Auth Endpoints
- **Detailed Description:** Implement password hashing directly with a maintained Argon2id library after dependency approval. Implement JWT creation and verification with distinct access/refresh token types and claims scoped to the user and workspace. Implement registration, email-code verification, username/password login, forgot/reset password, refresh, and logout. Do not implement Google OIDC login.
- **Expected Files:** backend/app/core/security.py (planned artifact), backend/app/api/v1/auth.py (planned artifact), backend/app/schemas/auth.py (planned artifact).
- **Dependencies:** P02-T003.
- **Assigned Agent Role:** Security & Auth Engineer.
- **Required Tests:** Unit and integration coverage for each auth flow, OTP expiry/attempt ceiling/resend/single-use/invalidation, enumeration-resistant responses, per-email and per-address rate limits, session revocation after password reset, and invalid/expired/replayed tokens.
- **Documentation Updates:** Authentication API documentation, created when this API is approved and implemented.
- **Acceptance Criteria:** Registration creates an unverified account and OTP challenge; only a verified email can complete username/password login; recovery and reset use OTP; refresh and logout have explicit token/session revocation semantics; reset revokes every active session.

### Task P02-T005: Deferred — Secrets Vault

- **Task ID:** P02-T005
- **Task Name:** Move vault design and implementation to the governance phase
- **Detailed Description:** No vault code is implemented in Phase 02. The master plan places the secrets vault under P4 Governance; reconcile its detailed task ownership with Phase 07 before implementation.
- **Expected Files:** None in Phase 02.
- **Dependencies:** Phase 07 plan alignment.
- **Assigned Agent Role:** Security Engineer.
- **Required Tests:** Define in Phase 07.
- **Documentation Updates:** Define in Phase 07.
- **Acceptance Criteria:** This deferred task is not counted toward the Phase 02 exit gate.

### Task P02-T006: Deferred — Audit-Chain Enforcement

- **Task ID:** P02-T006
- **Task Name:** Move audit-chain enforcement to the governance phase
- **Detailed Description:** Phase 02 may create the plan-defined `audit_log` table, but does not implement hash chaining or append-only enforcement. The master plan places these controls under P4 Governance; reconcile detailed task ownership with Phase 07 before implementation.
- **Expected Files:** None in Phase 02.
- **Dependencies:** Phase 07 plan alignment.
- **Assigned Agent Role:** Security Engineer.
- **Required Tests:** Define in Phase 07.
- **Documentation Updates:** Define in Phase 07.
- **Acceptance Criteria:** This deferred task is not counted toward the Phase 02 exit gate.

---

## E. File and Folder Changes

- **Files to Create:**
  - backend/app/db/alembic/env.py (planned artifact)
  - backend/app/db/alembic/script.py.mako (planned artifact)
  - backend/app/db/alembic/versions/0001_initial_schema.py (planned artifact)
  - backend/app/db/alembic/versions/0002_rls_policies.py (planned artifact)
  - backend/app/db/models/__init__.py (planned artifact)
  - backend/app/db/models/workspace.py (planned artifact)
  - backend/app/db/models/user.py (planned artifact)
  - backend/app/db/models/session.py (planned artifact)
  - backend/app/db/models/message.py (planned artifact)
  - backend/app/db/models/task.py (planned artifact)
  - backend/app/db/models/approval.py (planned artifact)
  - backend/app/db/models/compensation.py (planned artifact)
  - backend/app/db/models/audit.py (planned artifact)
  - backend/app/db/models/mcp.py (planned artifact)
  - backend/app/db/models/artifact.py (planned artifact)
  - backend/app/db/models/memory.py (planned artifact)
  - OTP challenge model and migration (path to be selected with the schema design)
  - backend/app/core/security.py (planned artifact)
  - backend/app/core/dependencies.py (planned artifact)
  - backend/app/schemas/auth.py (planned artifact)
  - OTP challenge service and tests
  - backend/app/api/v1/auth.py (planned artifact)
  - backend/tests/unit/test_auth_security.py (planned artifact)
  - backend/tests/integration/test_rls_isolation.py (planned artifact)
  - backend/tests/integration/test_auth_endpoints.py (planned artifact)
- **Files to Modify:**
  - backend/app/api/v1/router.py (planned artifact)
  - `backend/app/main.py`
  - data-model documentation
- **Files to Avoid Modifying:**
  - `MAYA_AI_FINAL_PLAN.md`
  - `scripts/hooks/*`
- **Relevant Directories:** `backend/app/db/`, `backend/app/core/`, `backend/app/api/v1/`.

---

## F. API and Data Contracts

### Authentication contract to define before implementation

The required flows are:

- `POST /api/v1/auth/register` — create an account using username, email, and password; send a verification code.
- `POST /api/v1/auth/verify-email` — verify the email with its OTP.
- `POST /api/v1/auth/token` — login with username and password; no OTP at login.
- `POST /api/v1/auth/forgot-password` and `POST /api/v1/auth/reset-password` — use an emailed OTP and revoke all sessions after a successful reset.
- `POST /api/v1/auth/refresh` and `POST /api/v1/auth/logout` — define refresh and revocation semantics explicitly.

The exact request/response schemas and whether registration creates a workspace are not specified by the owner's decisions. Define them as an implementation contract before coding; do not retain the prior Google-OIDC or nullable-password assumptions. OTP handling must follow section C, and responses must be indistinguishable for known and unknown email addresses.

---

## G. Security Requirements

- **Authentication:** Hash passwords with Argon2id. Final password-strength policy must be selected and documented before implementation; do not rely on an unapproved complexity rule.
- **Authorization & RLS:** Enforce `SET LOCAL app.workspace_id = '<uuid>'` on all queries. Direct attempts to read across tenants must fail at the database engine level.
- **Secret Handling:** JWT signing and OTP HMAC keys are loaded from approved configuration and never logged or returned. Do not include real values in examples.
- **Audit:** Before implementation, decide how failed logins and OTP sends are recorded because they have no authenticated workspace context. This phase is blocked on that data/audit-boundary decision.
- **Workspace Visibility:** The plan does not enable RLS on `workspaces`. Resolve whether workspace names and slugs are intentionally visible across tenants before implementing workspace lookup or listing.

---

## H. Testing Strategy

- **Tenant Isolation Test Suite:** Create two workspaces (Tenant A, Tenant B) with distinct users and records; run query matrix asserting Tenant A receives 0 rows when attempting to select Tenant B entities under RLS.
- **SQL Injection & RLS Bypass Tests:** Attempt to inject `' OR '1'='1` or override `app.workspace_id` through query parameters; assert policy denies unauthorized records.

---

## I. Definition of Done

1. Alembic migrations run cleanly up and down against PostgreSQL 17.
2. Row-Level Security policies are active on all 11 plan-defined tenant tables, with OTP storage scope tested separately.
3. Cross-workspace and same-workspace/different-user tests prove isolation.
4. Registration, email verification, username/password login, forgot/reset, refresh, and logout pass positive and negative tests; OTP lifecycle and enumeration resistance are verified.
5. Account and RLS documentation matches the approved account flows and plan schema.

---

## J. Phase Exit Gate

- **Demonstrated Proof:** Migration, RLS, and auth/OTP tests pass with documented results.
- **Dependent Phase Unlocked:** Phases 05 and 06 may begin only after their other prerequisites are met; Phase 04 also requires Phase 03. Phases 07 and 11 retain their separate dependency gates.
