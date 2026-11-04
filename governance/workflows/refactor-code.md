---
name: refactor-code
description: Restructure code without changing behavior, protected by tests and made in small verified steps.
---

# Workflow: Refactor Code

## Objective
Improve structure, clarity or duplication with provably unchanged behavior.

## Inputs
- The target code and the specific problem the refactor solves.

## Preconditions
- The request actually calls for a refactor. Do not refactor opportunistically inside another task.
- Tests cover the behavior, or you can add characterization tests first.

## Files to inspect
- The target code, all callers, and its tests
- ADRs and module documentation that describe the intended structure

## Steps
1. Run the existing tests and record the baseline.
2. Add characterization tests where coverage is missing; confirm they pass on the unchanged code.
3. Refactor in small steps. After each step, run the tests.
4. Keep public interfaces, configuration and behavior unchanged. If an interface must change, stop and treat it as a feature with a plan.
5. Remove only code made dead by the refactor. Do not mix in unrelated cleanups.
6. Update documentation that describes the structure. If the architecture changes, write an ADR.

## Required validation
- The same tests pass before and after. Run `npm run validate`.

## Documentation requirements
- Update module or architecture docs when structure changes; otherwise state that none were needed.

## Approval requirements
- Level 3 if it touches protected paths or changes architecture-level boundaries.

## Expected outputs
A behavior-preserving diff, baseline and final test results, and a change report stating that behavior is unchanged and how that was verified.
