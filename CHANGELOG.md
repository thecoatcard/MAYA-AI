# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Newest entries first; unreleased work goes under "Unreleased".

## [Unreleased]

### Added (Phase 02, owner gate exception recorded in the status document)

- Database schema by four reversible Alembic revisions: the plan's 12 tables plus `auth_otp_challenges` and `auth_sessions`, all 14 with forced row-level security keyed by a transaction-local `app.workspace_id` (including `workspaces`, so tenants cannot list each other); least-privilege grants for the restricted `maya_app` role (no `hashed_password` read, state-column-only updates on the authentication tables, append-only `audit_log` grants); two `SECURITY DEFINER` lookup functions with a fixed `search_path`; a startup guard that refuses to run as a privileged or owning role. Applied to disposable test databases and, with the owner's approval after a schema-only backup, to the persistent local database (revision 0004, empty).
- Own-account authentication: registration with e-mailed one-time-code verification (the code **and the account's password** are needed, defeating pre-hijacking of an address), username/password login (Argon2id), password recovery by code with all sessions revoked, refresh-token rotation with generation-based replay detection, logout, and `GET /auth/me`; enumeration-identical responses, HMAC-stored single-use attempt-limited codes, Redis rate limits that fail closed, standard-library SMTP over mandatory TLS, and a request-size cap. See `docs/api/AUTH.md`, `docs/security/AUTHENTICATION.md`, ADR-0005 and ADR-0006 (both Accepted by the owner).
- Composite foreign keys on every user/session/task/subtask reference (a cross-workspace reference or cascade is impossible), a migration that also works for a non-superuser owner (testing it found and fixed a real defect), and an owner-run SMTP check tool (`python -m app.tools.smtp_smoke`) with secret-free failure categories.
- CI: `governance.yml` now starts digest-pinned PostgreSQL 17 and Redis 7.4 service containers, installs the locked backend environment and the web dependencies, and runs with required-service mode, so the service tests cannot be silently skipped. Written and linted only; no run has been observed yet.
- Dependencies (exact pins, resolver-checked, lock changed by additions only): `alembic` 1.20.1, `argon2-cffi` 25.1.0, `pyjwt` 2.15.1, and their transitive packages.
- 864 backend tests (502 without services); service tests run on disposable `maya_test_*` databases created from the local containers. The persistent local database was migrated (empty) after a schema-only backup, and the real API was probed against it. An independent security review's findings were fixed with regression tests; its dispositions are in the security document. ADR-0005 and ADR-0006 are Accepted. Not CI-verified. No agent contacted a mail server; the owner reported running the SMTP check and the account flows successfully against the real mail account.

### Changed

- Branch protection status is now documented honestly: GitHub does not enforce rulesets on this private repository on the current plan, so the protection table in `docs/operations/BRANCH_PROTECTION.md` is a target configuration, and `governance/GIT_WORKFLOW.md`, `governance/README.md` and `README.md` no longer say `main` is protected. The owner chose to stay private on the current plan for now; the trigger to revisit is listed in that document.
- Settings now require `MAYA_JWT_SIGNING_KEY` and `MAYA_OTP_HMAC_KEY` (at least 32 bytes each, different from each other); optional token lifetimes and `MAYA_SMTP_*` settings were added to `.env.example` and the configuration reference. Unknown routes and validation failures now return `{"error": ...}` bodies.

### Fixed

- Gemini free-tier continuity launcher: after a user run disconnected with close code 1008 at 124 seconds before any GoAway (inactivity suspected, not proven), the adapter now sends a fixed nonce-free synthetic turn every 60 seconds while no turn is outstanding (at most 15 per session, 90 seconds cumulative output PCM), keeps one pending receive, concatenates transcript deltas, and records only allowlisted close/failure categories. Mock-tested only (148 isolated checks); continuity evidence remains pending.
- Environment-variable documentation checks now inspect tracked and non-ignored source files instead of generated Next.js bundles and Python dependency environments. Regression tests retain checks for undocumented source variables, including tracked files matched by ignore patterns.
- Phase 01 documentation now matches generic health bodies, resource-test path and direct router mounting. The owner revised acceptance to free-tier-only, retaining provider-enforced paid caps before future paid use; extended lifetime/continuity evidence remains pending.
- All 587 nonexistent future-artifact references in Phases 02–16 are explicitly labelled planned without removing paths or requirements, creating dummy files, or changing validators; documentation validation now passes.

### Added

- Extended user-run free-tier lifecycle launcher with bounded real-GoAway resumption and synthetic nonce continuity verification through AUDIO output transcription. 111 isolated mocked checks pass; extended live evidence still requires user execution.

- Phase 01 local foundation: typed FastAPI settings, redacted JSON logs, async data-service lifecycles and health probes, a pinned Next.js 15/React 19 web shell, deterministic tests and dependency lockfiles.
- Digest-pinned local PostgreSQL 17/pgvector and Redis 7.4 AOF infrastructure, loopback-only ports, separate runtime login, and foundation setup/API/architecture documentation.
- Opt-in S1/S2 synthetic benchmark harnesses, deterministic tests and raw measured reports: isolated Celery/ARQ with LangGraph, plus workspace/user-filtered pgvector HNSW against exact ground truth. ADR-0003 is owner-accepted for the compatible Celery candidate. The initial S2 perfect-recall run was invalidated and superseded after correcting prepared-plan reuse.
- S3/S4 tooling: offline PCM/event replay and embedding analysis, plus separately locked google-genai 2.34.0 async SDK adapters for independent text sessions, bounded resumption/cleanup and three separate 768-dimension embedding requests. Mocked validation and a subsequent user-run short live smoke are documented; paid execution remains blocked pending the owner's INR 100 hard spending-cap requirement.
- User-run free-tier Gemini smoke evidence: three actual 768-dimensional vectors and audio from both separate text methods (484/453 ms first audio), completed in 11.078 seconds. Lifetime/resumption, independent billing verification and paid caps remain unverified; no full live-acceptance claim is made.
- Separately approved user-run free-tier smoke launcher with interactive no-billing confirmations, hidden key input, fixed Developer API endpoint, disabled retries/reconnects/fallback, bounded requests and redacted evidence. Model calls remain unexecuted by agents; billing status is user-attested, not programmatically verified.
- Phase 01 compatible queue validation: isolated Celery 5.6.3/Kombu 5.7.0/redis-py 8.1.0 completed 80 tasks and recovered after worker loss without runtime downgrades. Native revoke did not yield observed terminal cancellation within four seconds. ADR-0003 is owner-accepted; Phase 06 must implement durable cooperative cancellation and terminal reconciliation.

- Canonical AI-agent instructions in `AGENTS.md`, with thin entry points for Claude Code, GitHub Copilot, Google Antigravity, Cursor, Gemini CLI and OpenAI Codex, verified against vendor documentation (`docs/governance/AI_AGENT_COMPATIBILITY.md`).
- Governance policies, 12 reusable workflows and 9 development roles under `governance/`, plus a generator that produces the per-tool skills, prompts, rules and subagents and fails on drift.
- Shared Git hooks (`pre-commit`, `commit-msg`, `pre-push`) and an equivalent GitHub Actions pipeline, both built on one set of validators: documentation, secrets, blocked files, config syntax, workflow security, commit messages, and documentation/ADR/changelog change rules.
- Claude Code permission settings (`deny` for secret files and bypass flags, `ask` for approval-level actions).
- Documentation templates, ADR process with two initial ADRs, onboarding, configuration reference, troubleshooting and branch protection guides.
- Repository audit (`docs/governance/REPOSITORY_AUDIT.md`) and setup validation report (`docs/governance/SETUP_VALIDATION_REPORT.md`).
- Initial project plan, `MAYA_AI_FINAL_PLAN.md`.
- Implementation phase plans, roadmap, dependencies, status, acceptance checklist and agent assignments under `docs/implementation/`, with `START_HERE.md` as the entry point for a new session and `REVIEW_FINDINGS_2026-11-05.md` recording review findings; a subset of Phase 01–04 findings has since been addressed, with remaining blockers noted in the report.
