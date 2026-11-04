---
name: understand-repository
description: Build an accurate map of the repository (structure, stack, commands, conventions, risks) before any change. Read-only.
---

# Workflow: Understand Repository

## Objective
Produce an evidence-based picture of the repository so later work rests on facts, not assumptions.

## Inputs
- Optional focus area or question (for example "how are agent permissions enforced?").

## Preconditions
- None. This workflow is Level 1 (READ) and changes nothing.

## Files to inspect
- `AGENTS.md`, `governance/README.md`, `README.md`
- `docs/architecture/README.md`, `docs/architecture/SYSTEM_OVERVIEW.md`, `docs/adr/README.md`
- Manifests and lockfiles (`package.json`, `pyproject.toml`, and similar), CI under `.github/workflows/`
- The source, tests and docs of the focus area

## Steps
1. Read `AGENTS.md` and note the hard rules and permission levels.
2. List the directory structure; separate implemented code from planned items.
3. Identify the stack, package managers and the commands that actually exist (read scripts and CI, do not guess).
4. Read the architecture documents and any ADR that constrains the focus area.
5. Find existing patterns and conventions in code and tests for the area.
6. Mark sensitive paths (`sensitive.approvalRequiredPaths` in `governance/governance.config.json`) and secrets-bearing files you must not open.
7. Record unknowns and documentation that looks inaccurate or stale.

## Required validation
- Every claim in your summary is traceable to a file you read or a command you ran. Do not run anything beyond read-only commands.

## Documentation requirements
- None. If you find inaccurate documentation, report it; fixing it is a separate task (`governance/workflows/update-documentation.md`).

## Approval requirements
- None.

## Expected outputs
A short report: structure, stack, working commands, conventions, sensitive areas, risks, open questions, and documentation problems found, each with file references.
