# Repository structure

## Current layout

```text
.
├── AGENTS.md                  Canonical instructions for every AI agent
├── CLAUDE.md, GEMINI.md       Thin entry points that import AGENTS.md
├── MAYA_AI_FINAL_PLAN.md      Product plan (the only product document)
├── README.md, CHANGELOG.md, .env.example
├── package.json               npm scripts for the governance automation (no application code)
├── governance/                Policies, config, workflows/ (12), roles/ (9)
├── scripts/
│   ├── hooks/                 Git hooks (pre-commit, commit-msg, pre-push)
│   ├── validation/            Checks run by hooks and CI
│   ├── governance/            Setup and agent-file generator
│   └── lib/                   Shared helpers
├── templates/                 ADR, module, API, integration, agent, change report, runbook
├── docs/
│   ├── architecture/          This folder
│   ├── adr/                   Architecture decision records
│   ├── guides/                Onboarding, configuration reference, troubleshooting
│   ├── operations/            Branch protection and (later) runbooks
│   └── governance/            Audit, agent compatibility matrix, validation report
├── .github/                   CI workflows, Dependabot, PR template, Copilot files
├── .claude/                   Claude Code settings plus generated skills and subagents
├── .agents/skills/            Generated skills (Antigravity and Codex)
└── .cursor/rules/             Generated Cursor rules
```

## Which files are generated

Never edit these by hand; change the source and run `npm run agents:sync`:

| Generated | Source |
| :--- | :--- |
| `.claude/skills/gov-*`, `.agents/skills/gov-*`, `.github/prompts/gov-*`, `.cursor/rules/gov-*` | `governance/workflows/` |
| `.claude/agents/role-*` | `governance/roles/` |
| `.github/copilot-instructions.md`, `.cursor/rules/maya-governance.mdc` | the hard-rules block in `AGENTS.md` |

## Where new things go

The product plan proposes this application layout. It is not created yet:

```text
backend/        FastAPI application, task engine, agents, MCP gateway
apps/web/       Next.js client
docker/         Compose files and the sandbox image
```

Rules:

- A new module gets a README in its directory (or `docs/modules/<name>.md`); `npm run docs:check` enforces this under `backend/app/*`, `apps/*`, `packages/*` and `services/*`.
- Stack checks (`npm run lint`, `npm run typecheck`, `npm run validate`) detect `backend/pyproject.toml` and `apps/web/package.json` automatically and report `SKIPPED` until they exist.
- Directories such as `docs/api/`, `docs/modules/`, `docs/security/`, `docs/testing/` and `docs/integrations/` are created with their first real document, never as empty placeholders.
