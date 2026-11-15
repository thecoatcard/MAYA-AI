/**
 * Typed client for the connected folder and uploaded files API (`/api/v1/files`, see
 * docs/api/FILES.md). Same auth handling as the other clients: bearer token, one refresh and
 * retry on 401, and `ApiError` for every non-2xx answer.
 */

import { ApiError, NetworkError, apiErrorFromResponse } from "./api-client";
import { getAccessToken, refreshSession } from "./auth-session";
import { downloadFileName } from "./artifacts-client";

const BASE = "/api/v1/files";

/** Limits mirrored from the API contract; the server stays the authority. */
export const MAX_UPLOAD_FILES = 10;
export const MAX_UPLOAD_FILE_BYTES = 25 * 1024 * 1024;
export const MAX_UPLOAD_TOTAL_BYTES = 60 * 1024 * 1024;

/** Extensions the API accepts (lower case, without the dot). */
export const ALLOWED_UPLOAD_EXTENSIONS: readonly string[] = [
  "txt",
  "md",
  "csv",
  "json",
  "html",
  "css",
  "js",
  "ts",
  "tsx",
  "jsx",
  "py",
  "java",
  "c",
  "cpp",
  "cs",
  "go",
  "rs",
  "sql",
  "yaml",
  "yml",
  "xml",
  "toml",
  "ini",
  "log",
  "docx",
  "xlsx",
  "pptx",
  "pdf",
  "png",
  "jpg",
  "jpeg",
  "gif",
  "webp",
  "svg",
];

/** Executables and archives the API refuses with 415. */
export const BLOCKED_UPLOAD_EXTENSIONS: readonly string[] = [
  "exe",
  "dll",
  "bat",
  "cmd",
  "ps1",
  "sh",
  "msi",
  "zip",
  "7z",
  "rar",
  "jar",
];

/** The value for an `<input type="file" accept>` attribute. */
export const UPLOAD_ACCEPT = ALLOWED_UPLOAD_EXTENSIONS.map((e) => `.${e}`).join(
  ",",
);

export type FolderStatus = "none" | "ok" | "missing" | "not_writable";

export interface FolderInfo {
  readonly path: string | null;
  readonly status: FolderStatus;
  readonly writable: boolean;
  readonly name: string | null;
}

export interface FolderEntry {
  readonly name: string;
  readonly type: "file" | "dir";
  readonly size: number | null;
  readonly modified_at: string;
}

export interface FolderTree {
  readonly path: string;
  readonly entries: readonly FolderEntry[];
  readonly truncated: boolean;
}

export type UploadKind =
  | "text"
  | "code"
  | "markdown"
  | "csv"
  | "json"
  | "docx"
  | "xlsx"
  | "pptx"
  | "pdf"
  | "image"
  | "other";

export interface UploadedFile {
  readonly id: string;
  readonly name: string;
  readonly size: number;
  readonly kind: UploadKind | (string & {});
  readonly created_at: string;
}

export interface UploadPage {
  readonly items: readonly UploadedFile[];
  readonly next_cursor: string | null;
}

// ---------------------------------------------------------------------------
// Plain-language errors
// ---------------------------------------------------------------------------

/** Text for a failed files call. Server text is only used for a 422 reason, which is written for people. */
export function describeFilesError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 401:
        return "You are signed out. Sign in again and retry.";
      case 403:
        return "You do not have permission to do that.";
      case 404:
        return "That item no longer exists.";
      case 413:
        return "That file is too large. Each file can be up to 25 MB.";
      case 415:
        return "That type of file cannot be attached. Documents, spreadsheets, slides, PDFs, images, text and code files work. Programs and archives do not.";
      case 422:
        return error.serverMessage !== null && error.serverMessage.trim() !== ""
          ? error.serverMessage
          : "That was not accepted. Check it and try again.";
      case 429:
        return "Too many requests. Wait a moment and try again.";
      default:
        if (error.status >= 500) {
          return "The server could not do that right now. Try again in a moment.";
        }
        return "That could not be done. Try again.";
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
  if (token) headers.set("Authorization", `Bearer ${token}`);

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
  if (!response.ok) throw await apiErrorFromResponse(response);
  return response;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function parseFolder(value: unknown): FolderInfo {
  if (!isRecord(value)) throw new ApiError(200, "unexpected_response");
  const status = value.status;
  if (
    status !== "none" &&
    status !== "ok" &&
    status !== "missing" &&
    status !== "not_writable"
  ) {
    throw new ApiError(200, "unexpected_response");
  }
  return {
    path: typeof value.path === "string" ? value.path : null,
    status,
    writable: value.writable === true,
    name: typeof value.name === "string" ? value.name : null,
  };
}

