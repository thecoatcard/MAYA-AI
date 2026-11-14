# Phase 08 & Phase 09 Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement, integrate, test, audit, and verify Phase 08 (MCP Gateway & Integrations) and Phase 09 (Specialist AI Agents) to production-grade standards while preserving all Phase 01–07 architectural, security, and tenant-isolation guarantees.

**Architecture:** Build a central, hardened MCP Gateway (stdio subprocess pool, Streamable HTTP, 15s supervisor ping, leaky-bucket rate limiter, RFC 8785 SHA-256 schema-hash poisoning defense, and 5 core connectors) and 5 specialist domain agents (Research, Coding with non-root Sandbox Runner, Document, Email, and PM) integrated into LangGraph, SubtaskWorker, and the Next.js web interface.

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0, PostgreSQL 17 (pgvector, RLS), Redis 7.4, Google GenAI SDK (`google-genai`), LangGraph, Next.js 15, React 19, TypeScript 5, Vitest, Pytest.

**Spec:** `MAYA_AI_FINAL_PLAN.md` (§8, §9, §19, Decisions D4, D5, D8, D11, D14), `docs/implementation/IMPLEMENTATION_PHASES/PHASE_08_MCP_INTEGRATIONS.md`, `docs/implementation/IMPLEMENTATION_PHASES/PHASE_09_SPECIALIST_AGENTS.md`.

## Global Constraints

- **Decision D4:** Four permission tiers (`READ`, `WRITE`, `APPROVAL_REQUIRED`, `RESTRICTED`), deny-by-default for unregistered tools, elevation-only server hints.
- **Decision D5:** Single-use signed approval tokens bound to RFC 8785 canonical JSON payload hash; visual UI approval only; voice channel returns HTTP 403 on approve.
- **Decision D8:** Exactly 1 subtask per queued job; workers never sleep or block waiting on human approval.
- **Decision D11:** Pinned servers and reproducible dependencies; zero runtime `npx -y` / `uvx` package downloads; manifest-only server catalog.
- **Decision D14:** Host Docker socket (`/var/run/docker.sock`) is NEVER exposed; Sandbox Runner uses fixed-function non-root container with strict resource/network boundaries.
- **RLS Isolation:** Every tenant table enforces PostgreSQL RLS with `app.workspace_id` scoping; zero cross-tenant leakage.
- **No Regressions:** All existing 1,738 backend tests, 852 frontend tests, and 48 governance tests must continue passing.

## Review Focus

1. **Tool Description Poisoning:** Modified description in MCP server `tools/list` must fail closed at startup (`SchemaPoisoningError`) and refuse to register.
2. **Payload Tampering at MCP Boundary:** Any parameter modification between approval creation and tool execution must fail token verification.
3. **Sandbox Escape & Socket Probing:** Container execution must not mount `/var/run/docker.sock`, must execute as UID 10001, and must block outbound network traffic.
4. **Approval Replay & Worker Race:** Two workers attempting to claim the same approved subtask concurrently must result in exactly 1 claim and 1 failure via atomic CAS.
5. **Path Traversal in Filesystem MCP:** Any path with `../` or symlink escaping `/artifacts/{workspace_id}` must be blocked immediately.

---

### Task 1: Database Models & Migration (Phase 08)

**Files:**

- Modify: `backend/app/db/models.py`
- Create: `backend/app/db/migrations/versions/0011_phase_08_mcp_servers.py`
- Create: `backend/app/db/repositories/mcp_servers.py`
- Test: `backend/tests/postgres/test_mcp_servers_postgres.py`

**Interfaces:**

- Produces: `McpServer(Base)`, `FileArtifact(Base)` models; `McpServerRepository` with `create_server`, `get_server_by_id`, `list_servers_for_workspace`, `update_health_status`, `delete_server`.
- Consumes: `Base`, `Workspace`, `User` from `app.db.models`.

- [ ] **Step 1: Write failing PostgreSQL tests for `McpServer` and `FileArtifact` models, migration 0011, and RLS policies**
- [ ] **Step 2: Run test to verify it fails (`pytest backend/tests/postgres/test_mcp_servers_postgres.py`)**
- [ ] **Step 3: Define `McpServer` and `FileArtifact` in `backend/app/db/models.py` and create migration `0011_phase_08_mcp_servers.py`**
- [ ] **Step 4: Implement `McpServerRepository` in `backend/app/db/repositories/mcp_servers.py`**
- [ ] **Step 5: Run tests against PostgreSQL 17 to verify migration upgrade/downgrade and RLS pass**

