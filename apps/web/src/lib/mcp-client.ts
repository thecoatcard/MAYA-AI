/**
 * Typed API client for MCP Gateway Administration & Health (Phase 08).
 */

import { getAccessToken, refreshSession } from "./auth-session";
import { ApiError } from "./api-client";

export interface McpServer {
  id: string;
  workspace_id: string;
  user_id: string;
  name: string;
  transport: string;
  command?: string | null;
  args?: string[] | null;
  url?: string | null;
  pinned_version?: string | null;
  schema_hash?: string | null;
  is_active: boolean;
  health_status: "healthy" | "degraded" | "dead" | string;
  tool_count: number;
  tools_manifest?: Record<string, unknown>[] | null;
  last_ping_at?: string | null;
  consecutive_failures: number;
  created_at?: string | null;
  updated_at?: string | null;
}

export interface McpServerHealth {
  server_id: string;
  name: string;
  health_status: "healthy" | "degraded" | "dead" | string;
  last_ping_at?: string | null;
  consecutive_failures: number;
  tool_count: number;
}

export interface McpServerCreate {
  name: string;
  transport: "stdio" | "http" | "streamable_http";
  command?: string;
  args?: string[];
  url?: string;
  pinned_version?: string;
  schema_hash?: string;
  tools_manifest?: Record<string, unknown>[];
}

async function authenticatedFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  let token = getAccessToken();
  if (!token) {
    const refreshed = await refreshSession();
    token = refreshed ? getAccessToken() : null;
  }

  const headers = new Headers(init?.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  let response = await fetch(path, { ...init, headers });

  if (response.status === 401) {
    const refreshed = await refreshSession();
    if (refreshed) {
      const retryToken = getAccessToken();
      if (retryToken) {
        headers.set("Authorization", `Bearer ${retryToken}`);
        response = await fetch(path, { ...init, headers });
      }
    }
  }

  if (!response.ok) {
    let errorCode = "unknown_error";
    try {
      const body = (await response.json()) as {
        error?: string;
        detail?: string;
      };
      if (body?.error) {
        errorCode = body.error;
      } else if (body?.detail) {
        errorCode = body.detail;
      }
    } catch {
      // not JSON
    }
    throw new ApiError(response.status, errorCode);
  }

  return response;
}

export async function listMcpServers(isActive?: boolean): Promise<McpServer[]> {
  const query = isActive !== undefined ? `?is_active=${isActive}` : "";
  const res = await authenticatedFetch(`/api/v1/mcp/servers${query}`, {
    headers: {
      Accept: "application/json",
    },
  });

  return (await res.json()) as McpServer[];
}

export async function getMcpServerHealth(
  serverId: string,
): Promise<McpServerHealth> {
  const res = await authenticatedFetch(
    `/api/v1/mcp/servers/${encodeURIComponent(serverId)}/health`,
    {
      headers: {
        Accept: "application/json",
      },
    },
  );

  return (await res.json()) as McpServerHealth;
}

export async function registerMcpServer(
  payload: McpServerCreate,
): Promise<McpServer> {
  const res = await authenticatedFetch("/api/v1/mcp/servers", {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(payload),
  });

  return (await res.json()) as McpServer;
}

export async function deleteMcpServer(serverId: string): Promise<void> {
  await authenticatedFetch(
    `/api/v1/mcp/servers/${encodeURIComponent(serverId)}`,
    {
      method: "DELETE",
    },
  );
}
