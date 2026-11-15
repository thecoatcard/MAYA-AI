/**
 * Typed client for the chat API (see docs/api/CHAT.md and ADR-0008).
 *
 * Requests use the same-origin Next.js proxy, whose upstream is configured on
 * the server with MAYA_API_PROXY_TARGET. The access token is never sent to an
 * API origin chosen by browser-visible configuration.
 *
 * Nothing here logs request or response content, and the access token is only
 * ever placed in the Authorization header.
 */

import {
  getAccessToken,
  getAuthIdentityVersion,
  refreshSession,
} from "./auth-session";
import { readSseFrames, SseBufferOverflowError, type SseFrame } from "./sse";
import { readInputRequest, type ChatInputRequest } from "./ask-user";

export const DEFAULT_API_BASE_URL = "";

const CHAT_PATH = "/api/v1/chat";
const MAX_RETRY_AFTER_SECONDS = 3600;

export function getApiBaseUrl(): string {
  return DEFAULT_API_BASE_URL;
}

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/** API error codes exposed by the chat and authentication endpoints. */
export type KnownApiErrorCode =
  | "idempotency_key_required"
  | "not_authenticated"
  | "invalid_token"
  | "invalid_credentials"
  | "session_not_found"
  | "request_in_progress"
  | "validation_error"
  | "idempotency_key_reused"
  | "rate_limited"
  | "concurrent_stream_limit"
  | "model_unavailable"
  | "chat_unavailable"
  | "unexpected_response"
  | "stream_protocol_error"
  | "unknown_error";

/** A JSON error body `{ "error": code, "message"?: text }` (or a malformed response) from the API. */
export class ApiError extends Error {
  readonly status: number;
  /** The documented error code, or "unknown_error" when the body had none. */
  readonly code: KnownApiErrorCode | (string & {});
  readonly fields: readonly string[];
  /** Seconds from the Retry-After header, or null. */
  readonly retryAfterSeconds: number | null;
  /** Server-authored message, if provided in error body. */
  readonly serverMessage: string | null;

  constructor(
    status: number,
    code: string,
    options: {
      fields?: readonly string[];
      retryAfterSeconds?: number | null;
      message?: string | null;
    } = {},
  ) {
    const errorText =
      typeof options.message === "string" && options.message.trim().length > 0
        ? options.message
        : `The API answered ${status} (${code}).`;
    super(errorText);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = options.fields ?? [];
    this.retryAfterSeconds = options.retryAfterSeconds ?? null;
    this.serverMessage = options.message ?? null;
  }
}

/** Distinct error thrown when proxy fails with bare "Internal Server Error" or upstream connection fails. */
export class BackendUnreachableError extends Error {
  constructor(
    message = "The backend service is unreachable.",
    cause?: unknown,
  ) {
    super(message);
    this.name = "BackendUnreachableError";
    this.cause = cause;
  }
}

