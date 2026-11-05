# Branch protection and required checks

Local Git hooks can be skipped, and agent permission rules are advisory. The real gate is CI plus branch protection on the hosting platform. **Configuring it is a manual step** that needs administrator access to the remote repository.

## Current status (recorded 2026-11-05)

The repository exists on GitHub (`thecoatcard/MAYA-AI`, private). GitHub's notice to the owner said rulesets "won't be enforced on this private repository until you move to GitHub Team organization account". **Observed behavior differs for at least some rules.** Pushing `main` directly was rejected by the server twice (error GH013, "Repository rule violations found for refs/heads/main"): first with "At least 1 approving review is required by reviewers with write access", then, after the owner changed the ruleset, with "Changes must be made through a pull request."

- **Verified:** direct pushes to `main` are blocked, so every change reaches `main` through a pull request. `gh` is not installed on the owner's machine, so the pull request is opened in the browser (for the Phase 02 branch: `https://github.com/thecoatcard/MAYA-AI/pull/new/feat/phase-02-auth-database-security`).
- **Not verified:** whether the other rules in the table below are active (required status checks, up-to-date branches, force-push and deletion blocks, Code Owners). An agent cannot read the ruleset. The owner confirms them under Settings, Rules, and records the result under "Verify it works". The table stays the **target configuration**. GitHub's own notice says enforcement on private repositories depends on the plan, so re-check before relying on any rule.
- A single owner cannot approve their own pull request, so a required approving review blocks a one-person repository. Whether to use a second reviewer, zero required approvals with the pull-request rule kept, or a bypass entry is the owner's decision. Agents never change, disable or bypass the ruleset.
- Still true everywhere: local hooks can be skipped, agent `ask` rules are advisory, and CODEOWNERS is not committed.
- The `dependency-review` job needs GitHub Advanced Security on private repositories (see Prerequisites), so it may fail or be unavailable here. Its first result has not been observed; record it in the verification record once seen.
- Merging to `main` needs the owner's explicit request in the current conversation, and a commit that touches a guardrail path still needs a `Human-Approval:` trailer.

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
