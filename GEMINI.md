@./AGENTS.md

# Gemini CLI notes

The line above imports the canonical repository instructions using Gemini CLI's `@file` syntax. Gemini CLI does not read `AGENTS.md` by default, which is why this file exists. Keep policy in `AGENTS.md` and `governance/`; do not add it here.

Google Antigravity reads both `AGENTS.md` and `GEMINI.md` directly, so there the import line above is inert text and the instructions still load once from `AGENTS.md`.

Reusable procedures are plain Markdown files in `governance/workflows/`; read the relevant one and follow it. Role definitions are in `governance/roles/`.
