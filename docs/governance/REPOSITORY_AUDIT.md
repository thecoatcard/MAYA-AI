# Repository Audit

| | |
| :--- | :--- |
| **Date** | 2026-11-05 |
| **Scope** | Stage A of the AI-agent governance setup: inspect before changing anything |
| **Project** | MAYA AI (also written "MYAI" in the setup request; this repository uses "MAYA AI" because that is the name in the project plan) |
| **Auditor** | Claude Code (agent). Findings were produced by reading the working directory and running read-only commands. |

## 1. Current repository structure

At audit time the working directory contained exactly two entries:

```text
.
├── .codemie/                 (empty directory)
└── MAYA_AI_FINAL_PLAN.md     (65 KB project plan; the only substantive document)
```

Key facts:

- **It was not a Git repository.** There was no `.git` directory in the folder or any parent. There was no history, no remote and no hooks. The setup ran `git init -b main` as a first, reversible step because commit conventions, hooks and CI all depend on Git.
- **There is no application code.** There is no backend, frontend, test suite, container configuration, or dependency manifest. This is a greenfield repository with a plan, not a codebase with conventions to preserve.
- **`.codemie/` is empty.** It appears to be local tooling state. It has been left untouched and is ignored by `.gitignore` until someone confirms it holds nothing that should be committed (see §6).

## 2. Existing tech stack and tooling

**Of the project (planned, not implemented).** `MAYA_AI_FINAL_PLAN.md` specifies a modular monolith: Python 3.12 with FastAPI, LangGraph and Celery (or ARQ); Next.js 15 with TypeScript and Tailwind; PostgreSQL 17 with pgvector; Redis; Docker; Playwright. None of it exists yet.

**Of the developer machine (observed 2026-11-05).**

| Tool | Status |
| :--- | :--- |
| Git | 2.56.0 (Windows) |
| Node.js / npm | 24.21.0 / 11.19.0 |
| Docker | present |
| Python | **not usable**: only the Microsoft Store launcher shim resolves; no interpreter |
| `make`, `gh`, `gitleaks`, `semgrep`, `trivy`, `pre-commit`, PowerShell 7 | **not installed** |

**Consequences for the design.**

1. Repository automation is written as **zero-dependency Node.js scripts**, run through `npm run …`. Node is already required by the planned Next.js app, runs identically on Windows, macOS and Linux, and needs no Python.
2. **Git hooks use Git's native `core.hooksPath`**, not the Python `pre-commit` framework, which cannot run on this machine. The same checks run again in CI.
3. **No `Makefile`.** `make` is not available on the primary machine, so a Makefile could not be tested. `npm run` is the task runner.
4. Python and web lint/type/test tooling cannot be exercised yet because no such code exists. The stack-check runner detects projects and reports `SKIPPED` rather than pretending to pass.

## 3. Existing documentation, CI and conventions

| Area | Finding |
| :--- | :--- |
| Documentation | One plan document. No README, no ADRs, no architecture docs. |
| CI/CD | None (no `.github/`, no other CI configuration). |
| Git hooks | None. |
| Linters / formatters / test commands | None. |
| Coding conventions | Stated only inside the plan (§8, §11, §12, §21). Not enforceable as written. |
| `.gitignore`, `.gitattributes`, `.editorconfig` | None. Without `.gitattributes`, Windows line-ending conversion would corrupt shell hook scripts. |
| `.env.example` | None. |

## 4. Existing AI-agent instruction files and configuration

| Agent | Repository-level configuration found |
| :--- | :--- |
| Claude Code | None (`CLAUDE.md`, `.claude/` absent) |
| GitHub Copilot | None |
| Google Antigravity | None |
| Cursor | None |
| Gemini CLI | None |
| OpenAI Codex | None |
| Windsurf | None |
| CodeMie | Empty `.codemie/` directory only |

No duplicated, conflicting or outdated agent rules exist, because none exist. The risk is future divergence: seven tools reading seven different files.

## 5. Missing governance capabilities

1. A canonical instruction source and a defined precedence order.
2. Any enforced permission model for agents (what is autonomous, what needs a human, what is forbidden).
3. Security guardrails: secret scanning, sensitive-file blocking, protected paths, prompt-injection rules.
4. Documentation policy and automated validation (links, placeholders, env vars, module docs).
5. Change management: commit convention, change reports, ADRs, changelog.
6. Repository-level hooks and CI so checks run no matter which agent (or human) made the change.
7. Reusable workflows and role definitions shared by all agents.
8. Onboarding: README, setup command, validation command, troubleshooting.

