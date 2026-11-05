# Governance

This directory holds the detailed engineering, security and AI-agent policies for MAYA AI (MYAI). `AGENTS.md` at the repository root is the short canonical entry point; the documents here carry the detail.

**One repository, one governance system, multiple AI agents.**

## Precedence

1. The human user's explicit request, within the limits of the policies.
2. `AGENTS.md`
3. This directory
4. Module-level `AGENTS.md` files (only where a directory needs extra local rules; they may never weaken a higher level)
5. Existing code and accepted decisions in `docs/adr/`

If two sources conflict, follow the higher one and report the conflict in the change report so a human can fix it.

## Documents

| Document | Covers |
| :--- | :--- |
| [CORE_PRINCIPLES.md](CORE_PRINCIPLES.md) | The principles every other document derives from |
| [ENGINEERING_STANDARDS.md](ENGINEERING_STANDARDS.md) | Code quality, architecture, production readiness, definition of done |
| [SECURITY_POLICY.md](SECURITY_POLICY.md) | Secrets, scanning, shell safety, prompt-injection, incident handling |
| [AI_AGENT_POLICY.md](AI_AGENT_POLICY.md) | How agents behave, report, hand over work, and use roles and workflows |
| [DOCUMENTATION_POLICY.md](DOCUMENTATION_POLICY.md) | What must be documented with each kind of change, exceptions, validation |
| [TESTING_POLICY.md](TESTING_POLICY.md) | Test expectations by layer, and what "tested" means |
| [GIT_WORKFLOW.md](GIT_WORKFLOW.md) | Branches, Conventional Commits, trailers, hooks, pull requests |
| [CHANGE_MANAGEMENT.md](CHANGE_MANAGEMENT.md) | Change lifecycle, change reports, ADR triggers, risk classes |
| [TOOL_PERMISSIONS.md](TOOL_PERMISSIONS.md) | The four permission levels and how each tool enforces them |
| [governance.config.json](governance.config.json) | Machine-readable policy used by the validators |
| [workflows/](workflows/) | The 12 reusable procedures |
| [roles/](roles/) | The 9 optional development roles |

## How the rules are enforced

Instructions alone are not enforcement. Each layer catches what the previous one can miss, and none is claimed to be unbypassable.

| Layer | Mechanism | Can be bypassed by |
| :--- | :--- | :--- |
| 1. Instructions | `AGENTS.md` and per-tool entry points | An agent ignoring or misreading them |
| 2. Tool permissions | `.claude/settings.json` deny and ask rules; other tools' own approval prompts | Misconfiguration; matching limits (see [TOOL_PERMISSIONS.md](TOOL_PERMISSIONS.md)); tools without equivalents |
| 3. Local Git hooks | `scripts/hooks/` via `core.hooksPath` | `git commit --no-verify`, or not running `npm run setup` |
| 4. CI | `.github/workflows/` re-runs the same validators | Only by changing the workflow, which needs approval and review |
| 5. Platform controls | Branch protection, required checks, CODEOWNERS (a manual step; **not enforceable on the current private repository plan**, see `docs/operations/BRANCH_PROTECTION.md`) | Repository administrators |

Layers 4 and 5 are the real gate. Layers 1 to 3 give fast feedback.

## Changing governance

Governance files are Level 3 (APPROVAL_REQUIRED). An agent may propose a change and prepare it, but a human must approve it, the commit must carry a `Human-Approval:` trailer, and a change that alters architecture or security boundaries needs an ADR. After editing `AGENTS.md`, `governance/workflows/` or `governance/roles/`, run `npm run agents:sync` so the per-tool wrappers match.
