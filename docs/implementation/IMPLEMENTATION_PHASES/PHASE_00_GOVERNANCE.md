# Phase 00 — Project Governance and Development Environment

## A. Phase Overview

- **Phase Number:** Phase 00
- **Phase Name:** Project Governance and Development Environment
- **Objective:** Establish the canonical multi-agent governance framework, single-source instruction architecture, automated git hooks, linting/security validation scripts, continuous integration checks, and development standards before application development begins.
- **Why This Phase is Necessary:** In a multi-agent environment utilizing Claude Code, GitHub Copilot, Google Antigravity, OpenAI Codex, Cursor, and Gemini CLI, agents will drift, overwrite each other's work, invent conflicting abstractions, bypass security controls, and break shared code unless strict repository-level boundaries, deterministic validators, and automated git hooks enforce consistency.
- **Expected Outcome:** A fully verified repository setup where every agent reads canonical instructions from [`AGENTS.md`](../../../AGENTS.md), native git hooks block malformed commits and unvalidated pushes, automated scripts validate documentation and secrets, and onboarding documentation guides contributors.
- **Priority:** Critical
- **Estimated Complexity:** Medium
- **Estimated Duration:** 3 business days (Status: Baseline Implemented and Tested).

---

## B. Prerequisites

- **Required Completed Phases:** None (Root initialization phase).
- **Required Infrastructure:** Local developer workstation with Windows, macOS, or Linux; Git version 2.40+; Node.js version 22+ (LTS).
- **Required Credentials or External Services:** Git repository remote access (GitHub or GitLab).
- **Required Architectural Decisions:** [`docs/adr/0001-canonical-agent-governance.md`](../../adr/0001-canonical-agent-governance.md) (single instruction source) and [`docs/adr/0002-native-git-hooks-with-node-automation.md`](../../adr/0002-native-git-hooks-with-node-automation.md) (native hooks without external framework bloat).
- **Required Documentation:** [`governance/README.md`](../../../governance/README.md), [`governance/ENGINEERING_STANDARDS.md`](../../../governance/ENGINEERING_STANDARDS.md), [`governance/SECURITY_POLICY.md`](../../../governance/SECURITY_POLICY.md).

---

## C. Scope

### Included Features

- Canonical instructions architecture centered on [`AGENTS.md`](../../../AGENTS.md) with hard rules and change management lifecycle.
- Agent entrypoint synchronization automation (`scripts/governance/sync-agent-files.mjs`) generating wrappers for Claude Code, Copilot, Cursor, Antigravity, and Gemini.
- Repository-level native git hooks (`commit-msg`, `pre-commit`, `pre-push`) configured through `core.hooksPath = scripts/hooks`.
- Zero-external-dependency Node.js validation harness enforcing commit message formats, forbidden placeholder checks, sensitive file path protection, workflow security, and secret detection.
- Unified configuration file (`governance/governance.config.json`).
- GitHub Actions CI workflows (`.github/workflows/governance.yml`, `.github/workflows/security.yml`).
- Developer onboarding and troubleshooting documentation.

### Excluded Features

- Application runtime code (FastAPI, Next.js, LangGraph).
- Database migrations and schemas (deferred to Phase 01/02).
- External API credentials and production infrastructure provisioning (deferred to Phase 01/16).

### Deferred Features

- Automated PR attestation signing (Cosign/SLSA) hook integration into non-Claude environments.

### Dependencies on Future Phases

- All future phases (Phases 01 through 16) directly depend on Phase 00 governance policies and validation checks remaining strictly active.

---

## D. Implementation Breakdown

### Task P00-T001: Establish Canonical Agent Instruction Architecture