## 6. Security concerns

| # | Concern | Severity | Handling |
| :-- | :--- | :--- | :--- |
| S1 | No Git history, `.gitignore` or secret protection: a `.env` or key file could be committed on the first `git add .` | High | `.gitignore`, sensitive-file blocking, secret scan in hook and CI |
| S2 | `.codemie/` contents are unknown | Low | Ignored until reviewed; confirm manually |
| S3 | Automated scan of `MAYA_AI_FINAL_PLAN.md` for credential patterns (AWS, GitHub, Google, Slack, OpenAI-style keys, private-key headers, JWTs) | Info | **No matches.** The plan contains no secrets. |
| S4 | No branch protection or required reviews (no remote exists yet) | High once a remote is added | Documented as a manual step in `docs/operations/BRANCH_PROTECTION.md` |
| S5 | Local Git hooks can be skipped (`--no-verify`) and agent permission rules are advisory | Medium | CI re-runs every critical check; branch protection is the real gate |
| S6 | Agent-instruction files are themselves a prompt-injection target | Medium | Policy that repository and external content is untrusted data; sensitive instruction files require human approval |
| S7 | The project plan describes an agent that can reach the Docker socket in one place of the original specs | Medium (design) | Already resolved in the plan (decision D14); noted so agents do not reintroduce it |

## 7. Verification of AI-agent conventions

Each tool's entry points were checked against its official documentation on 2026-11-05 rather than assumed. Results are in `docs/governance/AI_AGENT_COMPATIBILITY.md`. Two findings changed the layout requested in the setup brief:

- **Antigravity** reads `.agents/` (plural). The `.agent/` folder is accepted only as a legacy path for rules, and Antigravity's own migration page says legacy workflows stop working on **2026-10-19**, which has passed. So workflows are delivered as skills in `.agents/skills/`, and no `.agent/workflows/` is created.
- **Codex** does not read a `.codex/` folder for instructions. `AGENTS.md` is its entry point, so no `.codex/README.md` is created.

## 8. Recommended setup (what was built)

1. `AGENTS.md` as the single canonical instruction file, kept under the strictest documented size limit (24,000 bytes for Antigravity; Codex caps combined instruction files at 32 KiB).
2. `governance/` for detailed policy, plus canonical `governance/workflows/` (12) and `governance/roles/` (9).
3. A generator, `scripts/governance/sync-agent-files.mjs`, that emits thin per-tool wrappers from those canonical sources, and a `--check` mode that fails CI on drift.
4. Native Git hooks (`scripts/hooks/`) and an equivalent GitHub Actions pipeline, both calling the same validators.
5. Documentation templates and automated documentation, secret, sensitive-path and change-rule validation.
6. Onboarding docs and `npm run` commands that were each executed during validation.

## 9. Files created or modified

All files are new; nothing pre-existing was modified or deleted (`MAYA_AI_FINAL_PLAN.md` is unchanged).

| Group | Paths |
| :--- | :--- |
| Canonical instructions | `AGENTS.md` |
| Agent entry points | `CLAUDE.md`, `GEMINI.md`, `.github/copilot-instructions.md`, `.github/instructions/`, `.cursor/rules/`, `.claude/settings.json` |
| Generated wrappers | `.claude/skills/`, `.claude/agents/`, `.agents/skills/`, `.github/prompts/`, `.cursor/rules/gov-*.mdc` |
| Governance | `governance/*.md`, `governance/workflows/`, `governance/roles/`, `governance/governance.config.json` |
| Automation | `scripts/lib/`, `scripts/validation/`, `scripts/governance/`, `scripts/hooks/`, `package.json`, lockfile |
| CI | `.github/workflows/`, `.github/dependabot.yml`, `.github/PULL_REQUEST_TEMPLATE.md` |
| Documentation | `README.md`, `CHANGELOG.md`, `.env.example`, `docs/architecture/`, `docs/adr/`, `docs/guides/`, `docs/operations/`, `docs/governance/`, `templates/` |
| Repository hygiene | `.gitignore`, `.gitattributes`, `.editorconfig` |

Directories the brief listed for later features (`docs/api/`, `docs/modules/`, `docs/security/`, `docs/testing/`, `.codex/`, `.agent/`) are deliberately not created: they would be empty or unsupported. The change-rule engine tells contributors which of them to create when the first matching code appears.