/** The request never produced a usable response (offline, DNS, reset or proxy failure). */
export class NetworkError extends Error {
  constructor(cause?: unknown) {
    super("The network request failed.");
    this.name = "NetworkError";
    this.cause = cause;
  }
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function isAbortError(error: unknown): boolean {
  return (
    typeof error === "object" &&
    error !== null &&
    (error as { name?: unknown }).name === "AbortError"
  );
}

function parseRetryAfter(header: string | null): number | null {
  if (header === null) {
    return null;
  }
  const value = header.trim();
  if (value === "") {
    return null;
  }
  let allDigits = value.length <= 7;
  for (let i = 0; allDigits && i < value.length; i += 1) {
    const code = value.charCodeAt(i);
    allDigits = code >= 48 && code <= 57;
  }
  if (allDigits) {
    return Math.min(Number.parseInt(value, 10), MAX_RETRY_AFTER_SECONDS);
  }
  const date = Date.parse(value);
  if (Number.isNaN(date)) {
    return null;
  }
  const seconds = Math.ceil((date - Date.now()) / 1000);
  return Math.min(Math.max(seconds, 0), MAX_RETRY_AFTER_SECONDS);
}

function isCodeLike(value: unknown): value is string {
  if (typeof value !== "string" || value.length === 0 || value.length > 64) {
    return false;
  }
  for (let i = 0; i < value.length; i += 1) {
    const code = value.charCodeAt(i);
    const ok =
      (code >= 97 && code <= 122) || (code >= 48 && code <= 57) || code === 95;
    if (!ok) {
      return false;
    }
  }
  return true;
}

export async function apiErrorFromResponse(
  response: Response,
): Promise<ApiError> {
  let code = "unknown_error";
  let fields: string[] = [];
  let serverMessage: string | null = null;
  try {
    const rawText = await response.text();
    const trimmed = rawText.trim();
    if (response.status >= 500 && trimmed.startsWith("Internal Server Error")) {
      throw new BackendUnreachableError(
        `Backend service unreachable (${response.status}): ${trimmed}`,
      );
    }
    try {
      const body: unknown = JSON.parse(rawText);
      if (isRecord(body)) {
        if (isCodeLike(body.error)) {
          code = body.error;
        } else if (isCodeLike(body.detail)) {
          code = body.detail;
        } else if (
          typeof body.detail === "string" &&
          body.detail.trim().length > 0 &&
          body.detail.length <= 300
        ) {
          serverMessage = body.detail;
        } else if (isRecord(body.detail)) {
          if (isCodeLike(body.detail.error)) {
            code = body.detail.error;
          }
          if (typeof body.detail.message === "string") {
            serverMessage = body.detail.message;
          }
        }

        if (
          typeof body.message === "string" &&
          body.message.trim().length > 0
        ) {
          serverMessage = body.message;
        }
        if (Array.isArray(body.fields)) {
          fields = body.fields
            .filter((field): field is string => typeof field === "string")
            .slice(0, 20);
        }
      }
    } catch (parseErr) {
      if (parseErr instanceof BackendUnreachableError) {
        throw parseErr;
      }
      // Not JSON
    }
  } catch (err) {
    if (err instanceof BackendUnreachableError) {
      throw err;
    }
    // Network or other error reading text
  }
  return new ApiError(response.status, code, {
    fields,
    retryAfterSeconds: parseRetryAfter(response.headers.get("Retry-After")),
    message: serverMessage,
  });
}

/**
 * Query the readiness endpoint GET /api/v1/readyz through the same-origin proxy.
 * Checks PostgreSQL and Redis health as documented in docs/api/HEALTH.md.
 */
export async function checkBackendReady(
  fetchImpl: typeof fetch = globalThis.fetch.bind(globalThis),
): Promise<{ ready: boolean; reason?: string }> {
  try {
    const response = await fetchImpl(`${getApiBaseUrl()}/api/v1/readyz`, {
      method: "GET",
      cache: "no-store",
    });
    if (!response.ok) {
      let reason = `status_${response.status}`;
      try {
        const data = (await response.json()) as { status?: string };
        if (data?.status) {
          reason = data.status;
        }
      } catch {
        // ignore
      }
      return { ready: false, reason };
    }
    const data = (await response.json()) as { status?: string };
    if (data?.status === "ready") {
      return { ready: true };
    }
    return { ready: false, reason: data?.status ?? "not_ready" };
  } catch (err) {
    return {
      ready: false,
      reason: err instanceof Error ? err.message : "network_error",
    };
  }
}

// ---------------------------------------------------------------------------
// Input validation (mirrors the documented rules; the server decides)
// ---------------------------------------------------------------------------

export type ChatContentProblem = "empty" | "invalid_characters";

/** A control character other than newline and tab. */
const DISALLOWED_CONTROL = /[^\P{Cc}\n\t]/u;

/** Count Unicode code points (the server counts characters, not UTF-16 units). */
export function countCodePoints(text: string): number {
  let count = 0;
  for (let i = 0; i < text.length; i += 1) {
    const code = text.charCodeAt(i);
    if (code >= 0xd800 && code <= 0xdbff && i + 1 < text.length) {
      const next = text.charCodeAt(i + 1);
      if (next >= 0xdc00 && next <= 0xdfff) {
        i += 1;
      }
    }
    count += 1;
  }
  return count;
}

/** Empty and control-character rules only. There is no fixed length limit. */
export function validateChatContent(text: string): ChatContentProblem | null {
  if (text.trim() === "") {
    return "empty";
  }
  if (DISALLOWED_CONTROL.test(text)) {
    return "invalid_characters";
  }
  return null;
}

// ---------------------------------------------------------------------------
// Idempotency keys
// ---------------------------------------------------------------------------

/**
 * A fresh Idempotency-Key (8 to 128 characters of A-Za-z0-9_-). The chat hook
 * creates exactly one per user message and reuses it for every retry of that
 * message.
 */
export function createIdempotencyKey(): string {
  const cryptoApi = globalThis.crypto as Crypto | undefined;
  if (cryptoApi !== undefined && typeof cryptoApi.randomUUID === "function") {
    return cryptoApi.randomUUID();
  }
  const bytes = new Uint8Array(16);
  if (cryptoApi !== undefined) {
    cryptoApi.getRandomValues(bytes);
  } else {
    for (let i = 0; i < bytes.length; i += 1) {
      bytes[i] = Math.floor(Math.random() * 256);
    }
  }
  return Array.from(bytes, (byte) => byte.toString(16).padStart(2, "0")).join(
    "",
  );
}

// ---------------------------------------------------------------------------
// Event types
// ---------------------------------------------------------------------------

export interface ChatUsage {
  prompt_tokens: number;
  candidates_tokens: number;
  total_tokens: number;
  /** A decimal string, or null when the cost is unknown (never "0"). */
  cost_usd: string | null;
  is_estimate: boolean;
  /** Prompt tokens the provider served from its cache. Absent when not reported. */
  cached_tokens?: number;
  /** Model calls made for the reply. Absent when the server did not count them. */
  turns?: number | null;
  /** Wall-clock milliseconds of the reply. Absent when not measured. */
  elapsed_ms?: number | null;
}

export type ChatToolStatus = "ok" | "error" | "skipped";

/** A file made inline in a chat reply (`file.created`, or `metadata.files_created`). */
export interface ChatFileCreated {
  readonly fileId: string;
  readonly name: string;
  /** The file type without the dot, for example "md" or "docx". */
  readonly fileType: string;
  readonly mimeType: string;
  readonly sizeBytes: number;
  readonly action: "created" | "edited";
  readonly sourceUploadId: string | null;
}

/**
 * Read one file card from its wire shape (snake_case). Returns null for anything that is not a
 * well-formed card, so a bad entry is skipped instead of breaking the message.
 */
export function readChatFileCreated(value: unknown): ChatFileCreated | null {
  if (!isRecord(value)) return null;
  const {
    file_id,
    name,
    file_type,
    mime_type,
    size_bytes,
    action,
    source_upload_id,
  } = value;
  if (typeof file_id !== "string" || file_id === "") return null;
  if (typeof name !== "string" || name === "") return null;
  if (
    typeof size_bytes !== "number" ||
    !Number.isFinite(size_bytes) ||
    size_bytes < 0
  ) {
    return null;
  }
  if (action !== "created" && action !== "edited") return null;
  return {
    fileId: file_id,
    name,
    fileType: typeof file_type === "string" ? file_type : "",
    mimeType: typeof mime_type === "string" ? mime_type : "",
    sizeBytes: size_bytes,
    action,
    sourceUploadId:
      typeof source_upload_id === "string" ? source_upload_id : null,
  };
}

export type ChatProgressKind =
  | "plan"
  | "think"
  | "tool"
  | "step"
  | "render"
  | "check"
  | "repair"
  | "save"
  | "search"
  | "email"
  | "image"
  | "voice"
  | "note";

export type ChatProgressState = "running" | "done" | "failed" | "skipped";

/**
 * One step of the live "what MAYA is doing" list: the progress stream event, and the same
 * objects kept in the stored reply metadata for reload.
 */
export interface ChatProgressStep {
  readonly runId: string;
  readonly stepId: string;
  readonly parentId: string | null;
  readonly kind: ChatProgressKind;
  readonly label: string;
  readonly detail: string | null;
  readonly state: ChatProgressState;
  readonly index: number | null;
  readonly total: number | null;
  readonly elapsedMs: number | null;
  /** Short thinking summary; only set for kind "think". */
  readonly thought: string | null;
  /** The model's narration between two tool calls; only set for kind "note". */
  readonly note?: string | null;
  /**
   * Live only: characters of streamed reply text this note replaces. The client already showed
   * that text as the reply body, so it removes it instead of showing it twice.
   */
  readonly textLen?: number | null;
}

const PROGRESS_KINDS: readonly string[] = [
  "plan",
  "think",
  "tool",
  "step",
  "render",
  "check",
  "repair",
  "save",
  "search",
  "email",
  "image",
  "voice",
  "note",
];
const PROGRESS_STATES: readonly string[] = [
  "running",
  "done",
  "failed",
  "skipped",
];

function optionalString(value: unknown): string | null {
  return typeof value === "string" ? value : null;
}

function optionalNumber(value: unknown): number | null {
  return typeof value === "number" && Number.isFinite(value) ? value : null;
}

/**
 * Read one progress step from its wire shape (snake_case). Returns null for anything that is not
 * a well-formed step (missing ids, unknown kind or state), so a bad entry is skipped instead of
 * breaking the stream. Unknown extra fields are ignored.
 */
export function readChatProgressStep(value: unknown): ChatProgressStep | null {
  if (!isRecord(value)) return null;
  const { run_id, step_id, kind, state, label } = value;
  if (typeof run_id !== "string" || run_id === "") return null;
  if (typeof step_id !== "string" || step_id === "") return null;
  if (typeof kind !== "string" || !PROGRESS_KINDS.includes(kind)) return null;
  if (typeof state !== "string" || !PROGRESS_STATES.includes(state)) {
    return null;
  }
  return {
    runId: run_id,
    stepId: step_id,
    parentId: optionalString(value.parent_id),
    kind: kind as ChatProgressKind,
    label: typeof label === "string" ? label : "",
    detail: optionalString(value.detail),
    state: state as ChatProgressState,
    index: optionalNumber(value.index),
    total: optionalNumber(value.total),
    elapsedMs: optionalNumber(value.elapsed_ms),
    thought: kind === "think" ? optionalString(value.thought) : null,
    ...(kind === "note"
      ? {
          note: optionalString(value.note),
          textLen: optionalNumber(value.text_len),
        }
      : {}),
  };
}

export type ChatEvent =
  | {
      type: "message.accepted";
      sessionId: string;
      userMessageId: string;
      assistantMessageId: string;
      replayed: boolean;
    }
  | { type: "delta"; text: string }
  | {
      /** One live step (a new step, or an update to a known step id). */
      type: "progress";
      step: ChatProgressStep;
    }
  | {
      type: "task.created";
      taskId: string;
      title: string;
      status: string;
      /** True when an inline answer was moved to the background (the reply says so). */
      promoted: boolean;
    }
  | {
      /** An inline tool call started (progress only; carries no file content). */
      type: "tool.started";
      callId: string;
      name: string;
    }
  | {
      /** An inline tool call finished. */
      type: "tool.finished";
      callId: string;
      name: string;
      status: ChatToolStatus;
    }
  | {
      /** A file was created or edited for this reply; download it with the chat file route. */
      type: "file.created";
      file: ChatFileCreated;
    }
  | {
      /** MAYA saved (or updated) a memory for this message; Undo deletes it. */
      type: "memory.saved";
      memoryId: string;
      text: string;
      updated: boolean;
    }
  | {
      /** MAYA drafted a custom agent (ADR-0023). Nothing is saved until the user approves. */
      type: "agent.draft";
      name: string;
      description: string;
      path: string;
      content: string;
      capabilities: string[];
      instructions: string;
    }
  | {
      /** MAYA asked a question (ask_user). The turn ends; the answer is the next message. */
      type: "input.requested";
      request: ChatInputRequest;
    }
  | {
      /**
       * MAYA drafted a scheduled report (`schedule.draft` with `{ draft_id, name, preview, ... }`).
       * Nothing is created until the user clicks Create schedule on the card.
       */
      type: "schedule.draft";
      draftId: string;
    }
  | {
      type: "done";
      assistantMessageId: string;
      /** "stop", "max_tokens" or "interrupted". */
      finishReason: string;
      usage: ChatUsage | null;
    }
  | { type: "error"; code: string; retryable: boolean };

export function readUsage(value: unknown): ChatUsage | null {
  if (!isRecord(value)) {
    return null;
  }
  const { prompt_tokens, candidates_tokens, total_tokens, cost_usd } = value;
  if (
    typeof prompt_tokens !== "number" ||
    typeof candidates_tokens !== "number" ||
    typeof total_tokens !== "number"
  ) {
    return null;
  }
  return {
    prompt_tokens,
    candidates_tokens,
    total_tokens,
    cost_usd: typeof cost_usd === "string" ? cost_usd : null,
    is_estimate: value.is_estimate === true,
    ...(typeof value.cached_tokens === "number"
      ? { cached_tokens: value.cached_tokens }
      : {}),
    ...(typeof value.turns === "number" ? { turns: value.turns } : {}),
    ...(typeof value.elapsed_ms === "number"
      ? { elapsed_ms: value.elapsed_ms }
      : {}),
  };
}

/**
 * Turn a raw SSE frame into a typed event. Returns null for event names this
 * client does not know (Phase 05 adds `task.*`; they must be ignored) and for
 * frames whose data is not the documented shape.
 */
export function decodeChatEvent(frame: SseFrame): ChatEvent | null {
  switch (frame.event) {
    case "message.accepted":
    case "delta":
    case "progress":
    case "task.created":
    case "tool.started":
    case "tool.finished":
    case "file.created":
    case "memory.saved":
    case "agent.draft":
    case "schedule.draft":
    case "input.requested":
    case "done":
    case "error":
      break;
    default:
      return null;
  }
  let data: unknown;
  try {
    data = JSON.parse(frame.data);
  } catch {
    return null;
  }
  if (!isRecord(data)) {
    return null;
  }
  switch (frame.event) {
    case "message.accepted": {
      const { session_id, user_message_id, assistant_message_id, replayed } =
        data;
      if (
        typeof session_id !== "string" ||
        typeof user_message_id !== "string" ||
        typeof assistant_message_id !== "string" ||
        typeof replayed !== "boolean"
      ) {
        return null;
      }
      return {
        type: "message.accepted",
        sessionId: session_id,
        userMessageId: user_message_id,
        assistantMessageId: assistant_message_id,
        replayed,
      };
    }
    case "delta":
      return typeof data.text === "string"
        ? { type: "delta", text: data.text }
        : null;
    case "progress": {
      const step = readChatProgressStep(data);
      return step === null ? null : { type: "progress", step };
    }
    case "task.created":
      return typeof data.task_id === "string" && data.task_id !== ""
        ? {
            type: "task.created",
            taskId: data.task_id,
            title: typeof data.title === "string" ? data.title : "",
            status: typeof data.status === "string" ? data.status : "executing",
            promoted: data.promoted === true,
          }
        : null;
    case "tool.started":
      return typeof data.call_id === "string" &&
        data.call_id !== "" &&
        typeof data.name === "string"
        ? { type: "tool.started", callId: data.call_id, name: data.name }
        : null;
    case "tool.finished":
      return typeof data.call_id === "string" &&
        data.call_id !== "" &&
        typeof data.name === "string" &&
        (data.status === "ok" ||
          data.status === "error" ||
          data.status === "skipped")
        ? {
            type: "tool.finished",
            callId: data.call_id,
            name: data.name,
            status: data.status,
          }
        : null;
    case "file.created": {
      const file = readChatFileCreated(data);
      return file === null ? null : { type: "file.created", file };
    }
    case "memory.saved":
      return typeof data.memory_id === "string" &&
        data.memory_id !== "" &&
        typeof data.text === "string"
        ? {
            type: "memory.saved",
            memoryId: data.memory_id,
            text: data.text,
            updated: data.updated === true,
          }
        : null;
    case "schedule.draft":
      return typeof data.draft_id === "string" && data.draft_id !== ""
        ? { type: "schedule.draft", draftId: data.draft_id }
        : null;
    case "input.requested": {
      const request = readInputRequest(data);
      return request === null ? null : { type: "input.requested", request };
    }
    case "agent.draft":
      return typeof data.name === "string" &&
        data.name !== "" &&
        typeof data.path === "string" &&
        typeof data.content === "string"
        ? {
            type: "agent.draft",
            name: data.name,
            description:
              typeof data.description === "string" ? data.description : "",
            path: data.path,
            content: data.content,
            capabilities: Array.isArray(data.capabilities)
              ? data.capabilities.filter(
                  (c): c is string => typeof c === "string",
                )
              : [],
            instructions:
              typeof data.instructions === "string" ? data.instructions : "",
          }
        : null;
    case "done": {
      if (
        typeof data.assistant_message_id !== "string" ||
        typeof data.finish_reason !== "string"
      ) {
        return null;
      }
      return {
        type: "done",
        assistantMessageId: data.assistant_message_id,
        finishReason: data.finish_reason,
        usage: readUsage(data.usage),
      };
    }
    case "error":
      return typeof data.code === "string" &&
        typeof data.retryable === "boolean"
        ? { type: "error", code: data.code, retryable: data.retryable === true }
        : null;
    default:
      return null;
  }
}

// ---------------------------------------------------------------------------
// Requests
// ---------------------------------------------------------------------------

export interface RequestOptions {
  signal?: AbortSignal;
  /** Defaults to the in-memory token from auth-session. */
  accessToken?: string | null;
  /** Defaults to the global fetch (looked up at call time). */
  fetchImpl?: typeof fetch;
}

export interface StreamChatMessageParams extends RequestOptions {
  content: string;
  /** One key per user message; reuse it on every retry of that message. */
  idempotencyKey: string;
  /** Omit to let the server create a session. */
  sessionId?: string | null;
  /** Ids of uploaded files (POST /api/v1/files/uploads) the message refers to. Additive. */
  attachments?: readonly string[];
  /** The user's answer to a question card (ask_user): sent with the readable `content`. */
  answer?: { ask_id: string; answers: { selected: string[]; text: string }[] };
  /** Request mode. "deep_research" asks for the multi-source research run. Omitted: normal chat. */
  mode?: ChatRequestMode;
  /** Use THIS assistant message as the content for the request (`source_message_id`). */
  sourceMessageId?: string;
}

export type ChatRequestMode = "deep_research";

function resolveToken(options: RequestOptions): string {
  const token = options.accessToken ?? getAccessToken();
  if (token === null || token === "") {
    throw new ApiError(401, "not_authenticated");
  }
  return token;
}

async function send(
  url: string,
  init: RequestInit,
  options: RequestOptions,
): Promise<Response> {
  const fetchImpl = options.fetchImpl ?? globalThis.fetch.bind(globalThis);
  const identityAtRequestStart = getAuthIdentityVersion();
  try {
    let response = await fetchImpl(url, init);
    if (
      response.status === 401 &&
      url.startsWith(`${getApiBaseUrl()}${CHAT_PATH}`) &&
      options.accessToken === undefined &&
      getAuthIdentityVersion() === identityAtRequestStart
    ) {
      const refreshed = await refreshSession();
      if (
        refreshed !== null &&
        getAuthIdentityVersion() === identityAtRequestStart
      ) {
        const currentToken = getAccessToken();
        if (currentToken === null) {
          return response;
        }
        const headers = new Headers(init.headers);
        headers.set("Authorization", `Bearer ${currentToken}`);
        response = await fetchImpl(url, { ...init, headers });
      }
    }
    return response;
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    throw new NetworkError(error);
  }
}

async function* decodeEvents(
  body: ReadableStream<Uint8Array>,
  signal: AbortSignal | undefined,
): AsyncGenerator<ChatEvent, void, undefined> {
  let malformedKnownEvent = false;
  try {
    for await (const frame of readSseFrames(body, signal)) {
      const event = decodeChatEvent(frame);
      if (event === null) {
        if (
          frame.event === "message.accepted" ||
          frame.event === "delta" ||
          frame.event === "done" ||
          frame.event === "error"
        ) {
          malformedKnownEvent = true;
        }
        if (frame.event === "done" || frame.event === "error") {
          throw new ApiError(200, "stream_protocol_error");
        }
        continue;
      }
      if (
        malformedKnownEvent &&
        (event.type === "done" || event.type === "error")
      ) {
        throw new ApiError(200, "stream_protocol_error");
      }
      yield event;
    }
  } catch (error) {
    if (isAbortError(error)) {
      throw error;
    }
    if (error instanceof ApiError) {
      throw error;
    }
    if (error instanceof SseBufferOverflowError) {
      throw new ApiError(200, "stream_protocol_error");
    }
    throw new NetworkError(error);
  }
}

/**
 * POST /api/v1/chat/message. Resolves once the response headers are known to be
 * a stream, and rejects with `ApiError` (JSON error body, no events were sent)
 * or `NetworkError`. The returned generator yields typed events and throws
 * `NetworkError` if the connection fails mid-stream; it throws an `AbortError`
 * when `signal` is aborted.
 */
export async function streamChatMessage(
  params: StreamChatMessageParams,
): Promise<AsyncGenerator<ChatEvent, void, undefined>> {
  const token = resolveToken(params);
  const body: Record<string, unknown> = { content: params.content };
  if (params.sessionId) {
    body.session_id = params.sessionId;
  }
  if (params.attachments !== undefined && params.attachments.length > 0) {
    body.attachments = [...params.attachments];
  }
  if (params.answer !== undefined) {
    body.answer = params.answer;
  }
  if (params.mode !== undefined) {
    body.mode = params.mode;
  }
  if (params.sourceMessageId !== undefined) {
    body.source_message_id = params.sourceMessageId;
  }
  const response = await send(
    `${getApiBaseUrl()}${CHAT_PATH}/message`,
    {
      method: "POST",
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        Accept: "text/event-stream",
        "Idempotency-Key": params.idempotencyKey,
      },
      body: JSON.stringify(body),
      signal: params.signal,
      cache: "no-store",
      credentials: "omit",
    },
    params,
  );
  if (!response.ok) {
    throw await apiErrorFromResponse(response);
  }
  const contentType = (
    response.headers.get("Content-Type") ?? ""
  ).toLowerCase();
  if (!contentType.startsWith("text/event-stream") || response.body === null) {
    response.body?.cancel().catch(() => undefined);
    throw new ApiError(response.status, "unexpected_response");
  }
  return decodeEvents(response.body, params.signal);
}