function parseUpload(value: unknown): UploadedFile {
  if (
    !isRecord(value) ||
    typeof value.id !== "string" ||
    typeof value.name !== "string"
  ) {
    throw new ApiError(200, "unexpected_response");
  }
  return {
    id: value.id,
    name: value.name,
    size: typeof value.size === "number" ? value.size : 0,
    kind: typeof value.kind === "string" ? value.kind : "other",
    created_at: typeof value.created_at === "string" ? value.created_at : "",
  };
}

async function json(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new ApiError(response.status, "unexpected_response");
  }
}

// ---------------------------------------------------------------------------
// Connected folder
// ---------------------------------------------------------------------------

export async function getFolder(): Promise<FolderInfo> {
  const response = await authenticatedFetch(`${BASE}/folder`, {
    headers: { Accept: "application/json" },
  });
  return parseFolder(await json(response));
}

export async function setFolder(path: string): Promise<FolderInfo> {
  const response = await authenticatedFetch(`${BASE}/folder`, {
    method: "PUT",
    headers: { Accept: "application/json", "Content-Type": "application/json" },
    body: JSON.stringify({ path }),
  });
  return parseFolder(await json(response));
}

export async function clearFolder(): Promise<void> {
  await authenticatedFetch(`${BASE}/folder`, { method: "DELETE" });
}

export async function listFolderTree(
  relativePath = "",
  limit = 200,
): Promise<FolderTree> {
  const query = new URLSearchParams({
    path: relativePath,
    limit: String(limit),
  });
  const response = await authenticatedFetch(`${BASE}/folder/tree?${query}`, {
    headers: { Accept: "application/json" },
  });
  const body = await json(response);
  if (!isRecord(body) || !Array.isArray(body.entries)) {
    throw new ApiError(200, "unexpected_response");
  }
  const entries: FolderEntry[] = [];
  for (const raw of body.entries as unknown[]) {
    if (!isRecord(raw) || typeof raw.name !== "string") continue;
    entries.push({
      name: raw.name,
      type: raw.type === "dir" ? "dir" : "file",
      size: typeof raw.size === "number" ? raw.size : null,
      modified_at: typeof raw.modified_at === "string" ? raw.modified_at : "",
    });
  }
  return {
    path: typeof body.path === "string" ? body.path : relativePath,
    entries,
    truncated: body.truncated === true,
  };
}

// ---------------------------------------------------------------------------
// Uploads
// ---------------------------------------------------------------------------

export interface UploadOptions {
  /** Bytes sent so far and the total, as the browser reports them. */
  readonly onProgress?: (loaded: number, total: number) => void;
  readonly signal?: AbortSignal;
}

function sendMultipart(
  files: readonly File[],
  token: string | null,
  options: UploadOptions,
): Promise<{ status: number; body: string }> {
  return new Promise((resolve, reject) => {
    const xhr = new XMLHttpRequest();
    xhr.open("POST", `${BASE}/uploads`);
    xhr.setRequestHeader("Accept", "application/json");
    if (token) xhr.setRequestHeader("Authorization", `Bearer ${token}`);
    xhr.upload.onprogress = (event) => {
      if (event.lengthComputable)
        options.onProgress?.(event.loaded, event.total);
    };
    xhr.onload = () => resolve({ status: xhr.status, body: xhr.responseText });
    xhr.onerror = () => reject(new NetworkError());
    xhr.ontimeout = () => reject(new NetworkError());
    xhr.onabort = () =>
      reject(
        Object.assign(new Error("Upload cancelled"), { name: "AbortError" }),
      );
    const abort = () => xhr.abort();
    if (options.signal) {
      if (options.signal.aborted) {
        abort();
        return;
      }
      options.signal.addEventListener("abort", abort, { once: true });
    }
    const form = new FormData();
    for (const file of files) form.append("files", file, file.name);
    xhr.send(form);
  });
}

/** Turn a finished XHR into the same ApiError a fetch Response would give. */
async function errorFromXhr(status: number, body: string): Promise<ApiError> {
  return apiErrorFromResponse(new Response(body, { status }));
}

/**
 * POST /uploads with the given files in ONE request (1 to 10 files, each at most 25 MB). The
 * composer sends one file per call so each chip has its own progress and its own error.
 */
export async function uploadFiles(
  files: readonly File[],
  options: UploadOptions = {},
): Promise<UploadedFile[]> {
  if (files.length === 0 || files.length > MAX_UPLOAD_FILES) {
    throw new ApiError(422, "validation_error", {
      message: `Choose between 1 and ${MAX_UPLOAD_FILES} files.`,
    });
  }
  let token = getAccessToken();
  if (!token) {
    token = (await refreshSession()) ? getAccessToken() : null;
  }
  let result = await sendMultipart(files, token, options);
  if (result.status === 401 && (await refreshSession())) {
    result = await sendMultipart(files, getAccessToken(), options);
  }
  if (result.status < 200 || result.status >= 300) {
    throw await errorFromXhr(result.status, result.body);
  }
  let body: unknown;
  try {
    body = JSON.parse(result.body);
  } catch {
    throw new ApiError(result.status, "unexpected_response");
  }
  if (!Array.isArray(body))
    throw new ApiError(result.status, "unexpected_response");
  return body.map(parseUpload);
}

