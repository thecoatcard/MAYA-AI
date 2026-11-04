@AGENTS.md

# Claude Code notes

The line above imports the canonical repository instructions (Claude Code reads `AGENTS.md` natively only when no `CLAUDE.md` exists, so this file imports it explicitly). Keep policy in `AGENTS.md` and `governance/`; do not add it here.

- **Workflows:** `/gov-<workflow>` skills in `.claude/skills/` (for example `/gov-plan-feature`). They are generated; run `npm run agents:sync` after changing a workflow.
- **Roles:** `role-<name>` subagents in `.claude/agents/`. A role grants no extra permissions.
- **Permissions:** `.claude/settings.json` holds `deny` rules for secret files and bypass flags and `ask` rules for Level 3 actions. Editing it is itself a Level 3 action. Claude Code documents Bash rules as not being a security boundary, so Git hooks and CI remain the enforcement layer.
- **Personal overrides** belong in `settings.local.json` (inside the `.claude` folder) and `CLAUDE.local.md`, both ignored by Git.
