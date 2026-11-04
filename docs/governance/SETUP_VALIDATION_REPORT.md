# Setup validation report

| | |
| :--- | :--- |
| **Date** | 2026-11-05 |
| **Performed by** | Claude Code (agent), at the repository owner's request |
| **Environment** | Windows 11, Git 2.56.0, Node.js 24.21.0, npm 11.19.0. No Python, `make`, `gh`, Gitleaks, Semgrep, Trivy or `pre-commit` installed. |
| **Scope** | Repository infrastructure only. No product features were implemented. |
| **Baseline** | See `docs/governance/REPOSITORY_AUDIT.md`: the folder was not a Git repository and contained only the project plan and an empty `.codemie` folder. |

Status vocabulary: Implemented, Tested, Partially tested, Not tested, Blocked, Requires human approval.

## 1. Definition of done

| Requirement | Status | Evidence |
| :--- | :--- | :--- |
| One canonical source of truth for agent instructions | **Implemented, Tested** | `AGENTS.md` (6,967 bytes; limit 24,000). `npm run agents:check` verifies every entry point references it and that 59 generated files match their sources. |
| Verified entry points for all supported agents | **Implemented** for Claude Code, Copilot, Antigravity, Cursor, Gemini CLI, Codex. **Partially verified** for Windsurf. | `docs/governance/AI_AGENT_COMPATIBILITY.md` with sources. Windsurf documentation moved and was only partly confirmed, so no Windsurf-specific files were created. |
| Shared engineering rules | **Implemented** | `AGENTS.md` hard rules; `governance/ENGINEERING_STANDARDS.md` |
| Security policy and permission model | **Implemented** | `governance/SECURITY_POLICY.md`, `governance/TOOL_PERMISSIONS.md`, `.claude/settings.json` |
| Shared workflows (12) and roles (9) | **Implemented, Tested** | Canonical files in `governance/workflows/` and `governance/roles/`; wrappers generated and drift-checked |
| Repository-level hooks | **Implemented, Tested** | Installed in this repository by `npm run setup` (`core.hooksPath = scripts/hooks`). 7 end-to-end scenarios pass against real `git commit` and `git push` (section 5). |
| CI enforces critical checks | **Implemented; Not tested on GitHub** | Two workflows written, YAML-valid, and pass the security lint. They have never run on GitHub because no remote exists. The commands they call were run locally. |
| Documentation requirements established | **Implemented** | `governance/DOCUMENTATION_POLICY.md`, templates, change rules |
| Documentation validation automated | **Implemented, Tested** | `npm run docs:check`; 5 unit tests plus a hook scenario |
| ADR and change-report templates | **Implemented** | `templates/adr/TEMPLATE.md`, `templates/change-report/TEMPLATE.md`, plus five more templates; `docs/adr/README.md` and two ADRs |
| Developer onboarding documented | **Implemented** | `README.md`, `docs/guides/ONBOARDING.md`, `docs/guides/TROUBLESHOOTING.md`, `docs/guides/CONFIGURATION.md` |
| Validation commands actually work | **Tested** | Every command in section 4 was executed. |
| Existing functionality preserved | **Verified** | The only pre-existing file, `MAYA_AI_FINAL_PLAN.md`, is unchanged (65,333 bytes, same modification time). There was no application code to break. |
| Complete setup tested | **Partially tested** | Locally yes; GitHub-side behavior and several tools are not tested (section 9). |
| Final report | **Implemented** | This document. |

## 2. Files

**Created: 159 files** (excluding ignored paths such as `node_modules/`). **Modified: none.** **Deleted: none.**

| Area | Files |
| :--- | :--- |
| Canonical instructions | `AGENTS.md` |
| Hand-written entry points | `CLAUDE.md`, `GEMINI.md`, `.claude/settings.json`, 3 files in `.github/instructions/` |
| Generated wrappers (59) | 12 `.claude/skills/gov-*`, 12 `.agents/skills/gov-*`, 12 `.github/prompts/gov-*`, 12 `.cursor/rules/gov-*`, 9 `.claude/agents/role-*`, `.github/copilot-instructions.md`, `.cursor/rules/maya-governance.mdc` |
| Governance | 10 policy documents, `governance.config.json`, 12 workflows, 9 roles |
| Automation | 28 files under `scripts/` (libraries, validators, hooks, generator, setup, tests), `package.json`, `package-lock.json` |
| CI | 2 workflows, `dependabot.yml`, pull request template |
| Documentation | 13 files in `docs/` (architecture, ADRs, guides, operations, governance), `README.md`, `CHANGELOG.md`, `.env.example`, 7 templates |
| Hygiene | `.gitignore`, `.gitattributes`, `.editorconfig` |

