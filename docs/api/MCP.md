# API: Model Context Protocol (MCP) Server Administration

- **Base path:** `/api/v1/mcp`
- **Authentication:** `Authorization: Bearer <access token>` (authenticated user in tenant workspace)
- **Status:** Implemented locally.

Bodies are JSON with unknown fields rejected. All endpoints are scoped to the authenticated tenant's workspace via row-level security and explicit workspace filtering.

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