// ---------------------------------------------------------------------------
// Read endpoints
// ---------------------------------------------------------------------------

export interface Page<T> {
  items: T[];
  next_cursor: string | null;
}

export interface SessionSummary {
  id: string;
  title: string;
  channel: string;
  status: string;
  created_at: string;
  /** Additive: absent on older servers (treated as false). */
  pinned?: boolean;
  archived?: boolean;
}

export interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  finish_reason?: string | null;
  /** Additive: the user's rating of this reply. */
  feedback?: "up" | "down" | null;
  model_id?: string | null;
  /** `audio` for a spoken voice turn (additive; absent on older servers). */
  modality?: "text" | "audio" | "multimodal";
  /** Voice turn id, null on typed messages. */
  turn_id?: string | null;
  source?: string | null;
  /**
   * Additive server metadata: `task` is set when this reply started a task and
   * `memories_saved` lists the memories it saved (read defensively).
   */
  metadata?: {
    task?: { task_id?: unknown; title?: unknown; promoted?: unknown } | null;
    memories_saved?: unknown;
    /** Final progress steps of this reply, for reload (read defensively). */
    progress?: unknown;
    /** `[file card]` for the files this reply created (read defensively; see readChatFileCreated). */
    files_created?: unknown;
    /** `[{ id, name, kind }]` for the files a user message carried (read defensively). */
    attachments?: unknown;
    /** "deep_research" on a user message sent with the Deep research toggle (read defensively). */
    mode?: unknown;
    /** Token usage of this reply (turns, elapsed_ms, cached_tokens; read defensively). */
    usage?: unknown;
    /** The ask_user question card of this reply (read with readInputRequest). */
    input_request?: unknown;
    /** `{ id }` of a scheduled-report draft this reply made (read with readScheduleDraftId). */
    schedule_draft?: unknown;
    /** `[{ id, name, preview, schedule, expires_at }]`, every draft this reply made. */
    schedule_drafts?: unknown;
    /** On a user message: the card it answered and the sanitised answers. */
    answer?: unknown;
    /** Set on the message MAYA posts when a task ends (read with readTaskResult). */
    task_result?: unknown;
  } | null;
}

