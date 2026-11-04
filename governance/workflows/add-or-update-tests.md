---
name: add-or-update-tests
description: Add or update deterministic tests, including negative cases, without weakening existing tests.
---

# Workflow: Add or Update Tests

## Objective
Provide tests that fail when the behavior breaks and pass when it is correct.

## Inputs
- The behavior to cover (new feature, bug fix, or coverage gap).

## Preconditions
- You understand the behavior and where it is specified.

## Files to inspect
- The code under test, its existing tests and fixtures, `governance/TESTING_POLICY.md`
- The test runner configuration (for this repository's scripts: `package.json`, tests beside `scripts/`)

## Steps
1. State the behavior in one sentence per case, including failure and boundary cases.
2. Choose the lowest layer that proves it (unit before integration before end-to-end).
3. Write deterministic tests: no real network, credentials, wall-clock or ordering dependence.
4. Include negative cases for validation and security code: show bad input is rejected.
5. Run the new tests and confirm they pass; temporarily break the behavior (or run the test against the unfixed code) to confirm they can fail, then restore it.
6. Never weaken, skip or delete an existing test to get green. If a test is wrong, fix it and explain.
7. Run the whole suite (`npm test` for this repository's scripts, plus stack tests when they exist).

## Required validation
- Command, result and test count recorded; failures quoted.

## Documentation requirements
- Update `docs/testing/` (create it when first needed) if test strategy or fixtures change.

## Approval requirements
- None for tests. Test tooling dependencies are Level 3.

## Expected outputs
New or updated tests, evidence they can fail, full-suite results.
