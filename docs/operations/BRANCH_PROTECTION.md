# Branch protection and required checks

Local Git hooks can be skipped, and agent permission rules are advisory. The real gate is CI plus branch protection on the hosting platform. **Configuring it is a manual step** that needs administrator access to the remote repository.

## Current status (recorded 2026-11-05): not enforceable on this repository

The repository exists on GitHub (`thecoatcard/MAYA-AI`, private). GitHub told the owner on 2026-11-05 that rulesets are **not enforced on this private repository until it moves to a GitHub Team organization account**. The owner's decision (option A) is to stay private on the current plan for now. Until that changes:

- The table below is the **target configuration**, not the current state. Nothing on the server stops a direct push to `main`, a force-push, a merge with failing checks, or a merge without review.
- What stands in for it: the repository Git hooks (skippable with `--no-verify`), the CI workflows (they run and report, but cannot be made required), the agent `ask` rules in `.claude/settings.json` (advisory), and the owner's own discipline: work on a branch, let CI run, and merge only when it passes.
- Agents never push or merge to `main` on their own initiative. Merging to `main` needs the owner's explicit request in the current conversation, and a commit that touches a guardrail path still needs a `Human-Approval:` trailer.
- CODEOWNERS is not committed and would not be enforced on this plan either.
- The `dependency-review` job needs GitHub Advanced Security on private repositories (see Prerequisites), so it may fail or be unavailable here. Its first result has not been observed; record it in the verification record once seen.
- **Trigger to revisit:** a second contributor, a hosted deployment, or any real user data. Then move to a plan that enforces rulesets (or make the repository public after reviewing what that exposes), apply the table below, and record the date and verifier under "Verify it works".

## Prerequisites

- The repository is on GitHub with the workflows from `.github/workflows/` enabled.
- Gitleaks: user-owned repositories need nothing; organization-owned repositories need a free `GITLEAKS_LICENSE` secret (see `.github/workflows/security.yml`).
- The dependency review job needs the dependency graph enabled; private repositories also need GitHub Advanced Security.

## Required settings for `main`

Configure under Settings, then Rules (or Branches).

| Setting | Value |
| :--- | :--- |
| Require a pull request before merging | On, with at least 1 approving review |
| Dismiss stale approvals when new commits are pushed | On |
| Require review from Code Owners | On (see below) |
| Require status checks to pass | On; require **Governance / validate**, **Security / secrets**, **Security / dependency-review** |
| Require branches to be up to date before merging | On |
| Require conversation resolution | On |
| Block force pushes and deletion | On |
| Do not allow bypassing the above settings | On, including administrators where available |
| Merge method | Squash only, with the pull request title as a Conventional Commit |

## CODEOWNERS

Guardrail files should require review by a named owner. A CODEOWNERS file was **not** committed because the owner's GitHub handle is not known to this repository. Create one in the `.github` folder with the real handle or team:

```text
# CODEOWNERS (replace OWNER with a real GitHub user or team)
/AGENTS.md                     @OWNER
/CLAUDE.md                     @OWNER
/GEMINI.md                     @OWNER
/governance/                   @OWNER
/scripts/                      @OWNER
/.github/                      @OWNER
/.claude/settings.json         @OWNER
/package.json                  @OWNER
/package-lock.json             @OWNER
```

The paths mirror `sensitive.approvalRequiredPaths` in `governance/governance.config.json`. Keep the two lists in step.

## Verify it works

After configuring, open a test pull request that edits a file under `governance/` and confirm that the merge is blocked until the owner approves, and that a failing `validate` job blocks the merge. Record the date and who verified it in `docs/governance/SETUP_VALIDATION_REPORT.md`.

## Why this matters

The `Human-Approval:` commit trailer is an audit trail anyone can type. Code Owner review plus required checks is what actually stops an unreviewed change to a guardrail file from reaching `main`.
