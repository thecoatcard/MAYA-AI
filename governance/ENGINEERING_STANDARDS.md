# Engineering Standards

These standards apply to all code in the repository. The application stack is planned, not yet implemented (see `docs/architecture/SYSTEM_OVERVIEW.md`); stack-specific tools named below become mandatory when the first project of that kind lands, and adding one is an architecture decision that needs an ADR.

## 1. Understand before changing

- Inspect the files you will touch and their callers, tests and documentation.
- Read the relevant module documentation and any ADR that constrains the area.
- Look for an existing implementation before writing a new one; extend patterns already in use before introducing a new pattern.
- Identify downstream effects: public APIs, data formats, configuration, other modules.
- For non-trivial work, write a short plan first (see `governance/workflows/plan-feature.md`).

## 2. No unnecessary rewrites

- Preserve working functionality. Make targeted edits instead of replacing whole files.
- Do not refactor unrelated code in the same change.
- Do not add a dependency when the standard library or an existing dependency suffices. A new dependency is Level 3 (approval) and needs a reason in the change report.
- Do not change a public API without considering compatibility and documenting the change.
- Do not change environment configuration silently; update `.env.example` and the configuration reference.

## 3. Code quality

- **Structure:** separate concerns; keep modules cohesive with explicit interfaces; keep dependencies pointing inward from interfaces to core logic.
- **Typing:** use strong typing wherever the language supports it (Python type hints checked by mypy; TypeScript `strict`). Do not silence type errors without a comment explaining why.
- **Naming and size:** consistent, descriptive names; small, single-purpose functions.
- **Errors:** handle errors at the right layer, with specific exception types. Never swallow an exception. Fail loudly on programmer errors; recover deliberately from operational ones.
- **Logging:** structured logs with a correlation or task identifier. Never log secrets, tokens, full request bodies containing personal data, or raw user content without redaction.
- **Validation:** validate all input at trust boundaries (API, tool arguments, files, environment).
- **Constants:** no unexplained magic values; name them or configure them.
- **Duplication:** do not duplicate business logic; extract only when duplication is real, not speculative.
- **Cleanliness:** no dead code, no commented-out code, no temporary debug statements in production code, no unnecessary abstraction.

## 4. Production readiness

Consider each of these before calling work complete: security, reliability, scalability, observability, performance, maintainability, error recovery, testing, documentation and deployment impact.

For this product specifically (from the project plan):

- Mutating external actions must be idempotent and guarded by the permission gate.
- A step is complete only with verifiable proof (receipt, exit code, hash), never on model text alone.
- Long-running work must be checkpointed so it can resume after a crash without repeating side effects.
- Every tool call must be auditable.

## 5. Definition of done

A change is done only when all applicable items are true:

- [ ] It does what was asked and nothing unrelated.
- [ ] Tests cover the new or changed behavior and pass (or the gap is stated).
- [ ] `npm run validate` passes locally (or each failure and skip is explained).
- [ ] Documentation is updated in the same change, or the change report explains why none is needed.
- [ ] No secrets, debug leftovers or unexplained dependencies are introduced.
- [ ] Required approvals were obtained and recorded.
- [ ] A change report exists for non-trivial work, with accurate status labels.

## 6. Stack conventions (apply when the code exists)

| Area | Convention |
| :--- | :--- |
| Python | 3.12; Ruff for lint and format; mypy for types; pytest; Pydantic models at boundaries; async I/O in the API layer |
| TypeScript / Next.js | strict TypeScript; ESLint and Prettier; component tests; no secrets in client bundles |
| Database | migrations only (Alembic); every tenant table carries `workspace_id` with row-level security; never edit applied migrations |
| Containers | non-root, pinned base images, no host mounts for agent-controlled code, no Docker socket exposure |
| Tooling scripts | zero-dependency Node.js in `scripts/`; cross-platform (Windows, macOS, Linux); every script has tests |