/** GET /uploads. Accepts a bare array or `{ items, next_cursor }`. */
export async function listUploads(
  options: { limit?: number; cursor?: string | null } = {},
): Promise<UploadPage> {
  const query = new URLSearchParams();
  if (options.limit !== undefined) query.set("limit", String(options.limit));
  if (options.cursor) query.set("cursor", options.cursor);
  const qs = query.toString();
  const response = await authenticatedFetch(
    `${BASE}/uploads${qs === "" ? "" : `?${qs}`}`,
    { headers: { Accept: "application/json" } },
  );
  const body = await json(response);
  if (Array.isArray(body)) {
    return { items: body.map(parseUpload), next_cursor: null };
  }
  if (isRecord(body) && Array.isArray(body.items)) {
    return {
      items: body.items.map(parseUpload),
      next_cursor:
        typeof body.next_cursor === "string" ? body.next_cursor : null,
    };
  }
  throw new ApiError(200, "unexpected_response");
}

export async function deleteUpload(id: string): Promise<void> {
  await authenticatedFetch(`${BASE}/uploads/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function fetchUploadBlob(id: string): Promise<Blob> {
  const response = await authenticatedFetch(
    `${BASE}/uploads/${encodeURIComponent(id)}/download`,
  );
  return response.blob();
}

/** Authenticated download: fetches the blob, then saves it under `fileName`. */
export async function downloadUpload(
  id: string,
  fileName: string,
): Promise<void> {
  const blob = await fetchUploadBlob(id);
  const url = window.URL.createObjectURL(blob);
  try {
    const anchor = document.createElement("a");
    anchor.href = url;
    anchor.download = fileName;
    document.body.appendChild(anchor);
    anchor.click();
    document.body.removeChild(anchor);
  } finally {
    window.URL.revokeObjectURL(url);
  }
}

/**
 * Authenticated download of a file a chat reply created (GET
 * /api/v1/chat/sessions/{session}/messages/{message}/files/{file}). Same bearer token and refresh
 * as every other call here; the blob is saved under the file's real name, with its extension.
 */
export async function fetchChatFileBlob(
  sessionId: string,
  messageId: string,
  fileId: string,
): Promise<Blob> {
  const response = await authenticatedFetch(
    `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/files/${encodeURIComponent(fileId)}`,
  );
  return response.blob();
}

/**
 * Delete MAYA's stored copy of a file a chat reply created. The card stays in the message and
 * reads as deleted. A copy saved in the user's own folder is never touched. A file that is already
 * gone (404) counts as deleted.
 */
export async function deleteChatFile(
  sessionId: string,
  messageId: string,
  fileId: string,
): Promise<void> {
  try {
    await authenticatedFetch(
      `/api/v1/chat/sessions/${encodeURIComponent(sessionId)}/messages/${encodeURIComponent(messageId)}/files/${encodeURIComponent(fileId)}`,
      { method: "DELETE" },
    );
  } catch (error) {
    if (error instanceof ApiError && error.status === 404) return;
    throw error;
  }
}

export async function downloadChatFile(
  sessionId: string,
  messageId: string,
  fileId: string,
  fileName: string,
  fileType?: string,
): Promise<void> {
  const blob = await fetchChatFileBlob(sessionId, messageId, fileId);
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

// ---------------------------------------------------------------------------
// Client-side checks (mirror the contract so most mistakes are caught before sending)
// ---------------------------------------------------------------------------

export function fileExtension(name: string): string {
  const dot = name.lastIndexOf(".");
  return dot < 0 ? "" : name.slice(dot + 1).toLowerCase();
}

/** A plain reason a file will not be attached, or null when it can be sent. */
export function checkUploadFile(file: {
  name: string;
  size: number;
}): string | null {
  const ext = fileExtension(file.name);
  if (BLOCKED_UPLOAD_EXTENSIONS.includes(ext)) {
    return "Programs and archives cannot be attached.";
  }
  if (!ALLOWED_UPLOAD_EXTENSIONS.includes(ext)) {
    return "This type of file is not supported.";
  }
  if (file.size > MAX_UPLOAD_FILE_BYTES) {
    return "Larger than 25 MB.";
  }
  return null;
}

export function formatBytes(bytes: number): string {
  if (bytes < 1024) return `${bytes} B`;
  if (bytes < 1024 * 1024)
    return `${(bytes / 1024).toFixed(bytes < 10 * 1024 ? 1 : 0)} KB`;
  return `${(bytes / (1024 * 1024)).toFixed(1)} MB`;
}
