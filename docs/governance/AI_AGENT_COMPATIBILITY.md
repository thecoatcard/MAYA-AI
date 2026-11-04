# AI agent compatibility

What each supported tool actually reads and supports, **verified against official documentation on 2026-11-05**. Anything not confirmed is marked "not verified" and no configuration was created for it. Vendors change these conventions often; re-verify before relying on a row (see the end of this page).

Legend: **Yes** = confirmed in the vendor's documentation. **Not verified** = the documentation read did not confirm it, so this repository does not configure it.

## Matrix

| Agent | Instruction entry point | Rules support | Hooks support | Workflows support | Limitations |
| :--- | :--- | :--- | :--- | :--- | :--- |
| **Claude Code** | `CLAUDE.md`, which imports `AGENTS.md` with `@AGENTS.md`. Claude reads `AGENTS.md` directly only when no `CLAUDE.md` exists in or above the working directory (v2.1.277 or later). | Yes: markdown files in `.claude/rules/` (optional `paths` front matter, not used here). Permissions in `.claude/settings.json` (`allow`, `ask`, `deny`). | Yes: `hooks` in settings; 32 lifecycle events; a `PreToolUse` hook exits with code 2 to block. **Not configured here.** | Yes: skills `.claude/skills/<name>/SKILL.md` (custom commands are merged into skills). Subagents in `.claude/agents/<name>.md`. | `CLAUDE.md` is context, not enforcement. Bash permission rules match command text and are documented as not a security boundary. `allow` rules in a committed settings file wait for workspace trust; `deny` and `ask` apply immediately. |
| **GitHub Copilot** | `.github/copilot-instructions.md`. `AGENTS.md` anywhere in the repository (nearest wins) and a single root `CLAUDE.md` or `GEMINI.md` are also supported per GitHub's docs. | Yes: `.github/instructions/*.instructions.md` with `applyTo` globs (and optional `excludeAgent`). On GitHub.com these apply to the Copilot cloud agent and code review only. | Not verified | Yes (VS Code): prompt files `.github/prompts/*.prompt.md`, run only in local chat sessions. | The docs read do not say which Copilot surfaces (IDE chat, CLI, cloud agent) read `AGENTS.md`. Prompt files do not run in other harnesses. |
| **Google Antigravity** | `AGENTS.md` and `GEMINI.md` (both always on, no front matter), looked up from the workspace folder up to the repository root. | Yes: `.agents/rules/*.md` with a required `trigger` (`always_on`, `model_decision`, `glob`, `manual`). `.agent/rules/*.md` is accepted as legacy. | Not verified | **Changed:** legacy workflows stop working on 2026-10-19 (already passed). Use skills: `.agents/skills/<name>/SKILL.md` with `name` and `description`, invoked with `/name`. | Rule files over 24,000 bytes are truncated; active always-on rules share a 20,000-token budget. No precedence is defined between files; all are combined. Folders below the workspace folder are not loaded. |
| **Cursor** | `AGENTS.md` in the root and subdirectories. | Yes: `.cursor/rules/*.mdc` (the `.mdc` extension is required) with `description`, `globs`, `alwaysApply`. | Yes: `hooks.json` in the `.cursor` folder (project level), `version: 1`, events such as `beforeShellExecution`, `afterFileEdit`, `preToolUse`. **Not configured here.** | Not verified: custom commands in `.cursor/commands/` were not confirmed, so none are created. `gov-*` rules provide the workflows (applied when relevant, or by mentioning the rule). | Whether the legacy `.cursorrules` file is deprecated was not confirmed. |
| **Gemini CLI** | `GEMINI.md` (global, workspace and just-in-time hierarchy). Supports `@file` imports; this repository's `GEMINI.md` imports `@./AGENTS.md`. | `GEMINI.md` hierarchy only. `context.fileName` in settings can add `AGENTS.md` as a context name (not set here to avoid loading it twice). | Yes: `hooks` in `.gemini/settings.json` with events such as `BeforeTool`; exit code 2 blocks. **Not configured here.** | Not verified: custom commands were not confirmed, so none are created. Workflows are plain files the agent reads. | Does not read `AGENTS.md` by default. |
| **OpenAI Codex** | `AGENTS.md`: global (`~/.codex`), then from the project root down to the working directory, one file per directory (`AGENTS.override.md` takes precedence), concatenated root to leaf. | `AGENTS.md` only. Other file names (including `CLAUDE.md`) are read only if listed in `project_doc_fallback_filenames`. | Not verified: Codex documents a separate hooks page that was not read. | Yes: skills `.agents/skills/<name>/SKILL.md` with `name` and `description`; invoked with `$name` or `/skills`. | Combined instruction size is capped (`project_doc_max_bytes`, default 32 KiB). A trusted project-level `.codex/config.toml` exists, but its details were not verified, so none is created. |
| **Windsurf** | `AGENTS.md`: listed as supported on agents.md, and Windsurf's documentation (now hosted under Devin Desktop) was reported by search results to support it. **Only partly verified.** | `.windsurf/rules/*.md` is referenced as a legacy fallback. Not verified further. | Not verified | Not verified | Documentation moved; details unconfirmed. No `.windsurf/` files are created. |
| **Other agents** | Fallback: point the tool's custom-instructions setting at `AGENTS.md`, or paste its "Hard rules" section. | n/a | n/a | Read the files in `governance/workflows/` directly. | Advisory only. |

