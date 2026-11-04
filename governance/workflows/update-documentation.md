---
name: update-documentation
description: Update or correct documentation so it matches implemented behavior, and pass the documentation checks.
---

# Workflow: Update Documentation

## Objective
Make documentation accurate, complete for the change, and valid.

## Inputs
- A code change (diff), or a documentation gap or inaccuracy report.

## Preconditions
- You can read the code the documentation describes. Never write from the plan alone.

## Files to inspect
- The diff or code in question
- The existing documentation for it, `governance/DOCUMENTATION_POLICY.md`, and the templates in `templates/`
- `CHANGELOG.md`, `.env.example`, `docs/guides/CONFIGURATION.md`

## Steps
1. Map the change type to the required documents using the table in `governance/DOCUMENTATION_POLICY.md`.
2. Verify each statement against the code or by running the command. Fix wrong statements.
3. Create or update documents from the templates. Use safe placeholder values and never real secrets.
4. Mark unbuilt functionality as planned; do not describe it as present.
5. Update diagrams, setup steps, the root `README.md`, and the changelog as applicable.
6. Run `npm run docs:check` and fix every finding.
7. If no documentation change is needed, record the specific reason.

## Required validation
- `npm run docs:check` passes, or failures are listed with the reason.

## Documentation requirements
- This workflow is the documentation requirement.

## Approval requirements
- Editing `governance/` or `AGENTS.md` is Level 3. Other documentation is Level 2.

## Expected outputs
Updated documents, a passing documentation check, and a note of anything you could not verify.
