# System overview

> **Status: local foundation implemented; product features remain planned.** The authoritative product detail, decisions and roadmap are in [MAYA_AI_FINAL_PLAN.md](../../MAYA_AI_FINAL_PLAN.md).

## Implemented foundation

The [backend](../../backend/README.md) provides typed configuration, redacted JSON logs,
resource lifecycles and public [health probes](../api/HEALTH.md). The
[web scaffold](../../apps/web/README.md) provides a static overview, not chat or auth.
Local PostgreSQL/pgvector and Redis AOF are described in the
[data-store state](DATA_MODEL.md) and [runbook](../operations/LOCAL_DEVELOPMENT.md).
Phase 02 added the [application schema with forced row-level security](DATA_MODEL.md) and own-account
[authentication](../security/AUTHENTICATION.md) ([API](../api/AUTH.md)). There is no browser login or
browser-to-API product flow, model gateway or agent engine yet.

[S3 voice readiness](SPIKE_S3_GEMINI_LIVE.md) and
[S4 embedding readiness](SPIKE_S4_EMBEDDINGS.md) have offline fixture analyzers
and separately locked actual SDK adapters validated with mocked clients.
They have not contacted Gemini or verified live behavior; the owner's hard spending-cap
requirement blocks live execution. No voice bridge or model gateway is implemented.

## What MAYA AI is meant to be

A voice-first, multi-agent "digital employee": it accepts an instruction by voice or text, breaks it into a dependency graph of subtasks, runs them through specialised agents that use external tools through the Model Context Protocol (MCP), verifies results deterministically, asks a human before consequential actions, and reports back.

## Planned components

| Component | Responsibility |
| :--- | :--- |
| Web client | Chat and voice canvas, task center, approval center |
| Voice service | Real-time audio to and from the Gemini Live API; hands work to the task engine |
| API and task engine | Planning, orchestration, checkpointing, idempotency, permission gate, approvals |
| Agent workers | Research, coding, document, email and read-only project-management agents |
| MCP gateway | Connects agents to tool servers; enforces permissions, sanitises output, supervises servers |
| Sandbox runner | Fixed-function, isolated code execution (no Docker socket exposed to agents) |
| Data stores | PostgreSQL with pgvector and row-level security, Redis, S3-compatible artifact storage |

## Planned stack

Python 3.12 with FastAPI, LangGraph and a task queue; Next.js with TypeScript; PostgreSQL 17 with pgvector; Redis; Docker. Model access goes through a gateway that maps roles to model identifiers held in configuration.

## Key planned design decisions

These come from the plan and each needs an ADR in `docs/adr/` when implementation begins:

- A modular monolith first; services are split out later only if needed.
- Voice and task execution are separate runtimes joined by an event bus.
- Four permission tiers, deny by default, with payload-bound single-use human approvals.
- Execution state has one source of truth (the LangGraph checkpointer).
- Every tenant table carries a workspace identifier protected by row-level security.
- MCP servers are pinned to exact versions and built into images.

## Governance

How AI agents and humans change this repository is defined in [governance](../../governance/README.md).
