/**
 * Typed API client for Multi-Workspace management (ADR-0038 / /api/v1/workspaces).
 *
 * Interacts with backend endpoints under /api/v1/workspaces:
 * - GET    /api/v1/workspaces          -> list connected project workspaces
 * - POST   /api/v1/workspaces          -> register a new project workspace directory
 * - GET    /api/v1/workspaces/{id}     -> get details of a workspace
 * - DELETE /api/v1/workspaces/{id}     -> disconnect a project workspace
 * - POST   /api/v1/workspaces/{id}/default -> switch active default workspace
 */

import { apiErrorFromResponse, NetworkError, ApiError } from "./api-client";
import { getAccessToken, refreshSession } from "./auth-session";

export interface ProjectWorkspace {
  id: string;
  name: string;
  path: string;
  is_default: boolean;
  status: "ok" | "missing" | "not_writable";
  writable: boolean;
  created_at: string;
}

const BASE = "/api/v1/workspaces";

// ---------------------------------------------------------------------------
// Plain-language errors
// ---------------------------------------------------------------------------

/** Human-readable explanation of an error from the workspaces API. */
export function describeWorkspaceError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 401:
        return "You are signed out. Sign in again and retry.";
      case 403:
        return "You do not have permission to do that.";
      case 404:
        return error.serverMessage && error.serverMessage.trim() !== ""
          ? error.serverMessage
          : "That workspace was not found.";
      case 422:
        return error.serverMessage && error.serverMessage.trim() !== ""
          ? error.serverMessage
          : "Invalid workspace directory. Check the path and try again.";
      case 429:
        return "Too many requests. Wait a moment and try again.";
      default:
        if (error.status >= 500) {
          return "The server could not do that right now. Try again in a moment.";
        }
        return error.serverMessage && error.serverMessage.trim() !== ""
          ? error.serverMessage
          : "That could not be done. Try again.";
    }
  }
  if (error instanceof NetworkError) {
    return "The connection was lost. Check your network and try again.";
  }
  return "That could not be done. Try again.";
}

// ---------------------------------------------------------------------------
// Fetch with auth
// ---------------------------------------------------------------------------

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

  let response: Response;
  try {
    response = await fetch(path, { ...init, headers, cache: "no-store" });
    if (response.status === 401) {
      const refreshed = await refreshSession();
      const retryToken = refreshed ? getAccessToken() : null;
      if (retryToken) {
        headers.set("Authorization", `Bearer ${retryToken}`);
        response = await fetch(path, { ...init, headers, cache: "no-store" });
      }
    }
  } catch (error) {
    if (
      typeof error === "object" &&
      error !== null &&
      (error as { name?: unknown }).name === "AbortError"
    ) {
      throw error;
    }
    throw new NetworkError(error);
  }

  if (!response.ok) {
    throw await apiErrorFromResponse(response);
  }
  return response;
}

// ---------------------------------------------------------------------------
// Workspaces API methods
// ---------------------------------------------------------------------------

/**
 * List all project workspaces registered for the current user.
 */
export async function listWorkspaces(): Promise<ProjectWorkspace[]> {
  const response = await authenticatedFetch(BASE, {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
  });
  const data = (await response.json()) as { workspaces?: ProjectWorkspace[] };
  return data.workspaces ?? [];
}

/**
 * Register a new project workspace directory.
 */
export async function registerWorkspace(
  path: string,
  name?: string,
): Promise<ProjectWorkspace> {
  const body: { path: string; name?: string } = { path };
  if (name !== undefined) {
    body.name = name;
  }
  const response = await authenticatedFetch(BASE, {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      Accept: "application/json",
    },
    body: JSON.stringify(body),
  });
  return (await response.json()) as ProjectWorkspace;
}

/**
 * Get details and current dynamic status of a workspace.
 */
export async function getWorkspace(id: string): Promise<ProjectWorkspace> {
  const response = await authenticatedFetch(
    `${BASE}/${encodeURIComponent(id)}`,
    {
      method: "GET",
      headers: {
        Accept: "application/json",
      },
    },
  );
  return (await response.json()) as ProjectWorkspace;
}

/**
 * Disconnect a project workspace by id.
 */
export async function disconnectWorkspace(id: string): Promise<void> {
  await authenticatedFetch(`${BASE}/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

/**
 * Set a project workspace as the active/default workspace.
 */
export async function setDefaultWorkspace(
  id: string,
): Promise<ProjectWorkspace> {
  const response = await authenticatedFetch(
    `${BASE}/${encodeURIComponent(id)}/default`,
    {
      method: "POST",
      headers: {
        Accept: "application/json",
      },
    },
  );
  return (await response.json()) as ProjectWorkspace;
}
