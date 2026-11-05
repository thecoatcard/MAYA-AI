# Start here: continuing MAYA AI implementation

## Phase 02 update (2026-11-05)

Phase 02 (14-table schema with forced row-level security and composite foreign keys, restricted runtime role, own-account
authentication) is **implemented and tested locally** on branch `feat/phase-02-auth-database-security` (pushed to the GitHub remote and merged into `main`
on 2026-11-05; branch protection is not enforceable on the current private repository plan), under the owner's recorded dependency-gate exception (the extended Gemini continuity item stays open). Start with
the [verification record](../testing/AUTH_AND_RLS.md), the [security document](../security/AUTHENTICATION.md) (with the
decisions on the security review) and ADR-0005/0006 (Accepted). The persistent local database is migrated (revision 0004,
empty). The owner reported (2026-11-05) that the SMTP check (`python -m app.tools.smtp_smoke`) and the registration,
verification and login flows work against the real mail account. Still open: no agent has observed a GitHub CI run of the
workflow yet.

## Current implementation update (2026-11-05)

The Phase 01 local implementation scope is **implemented and tested**, with a
completed user-run free-tier Gemini smoke. Extended lifetime/resumption
verification is approved and awaiting user-run evidence, not passed.
Paid-cap verification is excluded from revised Phase 01 scope by explicit owner
approval; it remains mandatory before future paid use. This is not full original-scope
verification. Backend settings/logging/health/resource lifecycles, the static Next.js
overview and local PostgreSQL/Redis configuration now exist. No auth, tenant schema,
models, chat, agents or voice features are implemented. Spikes S1/S2 now have
isolated measured reports; S3/S4 have offline harnesses and isolated actual SDK
adapters with mocked tests. The user subsequently ran the approved short smoke:
three embeddings had 768 dimensions and both text methods returned audio.
See the [live smoke evidence](../architecture/SPIKE_S3_GEMINI_LIVE.md#user-run-live-smoke-evidence-2026-11-05).
The owner has deferred paid spending-cap work, with a
[deferred development record](../architecture/SPIKE_S3_GEMINI_LIVE.md#deferred-development-record).
The INR 100 cap is not waived and paid runs remain blocked. See the
[queue report](../architecture/SPIKE_S1_QUEUE.md) and
[vector report](../architecture/SPIKE_S2_VECTOR.md). The owner accepted
ADR-0003: Celery with Kombu 5.7.0 resolves against the
current Redis client without downgrading it, and real transport/worker-loss tests
now pass in the isolated candidate. Terminal cancellation was not observed within
four seconds; Phase 06 must implement durable cooperative cancellation and
terminal reconciliation before acceptance. No runtime queue integration was
added. ARQ still has a dependency conflict.

Read [implementation status](IMPLEMENTATION_STATUS.md) and the
[local runbook](../operations/LOCAL_DEVELOPMENT.md) for the current state and commands.
The owner approved scaffolding, installation, local containers and a scoped
validator file-discovery correction. After initially approving S3/S4 live evaluation
and isolated SDK installation, the owner chose offline-only tooling pending verified
spending controls. The owner later approved actual isolated SDK adapters and a
compatibility audit, still without paid execution. See
[S3 readiness](../architecture/SPIKE_S3_GEMINI_LIVE.md) and
[S4 readiness](../architecture/SPIKE_S4_EMBEDDINGS.md). SDK installation was confined
to its separately locked spike project. Agents made no live calls; the user
subsequently executed the approved free-tier smoke. The owner now authorized
a local commit and extended user-run tests, but not a push.

For Phase 01 closure, the owner approved an isolated real benchmark of Celery
5.6.3/Kombu 5.7.0/redis-py 8.1.0 and correction of planned-file documentation
references, without changing application runtime pins or validators.
Those tasks are complete. Final fast validation passed all 8 checks; full
validation reported 10 passed, 0 failed and 4 comparison checks skipped for no
base branch. All 146 backend tests passed and documentation has no findings.
Use current status and the test strategy for evidence and deferred live work.

The original snapshot below is retained as historical context. Its statements that
there is no application code or that no review fixes were applied are now superseded.
The old 160-error documentation count predates both planning alignment and scaffolding;
it must not be used as the current validation result.

Entry point for a **new chat or a new AI agent**. It tells you where the project stands, what the owner has already decided, and what to read before touching a phase. Written 2026-11-05.

## 1. Read in this order

1. [AGENTS.md](../../AGENTS.md): the hard rules (permission levels, honest reporting, documentation with code).
2. This file.
3. [REVIEW_FINDINGS_2026-11-05.md](REVIEW_FINDINGS_2026-11-05.md): problems found in the phase documents. **None are fixed yet.**
4. [IMPLEMENTATION_ROADMAP.md](IMPLEMENTATION_ROADMAP.md) and [IMPLEMENTATION_DEPENDENCIES.md](IMPLEMENTATION_DEPENDENCIES.md): phase order. Their schedule figures are wrong (see the findings).
5. The phase file you are about to work on, in [IMPLEMENTATION_PHASES](IMPLEMENTATION_PHASES/README.md).
6. [MAYA_AI_FINAL_PLAN.md](../../MAYA_AI_FINAL_PLAN.md): the authoritative product plan. Do not edit it without the owner's approval.

[IMPLEMENTATION_STATUS.md](IMPLEMENTATION_STATUS.md) and [PHASE_ACCEPTANCE_CHECKLIST.md](PHASE_ACCEPTANCE_CHECKLIST.md) contain statements that are not true (for example "report signed", "7/7 checks"). Trust the findings file over them.

## 2. Where the project stands (2026-11-05)

| Area                         | State                                                                                                                                                                                                                  |
| :--------------------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Governance system (Phase 00) | Implemented and tested locally: rules, hooks, validators, 59 generated agent files, CI workflows. One commit by the owner (`304dcdc`).                                                                                 |
| CI                           | Written; the GitHub remote exists since 2026-11-05 but no run has been observed yet. Not a required check (see BRANCH_PROTECTION.md).                                                                                                                                                |
| ADRs 0001 and 0002           | Still "Proposed"; the owner has not accepted them.                                                                                                                                                                     |
| Application code             | **None.** Phase 01 has not started.                                                                                                                                                                                    |
| This reorganisation          | The implementation documents were moved into `docs/implementation/` and their links made relative. Not yet committed.                                                                                                  |
| `npm run docs:check`         | Fails with 160 errors, all in the phase documents, which name future documentation files in inline code. They are not broken links. The owner has not yet decided how to resolve this, so do not commit until they do. |

## 3. Machine and credentials

- **Python 3.12.10 is installed** at `%LOCALAPPDATA%\Programs\Python\Python312`. A terminal opened before the install may resolve `python` to the Microsoft Store stub; open a new terminal, or call the interpreter by its full path.
- Node.js 24, npm 11, Git 2.56 and Docker are present. Docker has not been tested here.
- The owner has a Gemini API key, a dedicated Gmail account and its App Password. They belong **only in the untracked `.env` file**. Never read, print, log or ask for them in chat. Claude Code's project settings deny reading `.env`.

## 4. Decisions made by the owner

| Topic                      | Decision                                                                                                                                                           |
| :------------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Sign-up                    | Email plus a one-time code (OTP) for verification.                                                                                                                 |
| Login                      | Username and password only, with no OTP at login.                                                                                                                  |
| Forgot and reset password  | Both use an emailed OTP.                                                                                                                                           |
| Risky (RESTRICTED) actions | Extra confirmation by emailed OTP, replacing the TOTP or password re-entry in Phase 07.                                                                            |
| Identity provider          | Own accounts. This supersedes the "Google OIDC" default (O2). Google OAuth is needed later only for the Gmail and Calendar integration in Phase 08, not for login. |
| OTP email                  | SMTP through the dedicated Gmail account's App Password, for development. A transactional email service is a later decision (Phase 16).                            |
| Documents                  | Live in `docs/implementation/`.                                                                                                                                    |
| Language                   | The owner writes Hindi, English or Hinglish. **Reply in Hinglish**; keep code, commands, file names and committed documents in English.                            |

**Not confirmed by the owner** (despite "ACCEPTED" in the status file): O1 (local Ollama support), O3 (team size), O4 (hosting), O5 (voice cannot approve), O6 (budget caps). Treat them as recommendations and ask before relying on them.

OTP design that Phase 02 should follow (not yet written into the phase): 6 digits, stored only as an HMAC with a server-side key, 10-minute expiry, at most 5 attempts, resend cooldown, single use, previous codes invalidated, identical responses whether or not the email exists, rate limits per email and per address, all sessions revoked after a password reset, and the code never logged.

## 5. How to begin a phase

1. Read the phase's section in the findings file, then the phase file itself.
2. Check prerequisites by running them (versions, tools), not by reading the status file.
3. Follow the plan-feature workflow in `governance/workflows/plan-feature.md` (Claude Code: `/gov-plan-feature`). Write the plan, list every Level 3 action, and get the owner's approval.
4. Fix the phase document's errors **with the owner's agreement**, then implement test-first with small slices.
5. Level 3 actions need explicit approval first: new dependencies, anything under the Docker folder, new `package.json` files, workflow changes, migrations, authentication and authorisation code.
6. Every change updates its documentation, `.env.example` and the configuration reference for new variables, and needs an ADR when it sets an architecture-level direction.
7. Commit only when the owner asks. Commits touching guardrail files need a `Human-Approval:` trailer.
8. Report honestly: only claim a check passed if you ran it.

## 6. Suggested first message for a new chat

> `docs/implementation/START_HERE.md` padho, phir `AGENTS.md`. Phase 01 ke liye pehle plan banao (review findings zaroor dekhna). Kuch bhi install, commit ya push karne se pehle mujhse approval lo. Jawab Hinglish mein dena.