---

### Task 2: Schema Normalizer & Poisoning Defense (Phase 08)

**Files:**

- Create: `backend/app/mcp/normalizer.py`
- Test: `backend/tests/unit/test_schema_normalizer.py`

**Interfaces:**

- Produces: `normalize_mcp_tool_to_genai(tool_def: dict[str, Any]) -> ToolDefinition`, `compute_server_schema_hash(tools: list[dict[str, Any]]) -> str`, `verify_schema_hash(live_tools: list[dict[str, Any]], expected_hash: str) -> bool`, `SchemaPoisoningError`.
- Consumes: `app.mcp.approvals.canonical_json` (RFC 8785).

- [ ] **Step 1: Write unit tests for schema type conversion, unsupported keyword stripping, RFC 8785 hash computation, and drift detection**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement `normalizer.py` with type translation (`STRING`, `INTEGER`, `NUMBER`, `BOOLEAN`, `ARRAY`, `OBJECT`), sanitization, and deterministic SHA-256 hashing**
- [ ] **Step 4: Run tests to verify they pass**

---

### Task 3: Vendor Rate Limiter (Phase 08)

**Files:**

- Create: `backend/app/mcp/limiter.py`
- Test: `backend/tests/unit/test_mcp_limiter.py`

**Interfaces:**

- Produces: `VendorRateLimiter`, `LeakyBucket`, `VendorRateLimitError`.
- Consumes: Monotonic clock, standard asyncio primitives.

- [ ] **Step 1: Write unit tests for leaky-bucket burst handling, leak rate delay, HTTP 429 `Retry-After` parsing, and jittered backoff**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement `VendorRateLimiter` with per-vendor profiles (`brave`, `github`, `google`, `notion`) and 429 backoff**
- [ ] **Step 4: Run tests to verify they pass**

---

### Task 4: Transports & Connection Management (Phase 08)

**Files:**

- Create: `backend/app/mcp/transports/__init__.py`
- Create: `backend/app/mcp/transports/base.py`
- Create: `backend/app/mcp/transports/stdio.py`
- Create: `backend/app/mcp/transports/http.py`
- Test: `backend/tests/unit/test_mcp_transports.py`

**Interfaces:**

- Produces: `BaseTransport`, `StdioTransport`, `StreamableHttpTransport`, `MCPTransportError`, `MCPProcessCrashedError`.
- Consumes: Python `asyncio.subprocess`, `httpx.AsyncClient`.

- [ ] **Step 1: Write unit tests for stdio JSON-RPC 2.0 message framing, stderr separation, request multiplexing, and Streamable HTTP requests**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement `BaseTransport`, `StdioTransport` (with non-root environment scrubbing), and `StreamableHttpTransport`**
- [ ] **Step 4: Run tests to verify they pass**

---

### Task 5: Process Supervisor & Client Manager (Phase 08)

**Files:**

- Create: `backend/app/mcp/supervisor.py`
- Create: `backend/app/mcp/client_manager.py`
- Test: `backend/tests/unit/test_mcp_supervisor.py`
- Test: `backend/tests/integration/test_mcp_client_manager.py`

**Interfaces:**

- Produces: `ProcessSupervisor`, `ServerHealthStatus` (`HEALTHY`, `DEGRADED`, `DEAD`), `MCPClientManager`.
- Consumes: `ToolPermissionRegistry`, `normalizer`, `transports`, `limiter`, `sanitizer`.

- [ ] **Step 1: Write tests for 15s ping heartbeat, 3-strike degradation, exponential backoff restart (`1s, 2s, 4s, 8s`), 5s SIGKILL timeout, and client manager tool dispatch**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement `ProcessSupervisor` with state machine and auto-respawn**
- [ ] **Step 4: Implement `MCPClientManager` with connection pooling, pre-execution authorization check, and output wrapping**
- [ ] **Step 5: Run tests to verify they pass**

---

### Task 6: Core MCP Connectors (Phase 08)

**Files:**