export interface ListOptions extends RequestOptions {
  limit?: number;
  cursor?: string | null;
}

async function getJson(path: string, options: ListOptions): Promise<unknown> {
  const token = resolveToken(options);
  const query = new URLSearchParams();
  if (options.limit !== undefined) {
    query.set("limit", String(options.limit));
  }
  if (options.cursor) {
    query.set("cursor", options.cursor);
  }
  const queryText = query.toString();
  const suffix = queryText === "" ? "" : `?${queryText}`;
  const response = await send(
    `${getApiBaseUrl()}${CHAT_PATH}${path}${suffix}`,
    {
      method: "GET",
      headers: { Authorization: `Bearer ${token}`, Accept: "application/json" },
      signal: options.signal,
      cache: "no-store",
      credentials: "omit",
    },
    options,
  );
  if (!response.ok) {
    throw await apiErrorFromResponse(response);
  }
  try {
    return await response.json();
  } catch {
    throw new ApiError(response.status, "unexpected_response");
  }
}

function readPage<T>(
  body: unknown,
  status: number,
  isItem: (item: unknown) => item is T,
): Page<T> {
  if (!isRecord(body) || !Array.isArray(body.items)) {
    throw new ApiError(status, "unexpected_response");
  }
  const items = body.items.filter(isItem);
  const nextCursor =
    typeof body.next_cursor === "string" ? body.next_cursor : null;
  return { items, next_cursor: nextCursor };
}

