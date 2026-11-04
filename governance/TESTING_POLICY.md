# Testing Policy

## 1. What "tested" means

A change is **Tested** only when the relevant tests or checks were executed, passed, and the command is on record. "Looks correct", "should pass" and "tests exist" are not evidence. If a check could not run (missing tool, no network, no project yet), report it as **Not tested** or **Partially tested** with the reason.

## 2. Rules

1. New behavior and bug fixes come with tests. A bug fix starts with a test that fails for the right reason.
2. Never weaken, skip, delete or loosen a test to make it pass. If a test is wrong, fix it and explain why in the change report.
3. Tests are deterministic: no reliance on wall-clock time, network or ordering unless that is the thing under test. Fake clocks and local fixtures are preferred.
4. Tests do not touch real external services or real credentials. Use fixtures and mock servers.
5. Negative cases are mandatory for security and validation code: show that bad input is rejected, not only that good input works.
6. Keep fast checks fast. Slow suites run in pre-push and CI, not on every commit.

## 3. Layers

| Layer | Purpose | Runs in |
| :--- | :--- | :--- |
| Unit | Pure logic, parsers, validators, ciphers, verifiers | pre-commit (where fast), pre-push, CI |
| Integration | Components together with mock MCP servers, queue and database fixtures | pre-push (when present), CI |
| Gate and safety | The permission gate: no mutating call without a valid, unexpired, unconsumed, hash-matching approval | CI, required |
| Tenant isolation | Two workspaces; every endpoint and table shows zero cross-reads | CI, required |
| File verifiers | Generated documents are rejected when corrupt, truncated or out of bounds | CI |
| End-to-end | Approval flow, task status, voice states | CI, nightly |
| Evals (LLM-as-judge) | Plan quality, tool selection, parameter fidelity | CI or nightly; deterministic checks come first, because the judge shares a model family with the agents |
| Voice latency | First-audio latency and interrupt latency benchmarks | nightly |
| Chaos | Kill a worker, tool server or Redis mid-task and confirm resume without duplicate side effects | pre-release |

Layers beyond unit tests are introduced with the code they test.

## 4. The repository's own tests

The governance scripts are the enforcement layer, so they are tested like product code. `npm test` runs `node --test` over `scripts/**/*.test.mjs`. Any change to a validator needs a test for the new behavior, including a case that must fail.

## 5. Thresholds

Numeric thresholds (coverage, eval precision and recall, latency percentiles) are set from a measured baseline and ratcheted upward. Do not invent a target number to satisfy a checklist. The product plan's aspirational figures (for example 98% tool-selection precision) are goals, not current measurements.

## 6. Reporting results

State, per check: the exact command, the result, and anything skipped. For example: `npm test` passed (47 tests); `npm run docs:check` passed; web type check not run (no web project yet).
