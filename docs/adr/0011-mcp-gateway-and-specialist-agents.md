# ADR-0011: Model Context Protocol (MCP) Gateway and Specialist Agent Architecture

- **Status:** Proposed
- **Date:** 2026-11-07
- **Deciders:** Repository owner
- **Related:** [ADR-0006](0006-tenant-isolation-with-row-level-security.md), [ADR-0007](0007-gemini-model-gateway-architecture.md), [MAYA_AI_FINAL_PLAN.md §8, §9, §19](../../MAYA_AI_FINAL_PLAN.md)

## Context

Phase 08 (MCP Gateway & Integrations) and Phase 09 (Specialist AI Agents) require connecting specialized language model agents to external systems, filesystem workspaces, Git repositories, third-party APIs, and code execution sandboxes.

To ensure safety, multi-tenancy, and stability, several critical security constraints must be enforced:

1. **Decision D11 (Pinned Servers & Reproducible Dependencies):** Dynamic package downloading at runtime (e.g. `npx -y`, `uvx`) introduces supply chain risks, non-determinism, and network vulnerabilities.
2. **Decision D14 (Sandbox Runner Isolation):** Sandboxed code execution must never have access to host resources, especially `/var/run/docker.sock` or `//./pipe/docker_engine`. Sandboxes must run as non-root users with isolated ephemeral workspaces and strict resource limits.
3. **Decisions D4 & D5 (Tool Governance & Approval Tokens):** All tool operations must be classified into a four-tier permission model (`READ`, `WRITE`, `APPROVAL_REQUIRED`, `RESTRICTED`). Sensitive mutating or external operations require single-use approval tokens bound to RFC 8785 canonical hashes.
4. **Decision D8 (Single Subtask Worker Execution):** Subtask workers execute exactly 1 subtask per queued job message and must never block/sleep while waiting for human approval; they record state, pause, and yield execution.

## Decision

1. **MCP Gateway with Process Supervision & Connection Pooling:**
   - Implement managed stdio and streamable HTTP transports in `backend/app/mcp/`.
   - Implement `ProcessSupervisor` with 15-second JSON-RPC ping heartbeats, 3-strike degradation to `DEGRADED`, and exponential backoff restarts (1s, 2s, 4s, 8s) up to `DEAD`.
   - Provide fallback routing (e.g., GitHub to local Git, Brave Search to Gemini web search).

2. **Schema Normalization & Description Poisoning Defense:**
   - Map external JSON Schema tool definitions into Google GenAI `FunctionDeclaration` parameters.
   - Calculate deterministic SHA-256 hashes of canonical schemas (RFC 8785) and compare against persisted hashes in `mcp_servers.schema_hash`.
   - Fail closed with `ToolPoisoningError` if unexpected schema or description modifications are detected.
   - Enforce server hints to be elevation-only (cannot downgrade permissions).

3. **Vendor Leaky-Bucket Rate Limiter:**
   - Provide per-vendor token bucket limiters with RFC 2822 / seconds HTTP 429 `Retry-After` header parsing and jittered exponential backoff.

4. **Fixed-Function Sandbox Runner (Decision D14):**
   - Strictly prohibit `/var/run/docker.sock` and `docker_engine` named pipes (`SecurityViolationError`).
   - Run containers under non-root user (UID 10001) with `--network none` (unless explicitly whitelisted), 512MB RAM cap, 1.0 CPU cap, 60s timeout, and 1MB stdout/stderr truncation.
   - Use ephemeral volume mounts cleaned up reliably in `finally` blocks.

5. **Specialist AI Agent Fleet:**
   - Provide `BaseSpecialistAgent` with immutable `assigned_tools` allowlists and permission ceilings.
   - Implement `ResearchAgent` (READ ceiling, verifiable citations).
   - Implement `CodingAgent` (Workflow 3 bugfix: sandbox reproduction, patch, sandbox re-test to exit code 0, gated PR).
   - Implement `DocumentAgent` (artifacts strictly in `/artifacts/{workspace_id}/`, SHA-256 checksums in `file_artifacts`).
   - Implement `EmailAgent` (autonomous draft creation, gated email dispatch).
   - Implement `PMAgent` (Workflow 1 Daily Briefing from Calendar and Notion).

6. **SubtaskWorker Integration (Decision D8):**
   - Dispatch subtasks to matching specialist agents from `AgentFleetRegistry`.
   - Cleanly transition subtasks to `AWAITING_APPROVAL` and exit when approval is needed, persisting the `approval_request_id`.
   - Record verifiable proofs (`verification_proof`) upon subtask completion.

## Alternatives considered

- **Dynamic server loading with `uvx` / `npx -y`:** Rejected due to supply chain vulnerability and non-reproducible runtime environments (Decision D11).
- **Mounting Docker socket into sandbox containers (Docker-in-Docker):** Rejected due to root escalation risks and host breakout vulnerabilities (Decision D14).
- **Allowing workers to sleep/poll for human approval:** Rejected; worker slots would be exhausted while waiting for minutes or hours (Decision D8).

## Consequences

- Positive: Strong defense against tool poisoning, zero Docker socket exposure, deterministic reproducible environments, clean worker lifecycle, and verified agent actions.
- Negative: Additional maintenance overhead for in-house adapters; tools must be pre-registered and approved.
- Mitigations: Automated health checks, structured fallback routes, and comprehensive admin APIs for server registration.

## Security and compliance impact

Enforces multi-tenant workspace isolation, deny-by-default tool authorization, cryptographic single-use token consumption, and prevents privilege escalation through sandbox or tool description attacks.

## Implementation notes

- Implemented in `backend/app/mcp/`, `backend/app/agents/`, `backend/app/sandbox/`, `backend/mcp_servers/google_adapter/`, and `apps/web/src/components/`.
- Tested in unit and integration test suites in `backend/tests/` and `apps/web/src/`.