type SessionWithNullableTitle = Omit<SessionSummary, "title"> & {
  title: string | null;
};

function isSession(item: unknown): item is SessionWithNullableTitle {
  return (
    isRecord(item) &&
    typeof item.id === "string" &&
    (typeof item.title === "string" || item.title === null) &&
    typeof item.created_at === "string"
  );
}

function isStoredMessage(item: unknown): item is StoredMessage {
  return (
    isRecord(item) &&
    typeof item.id === "string" &&
    (item.role === "user" || item.role === "assistant") &&
    typeof item.content === "string" &&
    typeof item.created_at === "string"
  );
}

/** GET /api/v1/chat/sessions: the caller's sessions, newest first. */
export async function listSessions(
  options: ListOptions & { readonly archived?: "false" | "true" | "all" } = {},
): Promise<Page<SessionSummary>> {
  const path =
    options.archived === undefined || options.archived === "false"
      ? "/sessions"
      : `/sessions?archived=${options.archived}`;
  const page = readPage(await getJson(path, options), 200, isSession);
  return {
    ...page,
    items: page.items.map((item) => ({
      ...item,
      title: item.title ?? "Untitled conversation",
    })),
  };
}

/** GET /api/v1/chat/sessions/{id}/messages: oldest first. */
export async function listMessages(
  sessionId: string,
  options: ListOptions = {},
): Promise<Page<StoredMessage>> {
  return readPage(
    await getJson(
      `/sessions/${encodeURIComponent(sessionId)}/messages`,
      options,
    ),
    200,
    isStoredMessage,
  );
}

