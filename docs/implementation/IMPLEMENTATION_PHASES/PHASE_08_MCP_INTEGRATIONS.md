# Phase 08 — MCP Gateway and Integrations

## A. Phase Overview

- **Phase Number:** Phase 08
- **Phase Name:** MCP Gateway and Integrations
- **Objective:** Build the central Model Context Protocol (MCP) Gateway to manage verified MCP server processes, normalize tool schemas into Gemini `function_declarations`, enforce deterministic connection pooling, implement process supervision with heartbeat ping checks, protect against tool-description poisoning via schema-hash verification, and integrate the MVP server catalogue (Filesystem, Git, GitHub, Brave Search, Notion, and in-house Google adapter).
- **Why This Phase is Necessary:** As established in [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §9 and Decision D11, LLMs cannot speak JSON-RPC 2.0 or open stdio pipes directly. Furthermore, dynamic runtime package downloads (`npx -y` / `uvx`) introduce severe supply-chain vulnerabilities, and third-party tool descriptions can harbor indirect prompt injection. The MCP Gateway provides a hardened, supervised, and audited abstraction layer between the model and external tools.
- **Expected Outcome:** A production-grade MCP client pool supporting stdio subprocesses and Streamable HTTP; schema normalizer mapping JSON-RPC to Gemini function declarations; process supervisor with 15s heartbeats; schema-hash drift detection; and integrated connectors for Filesystem, Git, GitHub, Brave Search, Notion (read-only), and the in-house Google adapter.
- **Priority:** Critical
- **Estimated Complexity:** High
- **Estimated Duration:** 15 business days (Weeks 7–9 of master schedule).

---

## B. Prerequisites

- **Required Completed Phases:** Phase 02 (Authentication, Users, Workspaces, and Database Security) and Phase 07 (Security, Permissions, and Human Approval).
- **Required Infrastructure:** Running PostgreSQL 17 with `mcp_servers` table; Redis 7.4.
- **Required Credentials or External Services:** GitHub Personal Access Token or App Key; Brave Search API Key; Google Cloud OAuth 2.0 client credentials; Notion API Key.
- **Required Architectural Decisions:** [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) Decision D11 (Pinned servers, baked images, no runtime npx/uvx), Decision D4 (Deny-by-default permission gate), and Decision D14 (Docker socket never exposed).
- **Required Documentation:** [`docs/architecture/SYSTEM_OVERVIEW.md`](../../architecture/SYSTEM_OVERVIEW.md), docs/integrations/README.md (planned artifact).

---

## C. Scope

### Included Features

- MCP Client Manager maintaining connection pools for:
  - Subprocess `stdio` transports.
  - Streamable HTTP transports.
- Dynamic tool discovery and schema normalization:
  - Convert MCP JSON Schema tools into Google GenAI `function_declarations`.
  - Filter tools against workspace-level permission configurations.
- Supply-chain and poisoning protections:
  - Store SHA-256 schema hash in `mcp_servers.schema_hash`.
  - Reject startup and trigger security alert if third-party server changes tool descriptions unexpectedly.
  - Require pinned package versions baked into container images; disallow runtime package downloads.
- Process Supervisor:
  - JSON-RPC `ping` every 15 seconds.
  - Mark server `DEGRADED` after 3 consecutive missed pings.
  - Auto-restart crashed stdio subprocesses with exponential backoff (1s, 2s, 4s, 8s).
  - Provide fallback routing (e.g. GitHub MCP down -> local Git MCP; Brave Search down -> Gemini search grounding).
- Rate Limiting:
  - Leaky-bucket rate limiter per vendor/service to prevent 429 quota exhaustion.
- Core MVP Server Integrations:
  - Filesystem (`@modelcontextprotocol/server-filesystem`): Scoped strictly to `/artifacts`.
  - Local Git (`mcp-server-git`): Read logs/status; local commit; push gated.
  - GitHub (`github/github-mcp-server`): Repo analysis, issues, gated PR creation.
  - Web Search (`@brave/brave-search-mcp-server`): External search querying.
  - Notion (`@notionhq/notion-mcp-server`): Read-only in MVP.
  - In-house Google adapter (`backend/mcp_servers/google_adapter/`): Streamable HTTP adapter for Gmail, Calendar, and Drive with least-privilege OAuth scopes.

### Excluded Features

- Full Playwright browser automation container (deferred to Phase 13).
- Slack, Linear, Jira, and enterprise databases (post-MVP).
- Direct host Docker socket mounting (strictly banned by Decision D14).

### Deferred Features

- Dynamic runtime installation of unapproved community MCP servers.

### Dependencies on Future Phases

- Phase 09 (Specialist Agents) maps specific agent roles to tools exposed by this gateway.
- Phase 10 (File Processing) and Phase 11 (Memory) interact with filesystem and knowledge sources through this gateway.

---

## D. Implementation Breakdown

### Task P08-T001: MCP Process Supervisor and Connection Pool

- **Task ID:** P08-T001
- **Task Name:** Build Process Manager for Stdio and Streamable HTTP Transports
- **Detailed Description:** Implement `ClientManager` in backend/app/mcp/client_manager.py (planned artifact) using `mcp` Python SDK (pinned to 2.3.0). Manage subprocess pools for stdio servers, passing credentials via sanitized environment variables from the AES-256-GCM vault. Implement background supervisor running JSON-RPC `ping` every 15s. On 3 timeouts, flag server `DEGRADED` and attempt restart with backoff.
- **Expected Files:** backend/app/mcp/client_manager.py (planned artifact), backend/app/mcp/supervisor.py (planned artifact), backend/tests/integration/test_mcp_client_manager.py (planned artifact).
- **Dependencies:** Phase 02, Phase 07.
- **Assigned Agent Role:** Distributed Systems Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_mcp_client_manager.py` (server start, crash respawn, health ping timeout).
- **Documentation Updates:** docs/integrations/MCP_SUPERVISOR.md (planned artifact).
- **Acceptance Criteria:** Subprocesses start with non-root privileges; crash triggers backoff restart; hung processes are terminated via SIGKILL after 10s grace period.

### Task P08-T002: Schema Normalizer and Poisoning Detector

- **Task ID:** P08-T002
- **Task Name:** Implement Schema Normalizer with Schema-Hash Drift Detection
- **Detailed Description:** Implement backend/app/mcp/normalizer.py (planned artifact). Fetch tools via `list_tools()`; compute SHA-256 hash of the canonical serialized tool list; compare against `mcp_servers.schema_hash`. If hash differs, log security incident and disable server until administrator re-approves. Translate MCP tool parameters into Gemini `function_declarations` types (`STRING`, `INTEGER`, `BOOLEAN`, `OBJECT`, `ARRAY`).
- **Expected Files:** backend/app/mcp/normalizer.py (planned artifact), backend/tests/unit/test_schema_normalizer.py (planned artifact).
- **Dependencies:** P08-T001.
- **Assigned Agent Role:** AI Security Engineer.
- **Required Tests:** `pytest backend/tests/unit/test_schema_normalizer.py` (type mappings, schema drift detection, unexpected description changes).
- **Documentation Updates:** docs/security/MCP_HARDENING.md (planned artifact).
- **Acceptance Criteria:** MCP schemas convert cleanly to Gemini declarations; any alteration in tool descriptions triggers immediate security alert and disables the server.

### Task P08-T003: Core Local Tool Integrations (Filesystem and Git)

- **Task ID:** P08-T003
- **Task Name:** Integrate Filesystem and Local Git MCP Servers
- **Detailed Description:** Package `@modelcontextprotocol/server-filesystem` and `mcp-server-git` into the base container image. Configure filesystem server root strictly constrained to the workspace `/artifacts` path. Configure `mcp-server-git` to execute inside sandboxed repository checkouts. Map permission tiers: Filesystem read = READ, write in `/artifacts` = WRITE, user file write = APPROVAL_REQUIRED, delete = RESTRICTED.
- **Expected Files:** backend/app/mcp/servers/filesystem.py (planned artifact), backend/app/mcp/servers/git.py (planned artifact), backend/tests/integration/test_fs_git_mcp.py (planned artifact).
- **Dependencies:** P08-T002, Phase 07.
- **Assigned Agent Role:** Systems Integration Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_fs_git_mcp.py`.
- **Documentation Updates:** docs/integrations/CORE_TOOLS.md (planned artifact).
- **Acceptance Criteria:** Directory traversal outside `/artifacts` is blocked at the filesystem boundary; local git status and commit succeed; push is blocked pending approval token.

### Task P08-T004: Remote Service Integrations (GitHub, Brave Search, Notion)

- **Task ID:** P08-T004
- **Task Name:** Integrate GitHub, Brave Search, and Notion MCP Connectors
- **Detailed Description:** Integrate `github/github-mcp-server` (read = READ, branch/PR = APPROVAL_REQUIRED), `@brave/brave-search-mcp-server` (READ), and `@notionhq/notion-mcp-server` (strictly READ for MVP). Inject API tokens via `SecretsVault`. Implement leaky-bucket rate limiter per vendor. Implement fallback to local Git if GitHub MCP crashes.
- **Expected Files:** backend/app/mcp/servers/github.py (planned artifact), backend/app/mcp/servers/brave.py (planned artifact), backend/app/mcp/servers/notion.py (planned artifact), backend/tests/integration/test_remote_mcp.py (planned artifact).
- **Dependencies:** P08-T002, Phase 07.
- **Assigned Agent Role:** API Integration Specialist.
- **Required Tests:** `pytest backend/tests/integration/test_remote_mcp.py` (with mock HTTP responders).
- **Documentation Updates:** docs/integrations/SAAS_CONNECTORS.md (planned artifact).
- **Acceptance Criteria:** GitHub search and branch creation function; Brave search returns web snippets; Notion reads workspace pages; rate limiter throttles burst requests.

### Task P08-T005: In-House Google Adapter (Gmail, Calendar, Drive)

- **Task ID:** P08-T005
- **Task Name:** Build Streamable HTTP In-House Google Adapter
- **Detailed Description:** Create `backend/mcp_servers/google_adapter/` as an in-house Streamable HTTP MCP server communicating with Google APIs via official `google-api-python-client`. Implement OAuth 2.0 token management with automatic refresh. Implement tools:
  - `gmail_search`, `gmail_read_thread`: READ
  - `gmail_create_draft`: WRITE
  - `gmail_send`: APPROVAL_REQUIRED (irreversible action)
  - `calendar_list_events`, `calendar_get_event`: READ
- **Expected Files:** backend/mcp_servers/google_adapter/server.py (planned artifact), backend/mcp_servers/google_adapter/auth.py (planned artifact), backend/tests/integration/test_google_adapter.py (planned artifact).
- **Dependencies:** P08-T002, Phase 07.
- **Assigned Agent Role:** Senior Backend & Integration Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_google_adapter.py`.
- **Documentation Updates:** docs/integrations/GOOGLE_ADAPTER.md (planned artifact).
- **Acceptance Criteria:** Least-privilege OAuth scopes used; email drafting succeeds autonomously; sending email is strictly gated; token refresh executes without user re-authentication.

### Task P08-T006: MCP Server Administration API

- **Task ID:** P08-T006
- **Task Name:** Implement Server Registration and Status Endpoints
- **Detailed Description:** Implement administrative endpoints in backend/app/api/v1/mcp_admin.py (planned artifact):
  - `GET /api/v1/mcp/servers`: List connected servers, transport types, tool counts, and health statuses.
  - `POST /api/v1/mcp/servers`: Register new server; perform handshake, compute and store initial schema hash, encrypt environment secrets in database.
  - `DELETE /api/v1/mcp/servers/{id}`: Terminate server processes and delete configuration.
- **Expected Files:** backend/app/api/v1/mcp_admin.py (planned artifact), backend/app/schemas/mcp.py (planned artifact).
- **Dependencies:** P08-T001, P08-T002.
- **Assigned Agent Role:** API Developer.
- **Required Tests:** `pytest backend/tests/integration/test_mcp_admin_api.py`.
- **Documentation Updates:** docs/api/MCP_SPECIFICATION.md (planned artifact).
- **Acceptance Criteria:** Admins can view health and tools for all active servers; registering an invalid server returns handshake error; secrets are encrypted prior to database insertion.

---

## E. File and Folder Changes

- **Files to Create:**
  - backend/app/mcp/client_manager.py (planned artifact)
  - backend/app/mcp/supervisor.py (planned artifact)
  - backend/app/mcp/normalizer.py (planned artifact)
  - backend/app/mcp/servers/__init__.py (planned artifact)
  - backend/app/mcp/servers/filesystem.py (planned artifact)
  - backend/app/mcp/servers/git.py (planned artifact)
  - backend/app/mcp/servers/github.py (planned artifact)
  - backend/app/mcp/servers/brave.py (planned artifact)
  - backend/app/mcp/servers/notion.py (planned artifact)
  - backend/mcp_servers/google_adapter/server.py (planned artifact)
  - backend/mcp_servers/google_adapter/auth.py (planned artifact)
  - backend/app/api/v1/mcp_admin.py (planned artifact)
  - backend/app/schemas/mcp.py (planned artifact)
  - backend/tests/unit/test_schema_normalizer.py (planned artifact)
  - backend/tests/integration/test_mcp_client_manager.py (planned artifact)
  - backend/tests/integration/test_fs_git_mcp.py (planned artifact)
  - backend/tests/integration/test_remote_mcp.py (planned artifact)
  - backend/tests/integration/test_google_adapter.py (planned artifact)
  - backend/tests/integration/test_mcp_admin_api.py (planned artifact)
  - docs/integrations/MCP_SUPERVISOR.md (planned artifact)
  - docs/integrations/GOOGLE_ADAPTER.md (planned artifact)
  - docs/security/MCP_HARDENING.md (planned artifact)
- **Files to Modify:**
  - backend/app/api/v1/router.py (planned artifact)
  - `backend/app/main.py`
- **Files to Avoid Modifying:**
  - `MAYA_AI_FINAL_PLAN.md`
- **Relevant Directories:** `backend/app/mcp/`, `backend/mcp_servers/`, `docs/integrations/`.

---

## F. API and Data Contracts

### 1. Tool Declaration Contract (Gemini Function Declaration Schema)

```json
{
  "name": "gmail_create_draft",
  "description": "Create a draft email in the user's Gmail mailbox.",
  "parameters": {
    "type": "OBJECT",
    "properties": {
      "recipient": {
        "type": "STRING",
        "description": "Destination email address"
      },
      "subject": { "type": "STRING", "description": "Email subject line" },
      "body": {
        "type": "STRING",
        "description": "Email body content in plain text or HTML"
      }
    },
    "required": ["recipient", "subject", "body"]
  }
}
```

### 2. MCP Server Registration Contract

- **`POST /api/v1/mcp/servers`**
  - Request:
    ```json
    {
      "name": "notion",
      "transport": "stdio",
      "command": "notion-mcp-server",
      "args": ["--port", "8080"],
      "env_vars": { "NOTION_API_KEY": "dummy-notion-token-example" },
      "pinned_version": "v2.6.1"
    }
    ```
  - Response (`201 Created`):
    ```json
    {
      "server_id": "4b75249a-4c28-4f11-9a99-b1d5c219602e",
      "name": "notion",
      "status": "healthy",
      "tool_count": 6,
      "schema_hash": "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    }
    ```

---

## G. Security Requirements

- **No Runtime Code Fetching:** Strictly disallow `npx -y`, `uvx`, or dynamic curl/pip operations. All server binaries must be compiled and pinned inside the container image.
- **Process Isolation:** Stdio subprocesses run as non-root user `maya_runner` with minimal Linux capabilities and no access to host Docker socket.
- **Credential Hygiene:** API tokens and OAuth secrets are injected only into the process environment; never returned in client API responses or serialized to LLM prompts.
- **Egress Restrictions:** Stdio processes run with default-deny outbound network access, with whitelist exceptions only for verified SaaS endpoints (GitHub, Google, Notion, Brave).

---

## H. Testing Strategy

- **Crash Recovery & Supervisor Tests:** Send SIGKILL to running `mcp-server-git` subprocess; verify supervisor detects dead process, restarts within backoff window, and preserves connection pool.
- **Poisoning Defense Tests:** Modify a mock server's tool description string; verify gateway rejects the server startup with `SchemaDriftDetectedError`.
- **E2E Tool Execution Tests:** Execute end-to-end multi-tool flow: search Brave -> clone git repo -> read file -> commit change, asserting all actions record in `audit_log`.

---

## I. Definition of Done

1. All 6 MVP MCP servers connect, handshake, and expose tools cleanly.
2. Tools convert accurately into Gemini function declarations.
3. Schema hashes are persisted, and description drift triggers automated disabling.
4. Supervisor ping loop maintains connection health and auto-restarts failed processes.
5. In-house Google adapter successfully searches and drafts emails via OAuth 2.0.
6. Documentation in `docs/integrations/` is complete.

---

## J. Phase Exit Gate

- **Demonstrated Proof:** `pytest backend/tests/integration/test_mcp_client_manager.py` and `pytest backend/tests/integration/test_google_adapter.py` pass cleanly; at least 3 distinct MCP servers successfully invoked in an end-to-end integration test.
- **Dependent Phase Unlocked:** Phase 09 (Specialist AI Agents) is cleared to begin.