- Create: `backend/app/mcp/connectors/__init__.py`
- Create: `backend/app/mcp/connectors/filesystem.py`
- Create: `backend/app/mcp/connectors/git.py`
- Create: `backend/app/mcp/connectors/github.py`
- Create: brave connector (removed 2026-11-09)
- Create: `backend/app/mcp/connectors/notion.py`
- Test: `backend/tests/integration/test_core_connectors.py`

**Interfaces:**

- Produces: Connector classes wrapping underlying MCP server tool calls with sandboxing and validation.
- Consumes: `MCPClientManager`, `ToolPermissionRegistry`.

- [ ] **Step 1: Write integration tests for path traversal blocking in filesystem connector, local git commit/status, gated PR creation, Brave citations, and Notion read-only enforcement**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement `filesystem.py` (confined strictly to `/artifacts/{workspace_id}`), `git.py`, `github.py`, `brave.py`, and `notion.py`**
- [ ] **Step 4: Run tests to verify they pass**

---

### Task 7: In-House Google Adapter (Gmail & Calendar) (Phase 08)

**Files:**

- Create: `backend/mcp_servers/google_adapter/__init__.py`
- Create: `backend/mcp_servers/google_adapter/auth.py`
- Create: `backend/mcp_servers/google_adapter/gmail.py`
- Create: `backend/mcp_servers/google_adapter/calendar.py`
- Create: `backend/mcp_servers/google_adapter/server.py`
- Test: `backend/tests/integration/test_google_adapter.py`

**Interfaces:**

- Produces: Google Adapter FastMCP/Starlette Streamable HTTP server exposing Gmail and Calendar tools.
- Consumes: `google-api-python-client`, `google-auth`.

- [ ] **Step 1: Write integration tests for Gmail search/read/draft/send (with approval check) and Calendar events with mock responders**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement `auth.py`, `gmail.py`, `calendar.py`, and `server.py`**
- [ ] **Step 4: Run tests to verify they pass**

---

### Task 8: MCP Administration API & Router (Phase 08)

**Files:**

- Create: `backend/app/schemas/mcp.py`
- Create: `backend/app/api/v1/mcp_admin.py`
- Modify: `backend/app/api/v1/router.py`
- Test: `backend/tests/integration/test_mcp_admin_api.py`

**Interfaces:**

- Produces: `GET /api/v1/mcp/servers`, `POST /api/v1/mcp/servers`, `GET /api/v1/mcp/servers/{id}/health`, `DELETE /api/v1/mcp/servers/{id}`.
- Consumes: `McpServerRepository`, `MCPClientManager`, `ProcessSupervisor`.

- [ ] **Step 1: Write tests for manifest server registration, schema-hash storage, health inspection, and tenant isolation**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement `schemas/mcp.py`, `api/v1/mcp_admin.py`, and wire into `api/v1/router.py`**
- [ ] **Step 4: Run tests to verify they pass**

---

### Task 9: Fixed-Function Sandbox Runner (Decision D14) (Phase 09)

**Files:**

- Create: `backend/app/sandbox/__init__.py`
- Create: `backend/app/sandbox/schemas.py`
- Create: `backend/app/sandbox/runner.py`
- Create: `docker/sandbox/Dockerfile`
- Test: `backend/tests/integration/test_sandbox_runner.py`

**Interfaces:**

- Produces: `SandboxRunner`, `SandboxCommandRequest`, `SandboxExecutionResult`.
- Consumes: Isolated Docker API or local process sandboxing (banning `/var/run/docker.sock`).

- [ ] **Step 1: Write tests asserting non-root execution (UID 10001), 60s timeout, memory bounds, network isolation, and socket ban**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement `schemas.py`, `runner.py`, and `docker/sandbox/Dockerfile`**
- [ ] **Step 4: Run tests to verify they pass**

---

### Task 10: Base Specialist Agent Framework (Phase 09)

**Files:**

- Create: `backend/app/agents/base.py`
- Create: `backend/app/agents/schemas.py`
- Test: `backend/tests/unit/test_base_agent.py`

**Interfaces:**

- Produces: `BaseSpecialistAgent` abstract base class, `SpecialistAgentResult`, `UnauthorizedToolError`, `PermissionCeilingExceededError`.
- Consumes: `ModelGateway`, `ToolPermissionRegistry`, `SubtaskJob`.

- [ ] **Step 1: Write tests for tool allowlist enforcement and permission ceiling checks**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement `base.py` and `schemas.py`**
- [ ] **Step 4: Run tests to verify they pass**

---

