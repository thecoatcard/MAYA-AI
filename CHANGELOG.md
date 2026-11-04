# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Newest entries first; unreleased work goes under "Unreleased".

## [Unreleased]

### Added

- Canonical AI-agent instructions in `AGENTS.md`, with thin entry points for Claude Code, GitHub Copilot, Google Antigravity, Cursor, Gemini CLI and OpenAI Codex, verified against vendor documentation (`docs/governance/AI_AGENT_COMPATIBILITY.md`).
- Governance policies, 12 reusable workflows and 9 development roles under `governance/`, plus a generator that produces the per-tool skills, prompts, rules and subagents and fails on drift.
- Shared Git hooks (`pre-commit`, `commit-msg`, `pre-push`) and an equivalent GitHub Actions pipeline, both built on one set of validators: documentation, secrets, blocked files, config syntax, workflow security, commit messages, and documentation/ADR/changelog change rules.
- Claude Code permission settings (`deny` for secret files and bypass flags, `ask` for approval-level actions).
- Documentation templates, ADR process with two initial ADRs, onboarding, configuration reference, troubleshooting and branch protection guides.
- Repository audit (`docs/governance/REPOSITORY_AUDIT.md`) and setup validation report (`docs/governance/SETUP_VALIDATION_REPORT.md`).
- Initial project plan, `MAYA_AI_FINAL_PLAN.md`.