## Enforcement for every agent, regardless of native support

Where an agent has no native hooks, or where its hooks are not configured, the shared checks still apply because they sit in the repository rather than in the agent:

1. **Git hooks** (`scripts/hooks/`, activated by `npm run setup`) run on commit and push for anyone, including agents driving Git.
2. **CI** (`.github/workflows/`) repeats the checks on every pull request.
3. **Branch protection and Code Owners** (a manual step, see `docs/operations/BRANCH_PROTECTION.md`) make the CI checks required.

These can be bypassed locally (`--no-verify`, not running setup); only layers 2 and 3 are the gate.

## Deliberately not created

| Not created | Why |
| :--- | :--- |
| `.agent/workflows/` | Antigravity workflows stopped working on 2026-10-19; skills in `.agents/skills/` replace them. |
| `.agent/rules/` | Legacy path; `AGENTS.md` is read natively, so no rule files are needed. |
| `.codex/README.md` | Codex does not read it; `AGENTS.md` is its entry point. |
| `.windsurf/`, `.cursor/commands/`, `.gemini/commands/` | Conventions not confirmed in the documentation read. |
| Hooks for Claude Code, Cursor and Gemini CLI | Each tool supports them, but they would only protect one tool; the shared Git hooks and CI cover all. They can be added later with an ADR. |

## Sources (read 2026-11-05)

- Claude Code: [memory and CLAUDE.md](https://code.claude.com/docs/en/memory), [settings](https://code.claude.com/docs/en/settings), [permissions](https://code.claude.com/docs/en/permissions), [hooks](https://code.claude.com/docs/en/hooks), [subagents](https://code.claude.com/docs/en/sub-agents), [skills](https://code.claude.com/docs/en/skills)
- GitHub Copilot: [repository custom instructions](https://docs.github.com/en/copilot/how-tos/configure-custom-instructions/add-repository-instructions), [VS Code prompt files](https://code.visualstudio.com/docs/copilot/customization/prompt-files)
- Google Antigravity: [rules](https://antigravity.google/docs/rules/), [workflows to skills migration](https://antigravity.google/docs/migration/workflows-to-skills/)
- Cursor: [rules](https://cursor.com/docs/context/rules), [hooks](https://cursor.com/docs/agent/hooks)
- Gemini CLI: [GEMINI.md](https://geminicli.com/docs/cli/gemini-md/), [configuration](https://geminicli.com/docs/reference/configuration/), [hooks](https://geminicli.com/docs/hooks/)
- OpenAI Codex: [AGENTS.md](https://learn.chatgpt.com/docs/agent-configuration/agents-md), [skills](https://learn.chatgpt.com/docs/build-skills)
- Windsurf and shared format: [agents.md](https://agents.md/)

## How to re-verify

Open the source for the row, confirm the file names, front matter keys and limits, update the matrix and date, and if a convention changed update the generator in `scripts/governance/sync-agent-files.mjs` and run `npm run agents:sync`.