No Git history exists, so there is no diff to review; the working tree was reviewed through the validators and `git status`. Nothing was staged or committed.

## 3. Tools installed and configuration changed

| Item | Detail |
| :--- | :--- |
| `git init -b main` | Created the repository. Reversible by deleting the `.git` folder. |
| Local Git config | `core.autocrlf=false`, `core.safecrlf=false`, `core.hooksPath=scripts/hooks` (set by `npm run setup`) |
| Dev dependency | `yaml` 2.9.1, exact version, with `package-lock.json`. `npm install` reported 0 vulnerabilities. It is used only for YAML syntax checks. |
| Global installs | None |
| Docker, network services | Not used |

## 4. Commands executed in this repository

| Command | Result |
| :--- | :--- |
| `npm run setup` | Passed. Hooks installed, agent files verified. |
| `npm run agents:sync` | 59 files written. |
| `npm run agents:check` | Passed. |
| `npm run docs:check` | Passed, 99 Markdown files. The first run found 12 problems, all fixed (section 6). |
| `npm run security:check` | 3 passed (repository secret scan, blocked files, workflow lint). |
| `npm run lint` | 3 passed, 1 skipped (stack lint: no application project exists). |
| `npm run typecheck` | 1 skipped (no application project). Exit code 0. |
| `npm test` | 42 passed, 0 failed (about 98 seconds, including the hook end-to-end suite). |
| `npm run validate:fast` | 7 passed, 0 failed, 1 skipped. |
| `npm run validate` | 8 passed, 0 failed, 6 skipped: stack lint, type check, tests and build (no project), and four change-range checks (no base branch because there are no commits). |
| `node scripts/validation/validate.mjs --stage ci` with no base | Failed as designed: CI must supply a comparison base. |

**Skipped means the check did not run.** On this machine the stack checks and the change-range checks have not yet run for real.

## 5. Hook status

All scenarios drive real Git commands in a throwaway repository (`scripts/governance/hooks.e2e.test.mjs`); the repository itself is never touched.

| Scenario | Expected | Result |
| :--- | :--- | :--- |
| Commit message that is not a Conventional Commit | Rejected | Passed |
| Guardrail files without a `Human-Approval:` trailer | Rejected; accepted with the trailer | Passed |
| Force-added `.env` file | Rejected | Passed |
| Token in source | Rejected, value never printed | Passed |
| Broken documentation link | Rejected | Passed |
| Push of a feature branch missing API docs and changelog | Rejected by `pre-push` | Passed |
| Same branch with docs and changelog; and a specific versus a vague waiver trailer | Accepted; vague waiver rejected | Passed |

**Not tested:** bypassing hooks with `--no-verify`. It is documented Git behavior, and the command was refused for this agent when attempted as part of a larger script (see section 8). That is why CI and branch protection are the real gate.

## 6. Problems found and fixed during validation

| Finding | Resolution |
| :--- | :--- |
| `docs:check` found 12 problems: references to files that do not exist, a branch-name example that looks like a path, and the missing report | Fixed the references and wording; wrote this report |
| The secret scanner flagged realistic fake values in my own test file | Fixtures are now assembled at runtime; the scanner was not weakened |
| Dependabot commits touch protected paths but cannot add a trailer, so CI would reject every bot pull request | Bot-authored commits are exempt from the trailer check only; header rules still apply; tested |
| `npm run typecheck` would show a failure (exit 3) when nothing exists to check | Routed through the orchestrator: reports `SKIPPED`, exit 0 |
| Hook end-to-end fixtures were incomplete (a file the generated Copilot instructions reference; a module without a README) | Fixture fixed. The failures confirmed the documentation and module rules work. |
| End-to-end tests left temporary directories | Tests now remove them; the 165 earlier leftovers were deleted |
| I reported CRLF line endings in the hook scripts | **That was a measurement error** (a mangled `grep` pattern). A byte-level check found no CR bytes in any hook. A permanent check that hook files use LF was added anyway, with tests. |
| The brief suggested `.agent/` rules and workflows and a `.codex/` folder | Not created. Antigravity uses `.agents/`, its legacy workflows stopped working on 2026-10-19, and Codex reads `AGENTS.md` only. See the compatibility document. |

