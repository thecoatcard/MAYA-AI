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

| ADR                                                      | Title                                                                   | Status   |
| :------------------------------------------------------- | :---------------------------------------------------------------------- | :------- |
| [0001](0001-canonical-agent-governance.md)               | Canonical AI-agent governance with generated per-tool wrappers          | Proposed |
| [0002](0002-native-git-hooks-with-node-automation.md)    | Native Git hooks with zero-dependency Node.js automation                | Proposed |
| [0003](0003-task-queue-selection.md)                     | Compatible Celery task queue; durable cooperative cancellation required | Accepted |
| [0004](0004-local-foundation.md)                         | Local API, web and data-service foundation                              | Proposed |
| [0005](0005-own-account-authentication.md)               | Own-account authentication, one-time codes and sessions                 | Accepted |
| [0006](0006-tenant-isolation-with-row-level-security.md) | Tenant isolation with forced row-level security                         | Accepted |
| [0007](0007-gemini-model-gateway-architecture.md)        | Gemini model gateway architecture, resilience and token accounting      | Proposed |
| [0008](0008-chat-api-contract.md)                        | Chat API contract, idempotency and persistence                          | Accepted |
| [0009](0009-refresh-cookie-transport.md)                 | HttpOnly refresh-cookie transport for the browser client                | Accepted |
| [0010](0010-checkpoint-owner-isolation.md)               | Bind LangGraph checkpoints to workspace and user owners                 | Proposed |
| [0011](0011-mcp-gateway-and-specialist-agents.md)        | MCP Gateway and Specialist Agent Architecture                           | Proposed |
| [0013](0013-desktop-shell-electron.md)                   | Desktop shell with Electron (overrides the plan's Tauri / post-MVP)     | Accepted |
| [0014](0014-local-owner-bootstrap.md)                    | Local-owner bootstrap for the desktop app (extends ADR-0005)            | Accepted |
| [0012](0012-in-process-task-queue.md)                    | In-process asyncio task queue, single worker (amends 0003)              | Accepted |
| [0015](0015-voice-protocol-v2.md)                        | Voice WebSocket protocol v2: one conversation, honest failures          | Accepted |
| [0016](0016-chat-function-calling-and-classifier.md)     | Chat starts tasks by function calling; optional classifier (off)        | Accepted |
| [0017](0017-themed-document-generation.md)               | Themed document generation: browser PDF, native PPTX, DOCX              | Proposed |
| [0018](0018-chat-memory.md)                              | Chat saves and recalls memories (tools, recall block, per-user switch)  | Proposed |
| [0019](0019-self-healing-task-execution.md)              | Self-healing task execution: plan repair, run-time recovery, spec rebuild | Proposed |
| [0020](0020-long-document-pipeline.md)                   | Long-document pipeline (outline, chapters, assemble) and honest length acceptance | Proposed |
| [0021](0021-agent-key-affinity-and-model-policy.md)      | Per-agent API key affinity, instant key failover, one agent model (lite) | Proposed |
| [0022](0022-connected-folder-and-uploads.md)             | Connected folder, uploaded files, editing as new versions, project writer | Proposed |
| [0023](0023-unified-agents-and-custom-agents.md)         | Three unified agents (Research, Builder, Operator) and folder-defined custom agents | Proposed |
| [0024](0024-per-user-connections.md)                    | Per-user GitHub and Notion connections, encrypted at rest | Proposed |
| [0026](0026-chat-tool-loop-and-jobs.md)                 | Single tool-using chat loop, job runner for long work, agents retired in phases | Proposed |
| [0028](0028-mcp-client.md)                              | MCP client adapter: third-party MCP servers behind the gate, host-run, off by default | Proposed |
| [0029](0029-gemini-decision-model.md)                   | Gemini decision model: advisory, fail-open checks that never loosen approvals | Proposed |
| [0031](0031-learning-book-pipeline-and-large-notion-writes.md) | Learning-book pipeline for notes (PDF, DOCX, Notion) and large Notion page writes | Proposed |
| [0032](0032-deep-research.md) | Deep research mode: wide search, cited result, honest counts, hard caps | Proposed |
| [0033](0033-scheduled-reports.md) | Scheduled reports: settings-JSON store, safe unattended runs, system mailer, verified recipients, wake-PC mode | Proposed |

Product-level decisions from the project plan (modular monolith, voice/task separation, permission tiers, and others) will be recorded here as ADRs when implementation starts.