### Task 11: Specialist Agents Implementation (Phase 09)

**Files:**

- Create: `backend/app/agents/specialized/__init__.py`
- Create: `backend/app/agents/specialized/research.py`
- Create: `backend/app/agents/specialized/coding.py`
- Create: `backend/app/agents/specialized/document.py`
- Create: `backend/app/agents/specialized/email.py`
- Create: `backend/app/agents/specialized/pm.py`
- Test: `backend/tests/integration/test_specialist_agents.py`

**Interfaces:**

- Produces: `ResearchAgent`, `CodingAgent`, `DocumentAgent`, `EmailAgent`, `PMAgent`.
- Consumes: `BaseSpecialistAgent`, `MCPClientManager`, `SandboxRunner`, `ModelGateway`.

- [ ] **Step 1: Write integration tests for all 5 specialist agents with verification proof assertions**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement `research.py`, `coding.py`, `document.py`, `email.py`, and `pm.py`**
- [ ] **Step 4: Run tests to verify they pass**

---

### Task 12: Agent Fleet Dispatcher & SubtaskWorker Integration (Phase 09)

**Files:**

- Create: `backend/app/agents/registry.py`
- Modify: `backend/app/tasks/worker.py`
- Test: `backend/tests/integration/test_agent_workflows_e2e.py`

**Interfaces:**

- Produces: `AgentFleetRegistry`, end-to-end execution of Workflow 1 (Daily Briefing) and Workflow 3 (Bug fix to PR).
- Consumes: `SubtaskWorker`, specialist agents, outbox publisher.

- [ ] **Step 1: Write end-to-end tests for Workflow 1 and Workflow 3 through `SubtaskWorker`**
- [ ] **Step 2: Run test to verify it fails**
- [ ] **Step 3: Implement `AgentFleetRegistry` and wire into `SubtaskWorker.execute_job()`**
- [ ] **Step 4: Run tests to verify they pass**

---

### Task 13: Frontend Integration (Phases 08 & 09)

**Files:**

- Create: `apps/web/src/lib/mcp-client.ts`
- Create: McpStatusIndicator.tsx (removed 2026-11-09 with the UI redesign: no importer)
- Create: `apps/web/src/components/approvals/GitDiffViewer.tsx`
- Create: `apps/web/src/components/approvals/EmailApprovalCard.tsx`
- Create: ArtifactCard.tsx (removed 2026-11-09 with the UI redesign: no importer)
- Create: ResearchCitationsView.tsx (removed 2026-11-09 with the UI redesign: no importer)
- Modify: ApprovalCenterModal (removed; approvals are now inline on the task card)
- Modify: WorkPanel.tsx (removed 2026-11-10; its task progress now lives in the chat TaskCard)
- Modify: `apps/web/src/components/chat/ConversationView.tsx`
- Create: McpStatusIndicator.test.tsx (removed 2026-11-09 with the UI redesign: no importer) (removed 2026-11-09 with the UI redesign: the component had no importer)
- Test: `apps/web/src/components/approvals/GitDiffViewer.test.tsx`

**Interfaces:**

- Produces: UI components for MCP status, Git diff viewer, email preview card, artifact download, and research citations.
- Consumes: `approvals-client.ts`, `tasks-client.ts`, `mcp-client.ts`.

- [ ] **Step 1: Write frontend unit tests for `McpStatusIndicator` and `GitDiffViewer`**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement frontend components and integrate into `ChatWorkspace`, `ApprovalCenterModal`, and `TaskCenterDrawer`**
- [ ] **Step 4: Run `npm --prefix apps/web test` and verify all 852+ tests pass with zero lint errors**

---

### Task 14: Monorepo Full Regression & Independent Certification

**Files:**

- Create: `PRODUCTION_READINESS_REPORT_PHASES_08_09.md`
- Modify: `docs/implementation/IMPLEMENTATION_STATUS.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Execute full backend test suite (`pytest`) against PostgreSQL 17 & Redis 7.4**
- [ ] **Step 2: Execute frontend test suite (`npm --prefix apps/web test`)**
- [ ] **Step 3: Execute governance suite (`npm test`) and fast validation (`npm run validate:fast`)**
- [ ] **Step 4: Run independent verification subagents to audit implementation**
- [ ] **Step 5: Author `PRODUCTION_READINESS_REPORT_PHASES_08_09.md` and update documentation**
