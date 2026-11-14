# API: Model Context Protocol (MCP) Server Administration

- **Base path:** `/api/v1/mcp`
- **Authentication:** `Authorization: Bearer <access token>` (authenticated user in tenant workspace)
- **Status:** Implemented locally.

Bodies are JSON with unknown fields rejected. All endpoints are scoped to the authenticated tenant's workspace via row-level security and explicit workspace filtering.

## Built-in GitHub repository tools (read-only)

The built-in `maya-tools` server provides six READ-tier tools for reading a GitHub repository. They are called by tasks through the gateway, and offered directly to chat (when the message names a github.com repo URL or the user has a GitHub connection) and to voice. They use the calling user's own token (Settings > Connections); public repositories work without one (unauthenticated, 60 requests per hour).

| Tool                        | Arguments                                      | Returns                                                                                                                                     |
| :-------------------------- | :--------------------------------------------- | :------------------------------------------------------------------------------------------------------------------------------------------ |
| `github_get_repo`           | `repo`                                         | description, default branch, languages, size, stars, license, last push                                                                     |
| `github_list_tree`          | `repo`, `path?`, `branch?`, `max_depth?` (1-6) | files and folders (max 400 entries, noise folders skipped), suggested key files, truncation notice                                          |
| `github_read_file`          | `repo`, `path`, `branch?`, `max_chars?`        | decoded text (cap 20k default, 40k max; source file cap 256 KB); binary or oversized files are refused; secrets redacted with a warning    |
| `github_list_commits`       | `repo`, `branch?`, `path?`, `limit?`           | sha, date, author, first message line                                                                                                       |
| `github_list_pull_requests` | `repo`, `state?`, `limit?`                     | number, title, author, branches                                                                                                             |
| `github_list_issues`        | `repo`, `state?`, `limit?`                     | number, title, author, labels (pull requests excluded)                                                                                      |

`repo` is `owner/name` or a github.com URL; any other host is rejected. Output is wrapped between `<<<REPO_DATA>>>` and `<<<END_REPO_DATA>>>` and is untrusted data. Errors are precise and secret-free: 404 (private or no access), 401 (token rejected), 403 (rate limit with reset time, or permission), 409 (empty repository).

## GET /api/v1/mcp/servers

Lists all registered MCP servers within the tenant's workspace.

### Response `200 OK`

```json
[
  {
    "id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
    "workspace_id": "7ca85f64-5717-4562-b3fc-2c963f66afa1",
    "name": "google_adapter",
    "transport": "streamable_http",
    "uri": "http://127.0.0.1:8100/mcp",
    "health_status": "healthy",
    "tool_count": 5,
    "last_ping_at": "2026-11-07T04:00:00Z",
    "is_active": true,
    "created_at": "2026-11-07T03:30:00Z",
    "updated_at": "2026-11-07T04:00:00Z"
  }
]
```

## POST /api/v1/mcp/servers

Registers a new MCP server in the tenant's workspace.

### Request Body

| Field                  | Type             | Required | Description                                         |
| :--------------------- | :--------------- | :------- | :-------------------------------------------------- |
| `name`                 | string           | yes      | Normalized server identifier (`^[a-z0-9_-]{1,64}$`) |
| `transport`            | string           | yes      | `stdio` or `streamable_http`                        |
| `command`              | string           | no       | Executable path for `stdio` transport               |
| `args`                 | array of strings | no       | Command-line arguments for `stdio` transport        |
| `env`                  | object           | no       | Safe environment variables (no secret injection)    |
| `uri`                  | string           | no       | HTTP URL for `streamable_http` transport            |
| `expected_schema_hash` | string           | no       | Canonical SHA-256 schema hash for poisoning defense |

```json
{
  "name": "google_adapter",
  "transport": "streamable_http",
  "uri": "http://127.0.0.1:8100/mcp",
  "expected_schema_hash": "a1b2c3d4..."
}
```

### Response `201 Created`

Returns the created server record.

## GET /api/v1/mcp/servers/{server_id}/health

Queries the live health status of the specified server from the `ProcessSupervisor`.

### Response `200 OK`

```json
{
  "server_id": "3fa85f64-5717-4562-b3fc-2c963f66afa6",
  "status": "healthy",
  "last_ping_at": "2026-11-07T04:00:00Z",
  "consecutive_failures": 0,
  "tool_count": 5
}
```

## DELETE /api/v1/mcp/servers/{server_id}

Soft-deletes / deactivates the MCP server registration. Active connections in the pool are closed gracefully.

### Response `204 No Content`

## GET /api/v1/mcp-client/status

Read-only status of the MCP client adapter ([ADR-0028](../adr/0028-mcp-client.md)). Needs sign-in. Starts nothing and returns no secrets, arguments or tool output.

### Response `200 OK`

```json
{
  "enabled": true,
  "servers": [
    {"name": "playwright", "label": "Playwright", "builtin": true, "enabled": false,
     "configured_enabled": false, "running": false, "tool_count": 0, "pinned": false}
  ],
  "problems": []
}
```

`enabled` is the `MAYA_MCP_CLIENT_ENABLED` switch (`false` gives empty `servers`). A server's `enabled` is true only when the servers file enabled it AND pinned an exact version.