/**
 * DELETE /api/v1/chat/sessions/{id}: delete a session and all its messages. With
 * `alsoDeleteContent` its finished tasks and files are deleted too; by default they are kept.
 */
export async function deleteSession(
  sessionId: string,
  options: RequestOptions & { readonly alsoDeleteContent?: boolean } = {},
): Promise<void> {
  const token = resolveToken(options);
  const query =
    options.alsoDeleteContent === true ? "?delete_files_and_tasks=true" : "";
  const response = await send(
    `${getApiBaseUrl()}${CHAT_PATH}/sessions/${encodeURIComponent(sessionId)}${query}`,
    {
      method: "DELETE",
      headers: { Authorization: `Bearer ${token}` },
      signal: options.signal,
      cache: "no-store",
      credentials: "omit",
    },
    options,
  );
  if (!response.ok) {
    throw await apiErrorFromResponse(response);
  }
}

// ---------------------------------------------------------------------------
// Conversation and message actions (context menus). A 404 or 501 means the server does not
// have the endpoint yet: callers hide the action instead of showing an error.
// ---------------------------------------------------------------------------

/** True when the server does not offer an endpoint yet. */
export function isUnsupported(error: unknown): boolean {
  return (
    error instanceof ApiError && (error.status === 404 || error.status === 501)
  );
}

