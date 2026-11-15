# Integration: Model Context Protocol (MCP) Gateway

- **Status:** Implemented
- **Transport:** stdio (cross-platform non-blocking subprocess with Popen), streamable HTTP (JSON-RPC over httpx)
- **Server or SDK:** In-house Python 3.12 implementation (`backend/app/mcp/`) compliant with MCP Specification 2024-11-05 and Google GenAI Function Declarations

## Purpose

The MCP Gateway acts as the secure intermediary between MAYA AI specialist agents and external tools/services. It provides managed process supervision, persistent connection pooling, leaky-bucket rate limiting, GenAI tool schema normalization, schema hash poisoning defense, and strict four-tier tool permission enforcement.

## Managed Connectors & Servers

1. **Filesystem Connector (`backend/app/mcp/connectors/filesystem.py`):**
   - Strictly scoped to `/artifacts/{workspace_id}/`.
   - Prevents directory traversal (`../`), absolute paths, and symlink escapes with `SecurityTraversalError`.
   - Supports: `read_file`, `write_file`, `list_directory`, `get_file_info`.

2. **Git Connector (`backend/app/mcp/connectors/git.py`):**
   - Sandboxed local Git repository operations.
   - Read/Local operations: `git_status`, `git_diff`, `git_log`, `git_branch`, `git_commit_local` (READ/WRITE).
   - Remote operations: `git_push` (APPROVAL_REQUIRED, requires valid signed token).

3. **GitHub Connector (`backend/app/mcp/connectors/github.py`):**
   - REST API integration via httpx.
   - Read operations: `github_get_issue`, `github_search_issues` (READ).
   - Mutating operations: `github_create_pull_request` (APPROVAL_REQUIRED, requires valid signed token).

4. **Google Search Connector (`backend/app/mcp/connectors/google_search.py`):**
   - Web search through Gemini's built-in Google Search grounding; no separate search API key. Brave Search was removed on 2026-11-09.
   - Wraps results in `<<<UNTRUSTED_EXTERNAL_INPUT>>>` markers and scans them for prompt injection.
   - Extracts citations (`title`, `url`) from the grounding metadata only; an empty result raises `GoogleSearchError` instead of inventing findings.

5. **Notion Connector (`backend/app/mcp/connectors/notion.py`):**
   - Strictly read-only connector: `notion_get_page`, `notion_query_database` (READ).
   - Sanitizes block contents before returning to agent contexts.

6. **In-House Google Adapter (`backend/mcp_servers/google_adapter/`):**
   - Standalone Streamable HTTP MCP server mounting `/mcp`.
   - Gmail: `search_emails`, `read_thread` (READ); `create_draft` (autonomous WRITE); `send_email` (APPROVAL_REQUIRED).
   - Google Calendar: `list_events`, `get_event` (READ).
   - RFC 6749 OAuth 2.0 token refresh mechanism.

## Permissions

| Tool or operation                             | Permission level  | Approval needed | Token validation             |
| :-------------------------------------------- | :---------------- | :-------------- | :--------------------------- |
| `filesystem_read_file`                        | READ              | No              | None                         |
| `filesystem_write_file`                       | WRITE             | No              | Workspace sandbox scoped     |
| `filesystem_list_directory`                   | READ              | No              | None                         |
| `git_status` / `git_diff` / `git_log`         | READ              | No              | None                         |
| `git_commit_local`                            | WRITE             | No              | Local staging only           |
| `git_push`                                    | APPROVAL_REQUIRED | Yes             | Single-use JWT, CAS consumed |
| `github_get_issue` / `github_search_issues`   | READ              | No              | None                         |
| `github_create_pull_request`                  | APPROVAL_REQUIRED | Yes             | Single-use JWT, CAS consumed |
| `web_search`                                  | READ              | No              | Gemini Google Search grounding |
| `notion_get_page` / `notion_query_database`   | READ              | No              | None                         |
| `gmail_search` / `gmail_read_thread`          | READ              | No              | None                         |
| `gmail_create_draft`                          | WRITE             | No              | Saved as Gmail draft         |
| `gmail_send`                                  | APPROVAL_REQUIRED | Yes             | Single-use JWT, CAS consumed |
| `calendar_list_events` / `calendar_get_event` | READ              | No              | None                         |

## Failure Handling

| Failure                        | Detection                                                         | Response                                                                                                                |
| :----------------------------- | :---------------------------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------- |
| Server crash / process exit    | Heartbeat ping failure or exit code                               | Supervisor restarts with exponential backoff (1s, 2s, 4s, 8s). After 3 failures transitions to `DEGRADED`, then `DEAD`. |
| Rate limited (HTTP 429)        | HTTP 429 response or `Retry-After` header                         | VendorRateLimiter parses `Retry-After` (seconds or RFC 2822 date) and sleeps with jittered backoff.                     |
| Tool schema poisoning / drift  | SHA-256 canonical hash mismatch against `mcp_servers.schema_hash` | Client manager fails closed with `ToolPoisoningError` and refuses to mount modified tools.                              |
| Sandbox escape / traversal     | Path resolution outside `/artifacts/{workspace_id}`               | `SecurityTraversalError` raised immediately; request blocked.                                                           |
| Docker socket exposure attempt | Request includes `/var/run/docker.sock` or `docker_engine`        | `SecurityViolationError` raised immediately; container launch refused (Decision D14).                                   |

## Security

1. **RFC 8785 JSON Canonicalization:** Payloads for sensitive actions are canonically hashed and verified against the approval token's payload hash before execution.
2. **Untrusted Content Sanitization:** All raw tool outputs are stripped of dangerous control characters, ANSI codes, and BiDi overrides, and enclosed in `<untrusted_external_content>` tags.
3. **Reproducible Pinned Dependencies (Decision D11):** No `npx -y` or `uvx` runtime package downloads. All MCP server adapters are maintained in-house or run as pinned local executables.
4. **Tool Permission Ceiling:** Server hints can only increase a tool's permission tier (e.g., mark as `APPROVAL_REQUIRED`), never downgrade a restricted or approval-gated tool.

## Testing

Comprehensive test suites in `backend/tests/`:

- `tests/unit/test_schema_normalizer.py`: GenAI declaration mapping, RFC 8785 SHA-256 schema hash, injection sanitization.
- `tests/unit/test_mcp_limiter.py`: Token bucket rate limiting, HTTP 429 Retry-After parsing.
- `tests/unit/test_mcp_transports.py`: Stdio and Streamable HTTP transports.
- `tests/unit/test_mcp_supervisor.py`: Heartbeat ping, exponential backoff, health status transitions.
- `tests/integration/test_mcp_client_manager.py`: Tool discovery, poisoning defense, tool execution with permission checks.
- `tests/integration/test_core_connectors.py`: Filesystem traversal blocks, local Git, GitHub mocks, Google Search grounding, Notion.
- `tests/integration/test_google_adapter.py`: Gmail search/draft/send, Calendar list/events.
- `tests/integration/test_mcp_admin_api.py`: REST API for MCP server registration and health monitoring.
