# ADR-0001: Canonical AI-agent governance with generated per-tool wrappers

- **Status:** Proposed (awaiting repository owner acceptance)
- **Date:** 2026-11-05
- **Deciders:** Proposed by the setup agent (Claude Code); to be accepted by the repository owner
- **Related:** `AGENTS.md`, `governance/README.md`, `docs/governance/AI_AGENT_COMPATIBILITY.md`

## Context

Several AI coding agents (Claude Code, GitHub Copilot, Google Antigravity, Cursor, Gemini CLI, OpenAI Codex, Windsurf) will work in this repository. Each reads different instruction files, and the formats and capabilities differ. Without a shared system each agent would follow its own rules and drift apart. Verification against official documentation (2026-11-05) showed that `AGENTS.md` is read natively by Codex, Cursor and Antigravity, and is supported by Copilot; Claude Code reads it only when no `CLAUDE.md` exists; Gemini CLI needs an explicit import.

## Decision

1. `AGENTS.md` is the single canonical instruction file, kept under 24,000 bytes (the strictest documented limit, Antigravity's per-file truncation).
2. Detailed policy lives in `governance/`; workflows and roles are canonical Markdown files there.
3. Tool-specific files are thin entry points: `CLAUDE.md` and `GEMINI.md` import `AGENTS.md`; everything else that a tool needs (skills, subagents, prompts, Cursor rules, Copilot instructions) is **generated** by `scripts/governance/sync-agent-files.mjs` from the canonical sources, and CI fails on drift.
4. Only files verified in official documentation are created. Unverified conventions are documented as unsupported, not guessed.

## Alternatives considered

- **Symlinks to `AGENTS.md`:** not portable on Windows and unsupported by some tools.
- **Hand-copied policy in each tool's file:** guaranteed drift.
- **Only `AGENTS.md`, no wrappers:** loses native invocation (skills, prompts, subagents) in tools that offer it, and fails for tools that do not read `AGENTS.md`.

## Consequences

- Positive: one place to change a rule; drift is detected automatically; each tool gets its native entry point.
- Negative: a generator to maintain; wrappers add files to the repository; instruction conventions change over time, so the compatibility matrix must be re-verified periodically.
- Risk: instructions are advisory. Enforcement comes from Git hooks, CI and branch protection (ADR-0002).

## Security and compliance impact

Agent instruction files are a prompt-injection target and a way to weaken guardrails, so they are protected paths: changes need explicit human approval and a `Human-Approval:` commit trailer.

## Review trigger

Revisit when a supported tool changes its instruction discovery rules, or when a new agent needs support.
