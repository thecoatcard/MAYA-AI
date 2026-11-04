# Tool Permissions

Permission levels for AI agents working on this repository. These are repository policy. They are **not a substitute** for operating-system permissions, sandboxing, CI enforcement or platform controls, and each tool enforces them differently (section 4).

## 1. Levels

### Level 1: READ (autonomous)

- Read source files and documentation
- Search the repository
- Analyse code and inspect tests
- Produce plans and reviews

### Level 2: CONTROLLED_WRITE (autonomous)

- Create and modify source files
- Create tests
- Update documentation
- Modify non-sensitive configuration
- Run local development commands (validation, tests, linters, formatters)

### Level 3: APPROVAL_REQUIRED (explicit human approval first)

- Installing or upgrading dependencies
- Changing database schemas or migrations
- Modifying CI/CD workflows
- Changing authentication or authorization
- Changing security policies
- Deleting significant data or files
- Changing infrastructure configuration
- Running commands that affect external services
- Pushing commits or creating releases
- Accessing production environments
- **Changing the guardrails**: `AGENTS.md`, `governance/`, hooks, validators, `.claude/settings.json`, generated-file sources (the exact list is `sensitive.approvalRequiredPaths` in `governance/governance.config.json`)

### Level 4: RESTRICTED (never autonomous; do not attempt)

- Reading or exposing production secrets
- Exfiltrating credentials
- Disabling security controls
- Modifying production data
- Bypassing required approvals
- Publishing sensitive information
- Executing destructive production operations
- Modifying access controls to grant yourself additional privileges

## 2. Examples

| Action | Level |
| :--- | :--- |
| `git status`, `git diff`, `git log`, `git show` | 1 |
| Edit a source file, add a test, edit a doc | 2 |
| `npm run validate`, `npm test`, linters, type checkers | 2 |
| `git add`, `git commit` (when the user asks for a commit) | 2 |
| `npm install <pkg>`, `pip install`, upgrading a lockfile | 3 |
| Editing `.github/workflows/*`, hooks, validators, `governance/*`, `AGENTS.md` | 3 |
| Adding or applying a database migration | 3 |
| `git push`, creating a tag or release, opening or commenting on a PR | 3 |
| `curl`/`wget`, calling an API, using an MCP server that reaches outside the machine | 3 |
| `git reset --hard`, `git clean`, `rm -rf`, deleting a directory | 3 (Level 4 if the target is production data) |
| Reading `.env`, key files, cloud credentials | 4 (use `.env.example` instead) |
| `git push --force`, rewriting published history | 4 without explicit owner direction |
| `git commit --no-verify`, disabling a hook or CI check | 4 |

## 3. How to request approval

State, before acting: the exact action, why it is needed, the risk, and how to undo it. Wait for a clear yes. Then act, and record the approval in the change report. Approval covers only what was described.

## 4. Per-tool enforcement

| Tool | What the repository configures | Enforcement strength |
| :--- | :--- | :--- |
| Claude Code | `.claude/settings.json`: `deny` rules for secret files and bypass flags; `ask` rules for Level 3 commands and guardrail files | Strongest of the agent tools: deny and ask rules are evaluated before allow rules, and `deny`/`ask` apply immediately. Per Claude Code's own documentation, Bash rules match the command text Claude writes and are **not a security boundary** (a different spelling of the same program can bypass them), so they complement, not replace, hooks, CI and sandboxing |
| GitHub Copilot | Instructions only (`.github/copilot-instructions.md`, `AGENTS.md`) | Advisory. Rely on Copilot's own tool-approval prompts, Git hooks and CI |
| Google Antigravity | Instructions only (`AGENTS.md`, skills) | Advisory. Use Antigravity's own approval modes |
| Cursor | Instructions only (`AGENTS.md`, `.cursor/rules/`) | Advisory. Cursor supports hooks (a `hooks.json` file in the `.cursor` folder) but none are configured here |
| Gemini CLI | Instructions only (`GEMINI.md`) | Advisory. Gemini CLI supports settings-based hooks; none are configured here |
| OpenAI Codex | Instructions only (`AGENTS.md`, skills) | Advisory. Use Codex's own sandbox and approval policy |
| Windsurf and others | `AGENTS.md` | Advisory |

For every tool, the universal layers still apply: Git hooks (`core.hooksPath`) and CI run the same validators regardless of which tool made the change. Configure each tool's own approval and sandbox mode conservatively on your machine; that is a personal or organisational setting this repository cannot force. See `docs/governance/AI_AGENT_COMPATIBILITY.md` for what each tool supports.

## 5. Role assignment

Assigning a role (`governance/roles/`) does not change a level. A "DevOps Engineer" agent still needs approval to edit a workflow.