async function sendJson(
  method: "PATCH" | "POST",
  path: string,
  body: unknown,
  options: RequestOptions,
): Promise<Response> {
  const token = resolveToken(options);
  const response = await send(
    `${getApiBaseUrl()}${CHAT_PATH}${path}`,
    {
      method,
      headers: {
        Authorization: `Bearer ${token}`,
        "Content-Type": "application/json",
        Accept: "application/json",
      },
      body: JSON.stringify(body),
      signal: options.signal,
      cache: "no-store",
      credentials: "omit",
    },
    options,
  );
  if (!response.ok) throw await apiErrorFromResponse(response);
  return response;
}

function readSession(body: unknown, status: number): SessionSummary {
  if (!isSession(body)) throw new ApiError(status, "unexpected_response");
  return { ...body, title: body.title ?? "Untitled conversation" };
}

/** PATCH /api/v1/chat/sessions/{id}: rename, pin or archive. Returns the updated session. */
export async function updateSession(
  sessionId: string,
  patch: {
    readonly title?: string;
    readonly pinned?: boolean;
    readonly archived?: boolean;
  },
  options: RequestOptions = {},
): Promise<SessionSummary> {
  const response = await sendJson(
    "PATCH",
    `/sessions/${encodeURIComponent(sessionId)}`,
    patch,
    options,
  );
  return readSession(await response.json().catch(() => null), response.status);
}

