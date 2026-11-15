/**
 * Typed client for Deliverables and Artifacts Delivery API.
 */

import { getAccessToken, refreshSession } from "./auth-session";
import { apiErrorFromResponse } from "./api-client";
import { getArtifactOpener, type OpenArtifactResult } from "./desktop-bridge";

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
  /**
   * True when the file lives in the user's connected folder rather than MAYA's own storage. MAYA
   * never deletes such a file from disk, so the UI offers no Delete for it.
   */
  readonly in_connected_folder?: boolean;
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
    throw await apiErrorFromResponse(response);
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

export async function fetchArtifactBlob(artifactId: string): Promise<Blob> {
  const response = await authenticatedFetch(
    `/api/v1/artifacts/${encodeURIComponent(artifactId)}`,
    {
      method: "GET",
    },
  );
  return await response.blob();
}

/**
 * The text of a stored Markdown or text artifact (the Notion approval card reads the book this
 * way). The server sends the file as it is; nothing is rendered here.
 */
export async function fetchArtifactText(artifactId: string): Promise<string> {
  const response = await authenticatedFetch(
    `/api/v1/artifacts/${encodeURIComponent(artifactId)}`,
    { method: "GET", headers: { Accept: "text/markdown, text/plain" } },
  );
  return await response.text();
}

/**
 * The name a saved file gets: the stored name, with the type as extension when the name has none
 * (zips included). Path separators are dropped so the browser never sees a folder.
 */
export function downloadFileName(fileName: string, fileType?: string): string {
  const base = fileName.split(/[\\/]/).pop()?.trim() || "download";
  const ext = (fileType ?? "").trim().replace(/^\./, "").toLowerCase();
  if (
    ext !== "" &&
    /^[a-z0-9]{1,10}$/.test(ext) &&
    !/\.[A-Za-z0-9]{1,10}$/.test(base)
  ) {
    return `${base}.${ext}`;
  }
  return base;
}

export async function downloadArtifact(
  artifactId: string,
  fileName: string,
  fileType?: string,
): Promise<void> {
  const blob = await fetchArtifactBlob(artifactId);
  const url = window.URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = downloadFileName(fileName, fileType);
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
  } finally {
    window.URL.revokeObjectURL(url);
  }
}

/** Deprecated alias for downloadArtifact for backward compatibility */
export const downloadArtifactFile = downloadArtifact;

/** Delete a stored artifact. The server removes MAYA's own copy only, never a file in the user's folder. */
export async function deleteArtifact(artifactId: string): Promise<void> {
  await authenticatedFetch(
    `/api/v1/artifacts/${encodeURIComponent(artifactId)}`,
    { method: "DELETE" },
  );
}

/** Most ids the server accepts in one bulk delete. */
export const MAX_BULK_DELETE = 100;

export interface BulkDeleteArtifactsResult {
  readonly deleted: readonly string[];
  readonly notFound: readonly string[];
}

/** Delete several files (in batches of 100). Ids already gone come back as `notFound`. */
export async function deleteArtifacts(
  ids: readonly string[],
): Promise<BulkDeleteArtifactsResult> {
  const deleted: string[] = [];
  const notFound: string[] = [];
  for (let start = 0; start < ids.length; start += MAX_BULK_DELETE) {
    const response = await authenticatedFetch("/api/v1/artifacts/delete", {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ ids: ids.slice(start, start + MAX_BULK_DELETE) }),
    });
    const body = (await response.json()) as {
      deleted?: string[];
      not_found?: string[];
    };
    deleted.push(...(body.deleted ?? []));
    notFound.push(...(body.not_found ?? []));
  }
  return { deleted, notFound };
}

/** True when this page runs in the desktop app and can open files with the PC's default app. */
export function canOpenNatively(): boolean {
  return getArtifactOpener() !== null;
}

/**
 * Open an artifact with the PC's default app (desktop app only). The renderer sends the artifact
 * id and the file name, never a path; the desktop shell downloads it to a MAYA temp folder and
 * opens it. Resolves `{ ok: false, reason: "no-desktop" }` in a plain browser.
 */
export async function openArtifactNatively(
  artifact: Pick<ArtifactMetadata, "id" | "file_name" | "file_type">,
): Promise<OpenArtifactResult | { ok: false; reason: "no-desktop" }> {
  const open = getArtifactOpener();
  if (open === null) return { ok: false, reason: "no-desktop" };
  let token = getAccessToken();
  if (!token) {
    const refreshed = await refreshSession();
    token = refreshed ? getAccessToken() : null;
  }
  if (!token) return { ok: false, reason: "open-failed" };
  return open({
    artifactId: artifact.id,
    fileName: downloadFileName(artifact.file_name, artifact.file_type),
    token,
  });
}

/** The artifact as a File, so it can be attached to a chat message ("Ask AI"). */
export async function artifactToFile(
  artifact: Pick<ArtifactMetadata, "id" | "file_name" | "file_type">,
): Promise<File> {
  const blob = await fetchArtifactBlob(artifact.id);
  return new File(
    [blob],
    downloadFileName(artifact.file_name, artifact.file_type),
    {
      type: blob.type,
    },
  );
}
