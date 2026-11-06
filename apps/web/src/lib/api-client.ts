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

export const DEFAULT_API_BASE_URL = "";

/**
 * Owner-approved input limit shown in the UI (MAYA_CHAT_MAX_MESSAGE_CHARS on
 * the server). The server remains the authority.
 */
export const CHAT_MAX_MESSAGE_CHARS = 8000;

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

/** A JSON error body `{ "error": code }` (or a malformed response) from the API. */
export class ApiError extends Error {
  readonly status: number;
  /** The documented error code, or "unknown_error" when the body had none. */
  readonly code: KnownApiErrorCode | (string & {});
  readonly fields: readonly string[];
  /** Seconds from the Retry-After header, or null. */
  readonly retryAfterSeconds: number | null;

  constructor(
    status: number,
    code: string,
    options: {
      fields?: readonly string[];
      retryAfterSeconds?: number | null;
    } = {},
  ) {
    super(`The API answered ${status} (${code}).`);
    this.name = "ApiError";
    this.status = status;
    this.code = code;
    this.fields = options.fields ?? [];
    this.retryAfterSeconds = options.retryAfterSeconds ?? null;
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
  try {
    const body: unknown = await response.json();
    if (isRecord(body)) {
      if (isCodeLike(body.error)) {
        code = body.error;
      }
      if (Array.isArray(body.fields)) {
        fields = body.fields
          .filter((field): field is string => typeof field === "string")
          .slice(0, 20);
      }
    }
  } catch {
    // Not JSON (for example a proxy error page): keep the generic code.
  }
  return new ApiError(response.status, code, {
    fields,
    retryAfterSeconds: parseRetryAfter(response.headers.get("Retry-After")),
  });
}

// ---------------------------------------------------------------------------
// Input validation (mirrors the documented rules; the server decides)
// ---------------------------------------------------------------------------

export type ChatContentProblem = "empty" | "too_long" | "invalid_characters";

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

export function validateChatContent(
  text: string,
  maxChars: number = CHAT_MAX_MESSAGE_CHARS,
): ChatContentProblem | null {
  if (text.trim() === "") {
    return "empty";
  }
  if (countCodePoints(text) > maxChars) {
    return "too_long";
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
      type: "done";
      assistantMessageId: string;
      /** "stop", "max_tokens" or "interrupted". */
      finishReason: string;
      usage: ChatUsage | null;
    }
  | { type: "error"; code: string; retryable: boolean };

function readUsage(value: unknown): ChatUsage | null {
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
}

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
}

export interface StoredMessage {
  id: string;
  role: "user" | "assistant";
  content: string;
  created_at: string;
  finish_reason?: string | null;
  model_id?: string | null;
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
  options: ListOptions = {},
): Promise<Page<SessionSummary>> {
  const page = readPage(await getJson("/sessions", options), 200, isSession);
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

/** DELETE /api/v1/chat/sessions/{id}: delete a session and all its messages. */
export async function deleteSession(
  sessionId: string,
  options: RequestOptions = {},
): Promise<void> {
  const token = resolveToken(options);
  const response = await send(
    `${getApiBaseUrl()}${CHAT_PATH}/sessions/${encodeURIComponent(sessionId)}`,
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
