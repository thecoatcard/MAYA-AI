# Repository structure

## Current layout

```text
.
├── AGENTS.md                  Canonical instructions for every AI agent
├── CLAUDE.md, GEMINI.md       Thin entry points that import AGENTS.md
├── MAYA_AI_FINAL_PLAN.md      Authoritative product plan
├── README.md, CHANGELOG.md, .env.example
├── package.json               npm scripts for governance automation
├── backend/                   FastAPI foundation, locked Python dependencies and tests
├── apps/web/                  Next.js 15 overview, component tests and npm lockfile
├── docker-compose.yml         Local-only PostgreSQL/pgvector and Redis AOF
├── docker/postgres/           Extension and restricted runtime-role initialization
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
│   ├── implementation/        Phase plans, roadmap, status, review findings (START_HERE.md is the entry point)
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

The local foundation now exists. Product modules beyond configuration, data-service
connections, health probes and the static overview remain planned:

```text
backend/        FastAPI foundation; task engine, agents and MCP gateway are planned
apps/web/       Static Next.js overview; product UI is planned
docker/         Local PostgreSQL bootstrap; sandbox image is planned
```

Rules:

- A new module gets a README in its directory (or `docs/modules/<name>.md`); `npm run docs:check` enforces this under `backend/app/*`, `apps/*`, `packages/*` and `services/*`.
- Stack checks (`npm run lint`, `npm run typecheck`, `npm run validate`) now detect both manifests. Add the backend virtual environment's Scripts directory to the validation process PATH, or activate that environment first.
- Directories such as `docs/api/`, `docs/modules/`, `docs/security/`, `docs/testing/` and `docs/integrations/` are created with their first real document, never as empty placeholders.
