# MAYA AI (MYAI)

A planned voice-first, multi-agent "digital employee": it takes spoken or typed instructions, plans the work, uses external tools through the Model Context Protocol, verifies results, asks a human before consequential actions, and reports back.

> **Status: Phase 01 local foundation in progress.** FastAPI health probes, configuration/logging, PostgreSQL/Redis infrastructure and a Next.js web shell are implemented. Accounts, chat, models, agents and voice remain planned. See the [product plan](MAYA_AI_FINAL_PLAN.md) and [implementation status](docs/implementation/IMPLEMENTATION_STATUS.md).

## Quick start

Requires Git and Node.js 22 or newer.

For the application foundation (Python 3.12, uv, Docker and Node.js 22.12+ or 24+),
follow the [local development runbook](docs/operations/LOCAL_DEVELOPMENT.md).

```bash
npm run setup      # install Git hooks and verify the toolchain
npm run validate   # run every check
```

| Command | Purpose |
| :--- | :--- |
| `npm run validate` | Full validation (what the pre-push hook runs) |
| `npm run validate:fast` | Quick whole-repository checks |
| `npm test` | Unit tests for the governance scripts |
| `npm run lint` / `npm run typecheck` | Script and config checks; detected backend and web stack checks |
| `npm run docs:check` | Documentation validation |
| `npm run security:check` | Secret and blocked-file scan, workflow lint |
| `npm run agents:sync` / `agents:check` | Regenerate / verify per-tool agent files |

A check result of `SKIPPED` means it did not run; it is not a pass. Details: [docs/guides/ONBOARDING.md](docs/guides/ONBOARDING.md).

## How the repository is governed

- **One source of truth:** [AGENTS.md](AGENTS.md) holds the rules every AI agent follows; [governance/](governance/README.md) holds the detailed policies, 12 reusable workflows and 9 development roles.
- **Per-tool entry points** are thin and mostly generated: see [docs/governance/AI_AGENT_COMPATIBILITY.md](docs/governance/AI_AGENT_COMPATIBILITY.md) for what each tool reads, verified against vendor documentation.
- **Enforcement** is in the repository, not in any one agent: Git hooks ([scripts/hooks](scripts/hooks)) and GitHub Actions run the same validators. Hooks can be bypassed locally, so CI and branch protection ([docs/operations/BRANCH_PROTECTION.md](docs/operations/BRANCH_PROTECTION.md)) are the real gate; the server's pull-request rule on `main` has been observed to be enforced, the other rules are unverified.
- **Documentation ships with code**, decisions are recorded as [ADRs](docs/adr/README.md), and non-trivial changes end with a change report ([template](templates/change-report/TEMPLATE.md)).

## Where to look

| I want to… | Go to |
| :--- | :--- |
| Continue the implementation (new chat or new agent) | [docs/implementation/START_HERE.md](docs/implementation/START_HERE.md) |
| Understand the planned system | [docs/architecture/SYSTEM_OVERVIEW.md](docs/architecture/SYSTEM_OVERVIEW.md) |
| See what is in this repository | [docs/architecture/REPOSITORY_STRUCTURE.md](docs/architecture/REPOSITORY_STRUCTURE.md) |
| Set up and contribute | [docs/guides/ONBOARDING.md](docs/guides/ONBOARDING.md) |
| Fix a failing check or hook | [docs/guides/TROUBLESHOOTING.md](docs/guides/TROUBLESHOOTING.md) |
| Know what changed | [CHANGELOG.md](CHANGELOG.md) |
