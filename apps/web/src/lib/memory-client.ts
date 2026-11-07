/**
 * Typed client for Memory and Knowledge Retrieval REST API (Phase 11).
 */

import { ApiError } from "./api-client";
import { getAccessToken, refreshSession } from "./auth-session";

export interface MemoryItem {
  readonly id: string;
  readonly workspace_id: string;
  readonly user_id: string;
  readonly category:
    "preference" | "project" | "episodic" | "knowledge" | string;
  readonly content: string;
  readonly metadata: Record<string, unknown>;
  readonly permanent: boolean;
  readonly access_count: number;
  readonly last_accessed_at: string | null;
  readonly expires_at: string | null;
  readonly created_at: string;
}

export interface MemoryCreateRequest {
  readonly category:
    "preference" | "project" | "episodic" | "knowledge" | string;
  readonly content: string;
  readonly metadata?: Record<string, unknown>;
  readonly permanent?: boolean;
}

export interface MemorySearchRequest {
  readonly query: string;
  readonly categories?: readonly string[];
  readonly limit?: number;
  readonly raw_threshold?: number;
}

export interface MemorySearchResult {
  readonly id: string;
  readonly category: string;
  readonly content: string;
  readonly metadata: Record<string, unknown>;
  readonly raw_similarity: number;
  readonly effective_score: number;
  readonly source_id: string | null;
  readonly created_at: string;
}

export interface MemorySearchResponse {
  readonly query: string;
  readonly results: readonly MemorySearchResult[];
  readonly total_results: number;
}

export interface MemoryPurgeRequest {
  readonly scope?: "all" | "working" | "episodic";
}

export interface MemoryPurgeResponse {
  readonly status: string;
  readonly scope: string;
  readonly records_deleted: number;
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

export async function listMemoryItems(
  category?: string,
  limit: number = 50,
  offset: number = 0,
): Promise<MemoryItem[]> {
  const params = new URLSearchParams();
  if (category && category !== "all") {
    params.set("category", category);
  }
  params.set("limit", String(limit));
  params.set("offset", String(offset));

  const response = await authenticatedFetch(
    `/api/v1/memory/items?${params.toString()}`,
  );
  return (await response.json()) as MemoryItem[];
}

export async function createMemoryItem(
  payload: MemoryCreateRequest,
): Promise<MemoryItem[]> {
  const response = await authenticatedFetch("/api/v1/memory/items", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return (await response.json()) as MemoryItem[];
}

export async function searchMemory(
  payload: MemorySearchRequest,
): Promise<MemorySearchResponse> {
  const response = await authenticatedFetch("/api/v1/memory/search", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return (await response.json()) as MemorySearchResponse;
}

export async function deleteMemoryItem(itemId: string): Promise<void> {
  await authenticatedFetch(`/api/v1/memory/items/${itemId}`, {
    method: "DELETE",
  });
}

export async function purgeMemory(
  scope: "all" | "working" | "episodic" = "all",
): Promise<MemoryPurgeResponse> {
  const response = await authenticatedFetch("/api/v1/memory/purge", {
    method: "DELETE",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ scope }),
  });
  return (await response.json()) as MemoryPurgeResponse;
}
