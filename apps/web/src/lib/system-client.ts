/**
 * Typed client for System and User session queries.
 */

import { getAccessToken, refreshSession } from "./auth-session";
import { ApiError } from "./api-client";

export interface UserPrincipal {
  readonly user_id: string;
  readonly workspace_id: string;
  readonly role: string;
  /** Not sent by /auth/me today; shown in the status bar when the backend provides them. */
  readonly email?: string;
  readonly display_name?: string;
}

export interface SystemHealth {
  readonly status: "alive" | "ready" | "unavailable";
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
      const body = (await response.json()) as { error?: string };
      if (body?.error) {
        errorCode = body.error;
      }
    } catch {
      // not JSON
    }
    throw new ApiError(response.status, errorCode);
  }

  return response;
}

export async function getPrincipalMe(): Promise<UserPrincipal> {
  const response = await authenticatedFetch("/api/v1/auth/me", {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
  });

  return (await response.json()) as UserPrincipal;
}

export async function checkSystemHealth(): Promise<SystemHealth> {
  try {
    const res = await fetch("/api/v1/readyz", {
      cache: "no-store",
    });
    if (res.ok) {
      return (await res.json()) as SystemHealth;
    }
    return { status: "unavailable" };
  } catch {
    return { status: "unavailable" };
  }
}