/** POST /api/v1/chat/sessions/{id}/duplicate: branch a conversation (optionally up to a message). */
export async function duplicateSession(
  sessionId: string,
  fromMessageId?: string,
  options: RequestOptions = {},
): Promise<SessionSummary> {
  const response = await sendJson(
    "POST",
    `/sessions/${encodeURIComponent(sessionId)}/duplicate`,
    fromMessageId === undefined ? {} : { from_message_id: fromMessageId },
    options,
  );
  return readSession(await response.json().catch(() => null), response.status);
}

/** GET /api/v1/chat/sessions/{id}/export?format=md: the conversation as Markdown text. */
export async function exportSessionMarkdown(
  sessionId: string,
  options: RequestOptions = {},
): Promise<string> {
  const token = resolveToken(options);
  const response = await send(
    `${getApiBaseUrl()}${CHAT_PATH}/sessions/${encodeURIComponent(sessionId)}/export?format=md`,
    {
      method: "GET",
      headers: { Authorization: `Bearer ${token}`, Accept: "text/markdown" },
      signal: options.signal,
      cache: "no-store",
      credentials: "omit",
    },
    options,
  );
  if (!response.ok) throw await apiErrorFromResponse(response);
  return response.text();
}

/** POST /api/v1/chat/messages/{id}/feedback: rate a reply (null clears it). 204 on success. */
export async function sendMessageFeedback(
  messageId: string,
  value: "up" | "down" | null,
  note?: string,
  options: RequestOptions = {},
): Promise<void> {
  await sendJson(
    "POST",
    `/messages/${encodeURIComponent(messageId)}/feedback`,
    note === undefined ? { value } : { value, note },
    options,
  );
}
