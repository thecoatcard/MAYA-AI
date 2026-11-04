# ADR-0002: Native Git hooks with zero-dependency Node.js automation

- **Status:** Proposed (awaiting repository owner acceptance)
- **Date:** 2026-11-05
- **Deciders:** Proposed by the setup agent (Claude Code); to be accepted by the repository owner
- **Related:** `governance/GIT_WORKFLOW.md`, `scripts/validation/validate.mjs`, ADR-0001

## Context

Checks must run regardless of which agent or person made a change, so enforcement belongs in the repository, not in any one agent's lifecycle. The primary development machine (Windows 11) has Node.js 24 and Git but no usable Python, `make`, Gitleaks, Semgrep or the `pre-commit` framework. The application stack will include Next.js, so Node.js will be present on every developer machine anyway.

## Decision

1. Hooks are plain files in `scripts/hooks/`, activated with `git config core.hooksPath scripts/hooks` by `npm run setup`.
2. Every check is a zero-dependency Node.js script in `scripts/validation/`, tested with Node's built-in test runner. The only dev dependency is the pinned `yaml` parser used for syntax checks.
3. One orchestrator (`validate.mjs`) defines the checks for each stage; hooks and CI both call it.
4. CI (GitHub Actions) re-runs the same validators and adds Gitleaks and dependency review. Third-party actions are pinned to commit SHAs.
5. `npm run` scripts are the task runner; no `Makefile`.
6. A local heuristic secret scanner is a fast backstop; Gitleaks in CI is the authoritative scan.

## Alternatives considered

- **`pre-commit` framework:** requires Python, which is unavailable on the primary machine.
- **Husky or Lefthook:** extra dependencies and install steps for no benefit over `core.hooksPath`.
- **Makefile:** `make` is not installed on Windows by default, so it could not be tested here.
- **Claude-specific hooks:** only help one tool.

## Consequences

- Positive: works on Windows, macOS and Linux with no new runtime; checks are testable; hooks and CI cannot diverge.
- Negative: hooks are bypassable with `--no-verify` or by skipping `npm run setup`; the secret scanner is heuristic.
- Mitigation: CI repeats every critical check; branch protection requires them (`docs/operations/BRANCH_PROTECTION.md`).

## Review trigger

Revisit if the toolchain changes (for example Python becomes the dominant language of the tooling) or hook runtime becomes a developer complaint.
