# Onboarding

How to set up this repository, for people and for AI coding agents.

## Prerequisites

| Tool | Version | Needed for |
| :--- | :--- | :--- |
| Git | recent | Hooks, history |
| Node.js | 22 or newer (developed on 24) | All governance automation |
| npm | bundled with Node.js | Task runner and the pinned `yaml` dev dependency |

For the implemented local foundation, also install Python 3.12, uv, Docker Desktop
with Linux containers and Node.js 22.12+ or 24+. Follow the
[local runbook](../operations/LOCAL_DEVELOPMENT.md) to start the API, web and data services.
Dependency installation and infrastructure changes require human approval when performed by agents.

## One-command setup

```bash
git clone <repository-url>
cd <repository-folder>
npm run setup
```

`npm run setup` checks the Node.js version, points Git at the repository hooks (`core.hooksPath = scripts/hooks`), runs `npm ci` if the pinned dev dependency is missing, and verifies the generated agent files. It is safe to re-run. If you already use a different `core.hooksPath`, it stops and tells you; pass `--force` to replace it.

## Everyday commands

| Command | What it does |
| :--- | :--- |
| `npm run validate` | Everything the pre-push hook runs |
| `npm run validate:fast` | Whole-repository checks without tests or range rules |
| `npm test` | Unit tests for the governance scripts |
| `npm run lint` | Script and config syntax, workflow security lint, stack linters when projects exist |
| `npm run typecheck` | Stack type checkers when projects exist |
| `npm run docs:check` | Documentation validation |
| `npm run security:check` | Secret scan, blocked-file check, workflow lint |
| `npm run agents:sync` | Regenerate per-tool agent files after changing `AGENTS.md`, a workflow or a role |
| `npm run agents:check` | Fail if generated files drifted from their sources |

Results are `PASS`, `FAIL` or `SKIPPED`. **`SKIPPED` means the check did not run**, for example stack checks before any application project exists. It is not a pass.

## Making a change

1. Read `AGENTS.md` and the policy that applies (`governance/README.md`).
2. Follow the lifecycle in `governance/CHANGE_MANAGEMENT.md`.
3. Commit with a Conventional Commit message (`governance/GIT_WORKFLOW.md`). If you touch a guardrail file, add a `Human-Approval:` trailer.
4. Open a pull request with the template. CI runs the same checks as the hooks.

## Working with AI agents

Every supported agent starts from the same canonical file, `AGENTS.md`. What each tool reads, and what is and is not verified, is in `docs/governance/AI_AGENT_COMPATIBILITY.md`. Quick start:

| Tool | Do this |
| :--- | :--- |
| Claude Code | Open the repository root. `CLAUDE.md` imports `AGENTS.md`. Run `/gov-plan-feature` and similar skills. |
| GitHub Copilot | Instructions load from `.github/copilot-instructions.md` and `AGENTS.md`. In VS Code use `/gov-...` prompt files. |
| Antigravity | `AGENTS.md` and `GEMINI.md` load automatically. Use `/gov-...` skills. |
| Cursor | `AGENTS.md` and `.cursor/rules/` load automatically. |
| Gemini CLI | `GEMINI.md` imports `AGENTS.md`. |
| Codex | `AGENTS.md` loads automatically. Use `$gov-...` skills. |
| Anything else | Point the tool at `AGENTS.md`, or paste its "Hard rules" section into the tool's custom instructions. |

Configure each tool's own approval and sandbox mode conservatively on your machine. This repository cannot force those settings.

## Adding a workflow or a role

1. Create `governance/workflows/<name>.md` or `governance/roles/<name>.md` with `name` (equal to the file name) and `description` front matter.
2. Run `npm run agents:sync`.
3. Commit both the source and the generated files, with a `Human-Approval:` trailer because `governance/` is protected.
