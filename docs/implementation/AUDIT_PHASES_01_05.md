# Phase 01–05 adversarial audit

**Status: Partially tested; remediation and independent verification are in progress.**
This report distinguishes observed failures, code-review findings and planned behavior.
It is not a proof that every repository path has been inspected or that no other defects
exist.

The audit used local, deterministic tests and synthetic provider, Redis and checkpoint
doubles. It did not contact PostgreSQL, Redis, Gemini or SMTP, and it did not perform
authenticated browser acceptance. Secret and environment files were not inspected.
Phase 05 is an unwired prototype; findings in that prototype are not claims of a
currently exposed production endpoint.

## Security review findings

Confidence describes the evidence for the stated failure mode, not the likelihood of
exploitation. Prototype-only risks are explicitly identified.

| #   | Severity  | File                                         | Lines            | Vulnerability                                                                                                                                                                                                                                                                                                              | Confidence |
| --- | --------- | -------------------------------------------- | ---------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------- |
| 1   | 🟠 HIGH   | `backend/app/core/security.py`               | 81–89            | Cancellation releases password-work capacity before the worker finishes, enabling resource exhaustion.                                                                                                                                                                                                                     | 10/10      |
| 2   | 🟠 HIGH   | `backend/app/auth/postgres.py`               | 82–90            | A login concurrent with password reset can create a session missed by revocation; live PostgreSQL ordering remains unverified.                                                                                                                                                                                             | 8/10       |
| 3   | 🟠 HIGH   | `apps/web/src/lib/auth-session.ts`           | 76, 132          | A delayed refresh can overwrite a newer signed-in or signed-out identity; cross-tab cookie behavior remains unverified.                                                                                                                                                                                                    | 9/10       |
| 4   | 🟠 HIGH   | `scripts/validation/check-config-syntax.mjs` | 47–61            | The config validator can read ignored JSON or YAML files and include sensitive input in diagnostics.                                                                                                                                                                                                                       | 9/10       |
| 5   | 🟠 HIGH   | `backend/app/gateway/cost_tracker.py`        | 76–124, 197–221  | Separate budget checks and accounting allow concurrent billable calls beyond the configured cap.                                                                                                                                                                                                                           | 10/10      |
| 6   | 🟠 HIGH   | `backend/app/gateway/cost_tracker.py`        | 104–117, 197–221 | Unknown-price calls increase an unpriced counter but can continue without increasing the enforced monetary total.                                                                                                                                                                                                          | 10/10      |
| 7   | 🟠 HIGH   | `backend/app/agents/checkpointer.py`         | 67–76            | The unwired prototype retrieves checkpoints using a caller-supplied thread ID without an owner check.                                                                                                                                                                                                                      | 9/10       |
| 8   | 🟠 HIGH   | `backend/app/agents/orchestrator.py`         | 173–175          | RESOLVED: The unwired prototype could route an already-in-progress approval-required subtask without enforcing a human decision. Resolved by trapping in-progress, pending and runnable approval-required tasks to `wait_for_approval`, demoting to `awaiting_approval`, requesting human approval, and pausing the graph. | 10/10      |
| 9   | 🟡 MEDIUM | `backend/app/services/projection_service.py` | 18–27            | RESOLVED: Every task and subtask upsert establishes both `workspace_id` and `user_id` ownership in SQL parameters, ON CONFLICT user matching clauses, and subtask parent-task existence filters, enforcing tenant isolation matching migrations 0005 and 0006.                                                             | 10/10      |

## Architectural gaps

| Decision | Current gap / Status                                                                                                                                                                                                                                                                                          |
| :------- | :------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------ |
| D3       | PLANNED: The separate voice and task runtimes and their Redis bridge (Phase 12).                                                                                                                                                                                                                              |
| D4       | RESOLVED: Admin-owned, deny-by-default tool-permission registry (`ToolPermissionRegistry`, 4 tiers: READ, WRITE, APPROVAL_REQUIRED, RESTRICTED; server hints elevation-only).                                                                                                                                 |
| D5       | RESOLVED: Authenticated UI approval with signed, single-use, payload-bound HS256 tokens (`mint_approval_token`, `verify_approval_token`, `consume_approval_atomically`), RFC 6238 TOTP step-up authentication, channel isolation (voice 403), and frontend Approval Center modal (`ApprovalCenterModal.tsx`). |
| D6       | RESOLVED: Checkpointing and relational projections with PostgreSQL RLS enforcement (`AsyncPostgresSaver`, `projection_service.py`) and schema migrations 0005/0006.                                                                                                                                           |
| D8       | RESOLVED: Async subtask worker runner (`SubtaskWorker`) enforcing 1 subtask per queued job, atomic approval token consumption, and zero blocking on human approvals (immediate clean exit upon `awaiting_approval`).                                                                                          |
