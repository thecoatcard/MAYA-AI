---
name: implement-feature
description: Implement an approved plan with tests and documentation, validate it, and finish with an honest change report.
---

# Workflow: Implement Feature

## Objective
Deliver the approved change as a small, tested, documented, validated diff.

## Inputs
- An approved plan (`governance/workflows/plan-feature.md`) or a clearly small request.

## Preconditions
- Required approvals for Level 3 actions are in hand and recorded.
- Working tree state is known; you will not overwrite changes you did not make.

## Files to inspect
- Every file you will edit, re-read immediately before editing
- Neighbouring code and tests, to match existing patterns
- The documentation that describes the area

## Steps
1. Re-read the target files; confirm the plan still fits.
2. Implement in small slices, following `governance/ENGINEERING_STANDARDS.md`. No unrelated edits, no unexplained dependencies.
3. Add or update tests alongside each slice (`governance/workflows/add-or-update-tests.md`).
4. Update documentation in the same change (`governance/DOCUMENTATION_POLICY.md`).
5. If a Level 3 action becomes necessary that the plan did not cover, stop and ask.
6. Run `npm run validate:fast` while working and `npm run validate` before finishing.
7. Review your full diff for leftovers, secrets, debug output and scope creep.
8. Write the change report.

## Required validation
- `npm run validate` and any stack checks that exist. Record each command and result; list skipped checks as skipped.

## Documentation requirements
- Per the policy table; changelog entry for `feat`, `fix`, `security` and `perf` changes.

## Approval requirements
- Each Level 3 action individually. Do not commit or push unless asked.

## Expected outputs
The diff, passing validation (or an honest account of what did not pass), and a completed `templates/change-report/TEMPLATE.md`.
