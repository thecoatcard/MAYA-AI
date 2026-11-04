---
name: plan-feature
description: Produce a reviewable implementation plan with risks, approvals, test plan and documentation plan before writing code.
---

# Workflow: Plan Feature

## Objective
Turn a request into a minimal, risk-aware plan that a human can approve before implementation starts.

## Inputs
- The request and its acceptance criteria. If criteria are missing, propose them and ask for confirmation.

## Preconditions
- You have run `governance/workflows/understand-repository.md` for the affected area.

## Files to inspect
- Code that will change and its callers, tests and documentation
- ADRs and architecture documents that constrain the area
- `governance/governance.config.json` (`changeRules`) to see which documentation and ADR rules the change will trigger

## Steps
1. Restate the goal and acceptance criteria in your own words.
2. Search for existing implementations to extend instead of duplicating.
3. Choose the smallest design that meets the criteria; note one or two rejected alternatives and why.
4. List files to create or change, in order.
5. Identify risks: security, compatibility, data, operations, rollback.
6. List every Level 3 action the work will need (dependencies, migrations, workflow changes, external calls, pushes) so approval can be requested up front.
7. Write the test plan: layers, key cases, negative cases.
8. Write the documentation plan using the table in `governance/DOCUMENTATION_POLICY.md`. Decide whether an ADR is needed.
9. List open questions and assumptions.

## Required validation
- Nothing is executed beyond read-only commands. Check the plan against the hard rules in `AGENTS.md`.

## Documentation requirements
- If the design is architectural, draft an ADR from `templates/adr/TEMPLATE.md` with status Proposed.

## Approval requirements
- Medium and high risk plans need explicit human approval before implementation. Approval covers the listed actions only.

## Expected outputs
A plan containing: goal and criteria, design, file list, risks, Level 3 actions, test plan, documentation plan, ADR decision, open questions.