- **Task ID:** P00-T001
- **Task Name:** Establish Canonical Agent Instructions and Hard Rules
- **Detailed Description:** Create the canonical [`AGENTS.md`](../../../AGENTS.md) adhering to the 24,000-byte budget limit. Define precedence rules, the 12 non-negotiable hard rules, permission tiers (READ, CONTROLLED_WRITE, APPROVAL_REQUIRED, RESTRICTED), and standard command specifications.
- **Expected Files:** `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `.claude/settings.json`, `.github/copilot-instructions.md`.
- **Dependencies:** None.
- **Assigned Agent Role:** Governance Engineer / Principal Architect.
- **Required Tests:** `npm run agents:check`.
- **Documentation Updates:** `docs/governance/AI_AGENT_COMPATIBILITY.md`.
- **Acceptance Criteria:** Size of `AGENTS.md` is under 24,000 bytes; entrypoints point to `AGENTS.md`; no policy is duplicated across tool wrappers.

### Task P00-T002: Implement Governance Validation Automation

- **Task ID:** P00-T002
- **Task Name:** Build Zero-Dependency Node.js Validation Scripts
- **Detailed Description:** Implement testable ECMAScript modules in `scripts/validation/` to validate repository configuration syntax (`check-config-syntax.mjs`), commit messages (`check-commit-msg.mjs`), banned placeholder strings (`check-docs.mjs`), sensitive files (`check-sensitive.mjs`), secret patterns (`check-secrets.mjs`), and CI workflow permissions (`check-workflows.mjs`).
- **Expected Files:** `scripts/validation/*.mjs`, `scripts/lib/*.mjs`, `governance/governance.config.json`.
- **Dependencies:** P00-T001.
- **Assigned Agent Role:** DevOps Automation Engineer.
- **Required Tests:** `node --test scripts/validation/*.test.mjs`.
- **Documentation Updates:** `governance/CHANGE_MANAGEMENT.md`.
- **Acceptance Criteria:** `npm run validate:fast` runs in under 10 seconds; detects banned patterns (`TBD`, `TODO`, `FIXME`, `XXX`) and unauthorized changes to sensitive paths.

### Task P00-T003: Configure Native Git Hooks

- **Task ID:** P00-T003
- **Task Name:** Install Native Git Hook Dispatchers and Verification Suite
- **Detailed Description:** Implement shell hook scripts in `scripts/hooks/` (`commit-msg`, `pre-commit`, `pre-push`) that delegate to the Node.js validation harness. Implement automated installer in `scripts/governance/setup.mjs`. Build end-to-end hook test suite exercising git actions in temporary test repositories.
- **Expected Files:** `scripts/hooks/commit-msg`, `scripts/hooks/pre-commit`, `scripts/hooks/pre-push`, `scripts/governance/setup.mjs`, `scripts/governance/hooks.e2e.test.mjs`.
- **Dependencies:** P00-T002.
- **Assigned Agent Role:** DevOps Automation Engineer.
- **Required Tests:** `node --test scripts/governance/hooks.e2e.test.mjs`.
- **Documentation Updates:** `docs/guides/ONBOARDING.md`.
- **Acceptance Criteria:** `npm run setup` configures `core.hooksPath`; malformed commit messages are rejected with clear diagnostic instructions; secret commits are rejected before git records objects.

### Task P00-T004: Automate Agent Skill and Role Synchronization

- **Task ID:** P00-T004
- **Task Name:** Build Multi-Agent Skill and Prompt Generator
- **Detailed Description:** Create `scripts/governance/sync-agent-files.mjs` to ingest canonical markdown procedures from `governance/workflows/` (12 procedures) and `governance/roles/` (9 roles) and generate specialized wrappers for Claude Code (`.claude/skills/`, `.claude/agents/`), Google Antigravity (`.agents/skills/`), GitHub Copilot (`.github/prompts/`), and Cursor (`.cursor/rules/`).
- **Expected Files:** `scripts/governance/sync-agent-files.mjs`, `governance/workflows/*.md`, `governance/roles/*.md`.
- **Dependencies:** P00-T001.
- **Assigned Agent Role:** Tooling & Integration Engineer.
- **Required Tests:** `npm run agents:check`.
- **Documentation Updates:** `docs/governance/AI_AGENT_COMPATIBILITY.md`.
- **Acceptance Criteria:** 59 generated wrappers match sources identically; `--check` mode exits non-zero if drift is detected.

### Task P00-T005: Setup Continuous Integration Workflows

- **Task ID:** P00-T005
- **Task Name:** Implement GitHub Actions CI Checks
- **Detailed Description:** Create `.github/workflows/governance.yml` and `.github/workflows/security.yml` to execute `npm run validate:ci`, `npm test`, and `npm run security:check` on all pull requests and main pushes. Configure minimum GitHub token permissions (`contents: read`).
- **Expected Files:** `.github/workflows/governance.yml`, `.github/workflows/security.yml`, `.github/dependabot.yml`, `.github/PULL_REQUEST_TEMPLATE.md`.
- **Dependencies:** P00-T002.
- **Assigned Agent Role:** Security & CI Engineer.
- **Required Tests:** `node scripts/validation/check-workflows.mjs`.
- **Documentation Updates:** `docs/operations/BRANCH_PROTECTION.md`.
- **Acceptance Criteria:** Workflows pass security linter; unpinned third-party actions and wildcard write permissions are absent.

### Task P00-T006: Document Governance Baseline and Validation Report

- **Task ID:** P00-T006
- **Task Name:** Publish Repository Audit, Onboarding, and Setup Validation Reports
- **Detailed Description:** Document developer onboarding procedures, configuration guides, troubleshooting steps, and publish an audit verifying all baseline requirements are satisfied.
- **Expected Files:** `docs/guides/ONBOARDING.md`, `docs/guides/CONFIGURATION.md`, `docs/guides/TROUBLESHOOTING.md`, `docs/governance/SETUP_VALIDATION_REPORT.md`, `docs/governance/REPOSITORY_AUDIT.md`.
- **Dependencies:** P00-T001 through P00-T005.
- **Assigned Agent Role:** Technical Writer & Lead Architect.
- **Required Tests:** `npm run docs:check`.
- **Documentation Updates:** `README.md`, `CHANGELOG.md`.
- **Acceptance Criteria:** All documentation references exist; zero placeholder strings found across docs; all validation commands documented and verified.

---

## E. File and Folder Changes

- **Files Created:** `AGENTS.md`, `CLAUDE.md`, `GEMINI.md`, `package.json`, `package-lock.json`, `governance/governance.config.json`, `governance/*.md`, `scripts/hooks/*`, `scripts/validation/*`, `scripts/lib/*`, `scripts/governance/*`, `docs/adr/*`, `docs/architecture/*`, `docs/guides/*`, `docs/governance/*`.
- **Files Modified:** None (baseline created on top of original `MAYA_AI_FINAL_PLAN.md`).
- **Files to Avoid Modifying:** `MAYA_AI_FINAL_PLAN.md` (immutable reference specification).
- **Relevant Directories:** `governance/`, `scripts/`, `docs/`, `.claude/`, `.agents/`, `.github/`, `.cursor/`.
- **Expected Interfaces:** Node.js validation CLI (`node scripts/validation/validate.mjs --stage <stage>`).

---

## F. API and Data Contracts

### 1. Governance Configuration Contract (`governance/governance.config.json`)

```json
{
  "project": { "name": "MAYA AI", "alias": "MYAI" },
  "instructionFiles": {
    "files": ["AGENTS.md"],
    "maxBytes": 24000,
    "warnBytes": 20000
  },
  "agentWrappers": { "prefix": "gov-", "rolePrefix": "role-" },
  "markdown": { "roots": ["README.md", "AGENTS.md", "governance", "docs"] },
  "sensitive": {
    "blockedFiles": [
      ".env",
      "*.pem",
      "*.key",
      "id_rsa*",
      "credentials.json",
      "secrets/**"
    ],
    "approvalRequiredPaths": [
      "AGENTS.md",
      "governance/**",
      "scripts/hooks/**",
      "package.json"
    ]
  }
}
```

### 2. Validation Stages

- `fast`: Syntax, workflows, script syntax, agent sync, documentation placeholders, secrets, sensitive files (runtime < 10s).
- `full`: Fast stage + unit and end-to-end test execution.
- `ci`: Full stage with strict zero-warning tolerance and stack checkers when detected.

---

## G. Security Requirements

- **Authentication & Permissions:** Git commits affecting paths in `sensitive.approvalRequiredPaths` require a `Human-Approval:` git trailer.
- **Data Isolation:** Validation scripts run offline and do not communicate with external network services.
- **Secret Handling:** Banned secret patterns (AWS, GitHub, Google API tokens, private keys) are checked by `check-secrets.mjs` against diffs and full repo files.
- **Input Validation:** Commit message headers are restricted to Conventional Commits format with a maximum length of 100 characters.

---

## H. Testing Strategy

- **Unit Testing:** Unit tests in `scripts/lib/lib.test.mjs`, `scripts/validation/check-docs.test.mjs`, and `scripts/validation/check-script-syntax.test.mjs`.
- **Integration Testing:** End-to-end git hook scenario testing in `scripts/governance/hooks.e2e.test.mjs` executing actual git commits, invalid commits, and push attempts.
- **Drift Testing:** Automated agent file sync check (`npm run agents:check`) verifying all 59 generated wrappers reflect canonical procedures.

---

## I. Definition of Done

1. `npm run validate:fast` passes cleanly with 0 errors.
2. `npm test` passes all governance test suites.
3. Native git hooks are active in `.git/config` (`core.hooksPath = scripts/hooks`).
4. `AGENTS.md` is strictly under 24,000 bytes.
5. All 59 agent skill and role files match source templates without drift.
6. Documentation checks pass with zero banned placeholders.

---

## J. Phase Exit Gate

- **Demonstrated Proof:** Successful execution of `npm run validate:fast` producing PASS across all checks; `SETUP_VALIDATION_REPORT.md` signed and recorded.
- **Dependent Phase Unlocked:** Phase 01 (Core Infrastructure and Application Foundation) is cleared to begin.
