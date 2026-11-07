/**
 * Typed client for Deliverables and Artifacts Delivery API.
 */

import { getAccessToken, refreshSession } from "./auth-session";
import { ApiError } from "./api-client";

export interface ArtifactMetadata {
  readonly id: string;
  readonly workspace_id: string;
  readonly task_id: string;
  readonly user_id: string;
  readonly file_name: string;
  readonly file_type: string;
  readonly size_bytes: number;
  readonly sha256: string;
  readonly verification_status: "verified" | "failed" | "unverified" | string;
  readonly verification_report: Record<string, unknown> | null;
  readonly created_at: string;
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

export async function listArtifacts(
  taskId?: string,
): Promise<ArtifactMetadata[]> {
  const query = taskId ? `?task_id=${encodeURIComponent(taskId)}` : "";
  const response = await authenticatedFetch(`/api/v1/artifacts${query}`, {
    method: "GET",
    headers: {
      Accept: "application/json",
    },
  });

  return (await response.json()) as ArtifactMetadata[];
}

export async function getArtifactMetadata(
  artifactId: string,
): Promise<ArtifactMetadata> {
  const response = await authenticatedFetch(
    `/api/v1/artifacts/${encodeURIComponent(artifactId)}/metadata`,
    {
      method: "GET",
      headers: {
        Accept: "application/json",
      },
    },
  );

  return (await response.json()) as ArtifactMetadata;
}

export function getArtifactDownloadUrl(
  artifactId: string,
  inline: boolean = false,
): string {
  const token = getAccessToken();
  const base = `/api/v1/artifacts/${encodeURIComponent(artifactId)}`;
  const params = new URLSearchParams();
  if (token) {
    params.set("token", token);
  }
  if (inline) {
    params.set("inline", "true");
  }
  const qs = params.toString();
  return qs ? `${base}?${qs}` : base;
}

export async function downloadArtifactFile(
  artifactId: string,
  fileName: string,
): Promise<void> {
  const response = await authenticatedFetch(
    `/api/v1/artifacts/${encodeURIComponent(artifactId)}`,
    {
      method: "GET",
    },
  );

  if (!response.ok) {
    throw new ApiError(response.status, "download_failed");
  }

  const blob = await response.blob();
  const url = window.URL.createObjectURL(blob);
  const anchor = document.createElement("a");
  anchor.href = url;
  anchor.download = fileName;
  document.body.appendChild(anchor);
  anchor.click();
  document.body.removeChild(anchor);
  window.URL.revokeObjectURL(url);
}
