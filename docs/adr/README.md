# Architecture Decision Records

An ADR captures a decision that materially affects the system, the alternatives considered, and the consequences. ADRs are append-only history: never delete one; supersede it.

## When an ADR is required

Meaningful decisions about architecture, frameworks, database, authentication, authorization, AI model providers, agent orchestration, MCP integrations, background processing, storage, infrastructure, security boundaries, and major dependencies. Not for small implementation details.

The `architecture` rule in `governance/governance.config.json` asks for an ADR (or an `ADR: not-required: <reason>` commit trailer) when feature, refactor, security, performance or build commits touch architecture-level paths.

## Statuses

**Proposed**, **Accepted**, **Rejected**, **Deprecated**, **Superseded** (link to the replacing ADR).

## How to add one

1. Copy `templates/adr/TEMPLATE.md` to a new file in this directory named `NNNN-short-title.md`, using the next number.
2. Fill every section. Set the status to Proposed.
3. Add it to the index below in the same change.
4. A human accepts it (status becomes Accepted) when the decision is approved.

## Index

| ADR | Title | Status |
| :--- | :--- | :--- |
| [0001](0001-canonical-agent-governance.md) | Canonical AI-agent governance with generated per-tool wrappers | Proposed |
| [0002](0002-native-git-hooks-with-node-automation.md) | Native Git hooks with zero-dependency Node.js automation | Proposed |
| [0003](0003-task-queue-selection.md) | Compatible Celery task queue; durable cooperative cancellation required | Accepted |
| [0004](0004-local-foundation.md) | Local API, web and data-service foundation | Proposed |

Product-level decisions from the project plan (modular monolith, voice/task separation, permission tiers, and others) will be recorded here as ADRs when implementation starts.