## 7. Test results

42 tests, all passing: shared libraries (10), validators (20: secrets, blocked files, commit messages, change rules, workflow lint, stack detection, documentation, script syntax), agent-file generator (5), and Git-hook end-to-end scenarios (7).

**Mutation check.** Three defects were injected into a copy of the validators (blocked-file check always false, secret scanner matching nothing, change rules never requiring anything). The tests failed for each one, and the unmutated control passed all 12 tests. This shows the tests can fail.

## 8. Observations about agent permission rules

During setup a single Bash command that included `git commit`, `git push`, `git config` and `--no-verify` was refused by the harness after `.claude/settings.json` existed. The harness did not report which rule matched, so this shows the settings were active but not which rule fired. Claude Code's own documentation says Bash rules match command text and are not a security boundary; this repository treats them as a convenience layer.

## 9. Security checks and what did not run

| Check | Status |
| :--- | :--- |
| Local heuristic secret scan (repository) | Passed; 0 findings |
| Blocked-file check | Passed |
| Workflow security lint (pinned SHAs, permissions, triggers, injection) | Passed on both workflows |
| `npm` vulnerability report | 0 vulnerabilities at install |
| **Gitleaks** | **Not run** (not installed; runs in CI) |
| **Dependency review, CodeQL or Semgrep** | **Not run** (GitHub-only, or no application code yet) |
| **GitHub Actions execution** | **Not run** (no remote) |

Pinned actions (commit SHAs resolved on 2026-11-05, all releases older than two weeks): `actions/checkout` v7.0.1, `actions/setup-node` v7.0.0, `actions/dependency-review-action` v5.0.0, `gitleaks/gitleaks-action` v3.0.0.

## 10. Known limitations

- Hooks and agent permission rules can be bypassed; only CI plus branch protection is a real gate, and branch protection is not configured (no remote).
- The `Human-Approval:` trailer is an audit trail anyone can type, not a cryptographic control.
- The local secret scanner is heuristic and will miss some secrets; Gitleaks in CI is the authoritative scan.
- Stack checks (Ruff, mypy, pytest, ESLint, build) and the change rules for `backend/` paths are written against the planned layout and have only been exercised with test fixtures, not with real application code.
- Documentation checks prove that files exist and links resolve, not that the content is accurate.
- Agent tool conventions change often; the compatibility matrix is a snapshot from 2026-11-05.
- Windsurf is only partly verified; hooks for Claude Code, Cursor and Gemini CLI are supported by those tools but not configured here.

## 11. Manual steps remaining

1. **Review and accept** the two ADRs (`docs/adr/README.md`); they are marked Proposed.
2. **Make the first commit.** It touches guardrail files, so it needs a trailer, for example: `git commit -m "feat(governance): add AI agent governance system" -m "Human-Approval: approved by <your name>"`. The hooks will run.
3. **Create the remote repository, push, and configure branch protection and Code Owners** with a real owner handle (`docs/operations/BRANCH_PROTECTION.md`). Add a `GITLEAKS_LICENSE` secret if the repository belongs to an organization, and enable the dependency graph.
4. **Watch the first CI run** and record the result here.
5. **Review the `.codemie` folder.** It is empty and ignored; remove it from `.gitignore` if it should be shared.
6. **Set each tool's own approval and sandbox mode** conservatively on every developer machine; this repository cannot force it.
7. **When the first backend or web project is added**, update `governance/governance.config.json` (`stacks`), the onboarding prerequisites, the CI toolchain steps, and add static analysis (CodeQL or Semgrep) with an ADR.
8. **Optionally re-verify Windsurf and Codex hooks** and decide whether tool-specific hooks are worth adding.
