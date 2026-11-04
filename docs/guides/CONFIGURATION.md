# Configuration reference

## Application environment variables

The application is not implemented yet, so it reads **no environment variables**, and `.env.example` defines none.

When code starts reading an environment variable:

1. Add it to `.env.example` with a safe placeholder value.
2. Add a row to the table below.
3. Run `npm run docs:check`. It fails if source code reads a variable that is missing from either place, or if `.env.example` lists a variable that is missing here.

| Variable | Purpose | Required | Example value |
| :--- | :--- | :--- | :--- |
| *(none yet)* | | | |

Never put real secrets in `.env.example` or in this document. Real values live in an untracked `.env` file or a secret manager.

## Governance configuration

`governance/governance.config.json` is the machine-readable policy that the validators read. It is a guardrail file: changing it needs explicit human approval.

| Section | Controls |
| :--- | :--- |
| `requiredFiles` | Files that must exist; `docs:check` fails if one is missing |
| `instructionFiles` | Size limit for `AGENTS.md` (24,000 bytes, the Antigravity truncation limit) |
| `agentWrappers` | Name prefixes and the hard-rules markers used by the agent-file generator |
| `markdown` | Which folders are documentation, and the unfinished-work patterns that are rejected |
| `modules` | Where module directories live; each needs a README or a page under `docs/modules/` |
| `env` | Source globs scanned for environment variable use, and names that need no documentation |
| `sensitive` | Files that must never be committed, and the guardrail paths that need a `Human-Approval:` trailer |
| `commits` | Allowed Conventional Commit types and header length |
| `changeRules` | Diff-based documentation, ADR and changelog requirements |
| `stacks` | How application projects are detected and which lint, type, test and build commands run |

## Tooling and CI variables

The governance scripts read no environment variables. In CI, the workflow passes the comparison base ref to `validate.mjs` as an argument. Settings that are not variables: `core.hooksPath` (set by `npm run setup`) and the repository's branch protection (`docs/operations/BRANCH_PROTECTION.md`).
