# Branch protection and required checks

Local Git hooks can be skipped, and agent permission rules are advisory. The real gate is CI plus branch protection on the hosting platform. **This is a manual step**: it needs administrator access to a remote repository, and none exists yet for this project. Do this when the repository is first pushed to GitHub.

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
