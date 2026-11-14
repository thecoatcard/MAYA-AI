import { useCallback, useEffect, useReducer, useRef } from "react";

import {
  ApiError,
  NetworkError,
  type ChatProgressStep,
  createIdempotencyKey,
  streamChatMessage,
  validateChatContent,
  readChatFileCreated,
  readUsage,
  type ChatContentProblem,
  type ChatEvent,
  type ChatFileCreated,
  type ChatToolStatus,
  type StoredMessage,
  type ChatUsage,
  type StreamChatMessageParams,
  type ChatRequestMode,
} from "../lib/api-client";
import {
  answerToWire,
  readInputAnswer,
  readInputRequest,
  type ChatInputAnswer,
  type ChatInputRequest,
} from "../lib/ask-user";
import {
  applyProgressStep,
  settleProgress,
  storedProgress,
} from "../lib/progress-steps";

/**
 * State machine for one chat conversation.
 *
 * - `send(text)` creates ONE Idempotency-Key for the user message. Every retry
 *   of that message (automatic or the Retry button) reuses the same key and the
 *   same `session_id` as the first request, so the server can replay instead of
 *   calling the model again.
 * - `message.accepted` with `replayed: true` means the next delta holds the
 *   complete stored reply: it REPLACES whatever partial text is shown.
 * - The server stores EVERY finished outcome under the key for 24 hours,
 *   failures included, and answers a repeat of the same key and body with that
 *   stored result without calling the model again. So a same-key retry is only
 *   useful when the client does not know the outcome: a network error, a stream
 *   that ended without `done` or `error`, 409 `request_in_progress`, 429 (after
 *   Retry-After), 503 `chat_unavailable` and any other 5xx without a documented
 *   code. After an `error` event (whatever its `retryable` flag says, which is
 *   ignored) only "Ask again" is offered: the same text under a NEW key.
 * - Automatic retry covers a network failure (including a stream that ends
 *   without `done` or `error`) and 409, at most `maxAttempts` times with
 *   exponential backoff and jitter. A stop, a new conversation or an unmount
 *   cancels the wait and the request.
 * - Deltas are applied at most once per animation frame, so the Markdown is
 *   parsed and rendered once per frame instead of once per piece of text. The
 *   buffer is flushed before any other state change and on stop; it is dropped on
 *   reset and unmount.
 * - Message content is never logged.
 */

export type ChatStatus = "idle" | "sending" | "streaming" | "error";

export type ChatMessageState =
  | "pending" // user message, request in flight
  | "sent" // user message acknowledged by the server
  | "failed" // user message the server answered without accepting ("Not sent")
  | "unconfirmed" // user message whose delivery is unknown: the server may have it
  | "streaming" // assistant reply in progress
  | "complete"
  | "interrupted" // reply cut short; what arrived is kept
  | "stopped"; // the user stopped the reply

export interface ChatMessageToolAction {
  readonly toolName: string;
  readonly taskId?: string;
  readonly status: string;
  readonly intent?: string;
  readonly args?: Record<string, unknown>;
  readonly result?: Record<string, unknown>;
}

/** An inline tool call that is still running in an assistant reply (progress chip). */
export interface ChatRunningTool {
  readonly callId: string;
  /** The tool name from the server, for example create_file_inline. */
  readonly name: string;
}

/**
 * A file a reply created or edited, shown as a card. sessionId and messageId are what the
 * authenticated download route needs; they are copied here so a card is self-contained.
 */
export interface ChatFileCard {
  readonly fileId: string;
  readonly name: string;
  readonly fileType: string;
  readonly mimeType: string;
  readonly sizeBytes: number;
  readonly action: "created" | "edited";
  readonly sessionId: string;
  readonly messageId: string;
}

/** A file attached to a user message (from the upload API). */
export interface ChatAttachment {
  readonly id: string;
  readonly name: string;
  readonly kind: string;
}

/** A memory MAYA saved while writing a reply (`memory.saved`); Undo deletes it. */
export interface SavedMemory {
  readonly memoryId: string;
  readonly text: string;
  /** True when a near-identical memory was changed instead of a new one added. */
  readonly updated: boolean;
}

/** A custom agent MAYA drafted in a reply (`agent.draft`); written only after Approve. */
export interface AgentDraft {
  readonly name: string;
  readonly description: string;
  readonly path: string;
  readonly content: string;
  readonly capabilities: readonly string[];
  readonly instructions: string;
}

export interface ChatMessage {
  /** The question card MAYA asked in this reply (ask_user), from the stream or stored metadata. */
  readonly inputRequest?: ChatInputRequest;
  /** The scheduled-report draft this reply made; the card fetches it by id (reload-safe). */
  readonly scheduleDraftIds?: readonly string[];
  /** On a user message: the card it answers, so the thread shows the card as answered. */
  readonly inputAnswer?: ChatInputAnswer;
  /** The user's rating of this reply, from stored data (additive; absent on older servers). */
  readonly feedback?: "up" | "down" | null;
  /** Stable local key for React lists; never changes. */
  readonly key: string;
  /** Server message id once known. */
  readonly id: string | null;
  readonly role: "user" | "assistant";
  readonly content: string;
  readonly state: ChatMessageState;
  readonly finishReason: string | null;
  readonly usage: ChatUsage | null;
  /** Interaction modality (text or voice). */
  readonly modality?: "text" | "voice";
  /** Voice turn id for a stored spoken message. */
  readonly turnId?: string | null;
  /** Live tool or autonomous task execution telemetry. */
  readonly toolAction?: ChatMessageToolAction;
  /** Memories this reply saved, from the stream or from stored metadata. */
  readonly memoriesSaved?: readonly SavedMemory[];
  /** Custom agents this reply drafted, from the stream or from stored metadata. */
  readonly agentDrafts?: readonly AgentDraft[];
  /** Files attached to this user message (just sent, or from stored metadata). */
  readonly attachments?: readonly ChatAttachment[];
  /** On a user message: it was sent with the Deep research toggle (shows a small badge). */
  readonly mode?: ChatRequestMode;
  /** Inline tool calls running now (cleared on finish, and when the reply ends). */
  readonly runningTools?: readonly ChatRunningTool[];
  /** Files this reply created or edited, from the stream or from stored metadata. */
  readonly files?: readonly ChatFileCard[];
  /** The live step list of this reply (progress events, or metadata.progress on reload). */
  readonly progress?: readonly ChatProgressStep[];
}

export interface ChatError {
  readonly code: string;
  /** Plain, specific text safe to show to the user. */
  readonly message: string;
  /**
   * Whether the Retry action (same Idempotency-Key) is offered. True only when
   * the server's outcome for this message is unknown to the client.
   */
  readonly retryable: boolean;
  /** Seconds to wait before retrying (429), or null. */
  readonly retryAfterSeconds: number | null;
}

export interface ChatRetryInfo {
  /** The attempt about to run (2 or 3). */
  readonly attempt: number;
  readonly maxAttempts: number;
}

export interface UseChatStreamOptions {
  /** Replace the network call (tests). Defaults to `streamChatMessage`. */
  stream?: (
    params: StreamChatMessageParams,
  ) => Promise<AsyncIterable<ChatEvent>>;
  /** Replace the Idempotency-Key factory (tests). */
  createKey?: () => string;
  /** Automatic attempts per send or retry. Default 3. */
  maxAttempts?: number;
  /** Backoff tuning. Defaults: 600 ms base, 8000 ms cap, Math.random. */
  backoff?: { baseMs?: number; maxMs?: number; random?: () => number };
  /**
   * Called when the server started a task for the reply (task.created). promoted is true
   * when an inline answer was moved to the background.
   */
  onTaskCreated?: (task: {
    taskId: string;
    title: string;
    status: string;
    promoted: boolean;
  }) => void;
}

export interface UseChatStreamResult {
  readonly messages: readonly ChatMessage[];
  readonly status: ChatStatus;
  readonly sessionId: string | null;
  /** Replace the current thread with an already loaded server conversation. */
  openSession: (sessionId: string, messages: readonly StoredMessage[]) => void;
  /**
   * Take over a session id created elsewhere (the live voice socket reports it
   * in READY) without touching the messages already shown, so typed turns and
   * later reloads use the same conversation.
   */
  adoptSession: (sessionId: string) => void;
  /**
   * Append server-written assistant messages (the "what the task got" result of a finished task)
   * to the open conversation; already shown ones are skipped. Other conversations are ignored.
   */
  appendStored: (sessionId: string, messages: readonly StoredMessage[]) => void;
  readonly error: ChatError | null;
  /** Set while waiting to retry automatically. */
  readonly retrying: ChatRetryInfo | null;
  /** True while a request is in flight (`sending` or `streaming`). */
  readonly isBusy: boolean;
  /** True when the Retry action (same key) is available. */
  readonly canRetry: boolean;
  /**
   * True when the last message is an interrupted reply. It does not depend on
   * `status` or on an error being shown: it also holds after a replayed `done`
   * with `finish_reason: "interrupted"` and after the error was dismissed.
   */
  readonly canAskAgain: boolean;
  /**
   * Send one user message, optionally with already uploaded files (`attachments`, sent as ids).
   * Resolves when the whole exchange has ended.
   */
  send: (
    text: string,
    options?: {
      attachments?: readonly ChatAttachment[];
      /** The answer to a question card; `text` is its readable rendering. */
      answer?: ChatInputAnswer;
      /** "deep_research": ask for the multi-source research run (sent as `mode`). */
      mode?: ChatRequestMode;
      /** Use THIS assistant message as the content for the request (sent as `source_message_id`). */
      sourceMessageId?: string;
    },
  ) => Promise<void>;
  /** Retry the failed message with the SAME Idempotency-Key. */
  retry: () => Promise<void>;
  /**
   * After an interrupted reply: send the same text again as a new message with
   * a new Idempotency-Key (a new user action).
   */
  askAgain: () => Promise<void>;
  /** Abort the request; the server then cancels the model call. */
  stop: () => void;
  /**
   * Clear the error. If the server answered without accepting the message
   * ("Not sent") it is removed and its text is returned so the caller can
   * restore it in the composer. If delivery is unknown ("Not confirmed") the
   * message stays in the thread and nothing is returned, so a second copy is
   * never invited silently.
   */
  dismissError: () => string | null;
  /** Abort and start a new, empty conversation. */
  reset: () => void;
}

/**
 * Spoken or typed, read defensively: the stored-message type does not declare
 * a modality, but a server that sends `modality` (or `metadata.modality`) on a
 * message is honoured so reloaded conversations keep the "spoken" marker.
 */
export function storedModality(
  message: StoredMessage,
): "text" | "voice" | undefined {
  const raw = message as unknown as {
    modality?: unknown;
    metadata?: { modality?: unknown } | null;
  };
  const value = raw.modality ?? raw.metadata?.modality;
  if (value === "voice" || value === "audio") return "voice";
  if (value === "text") return "text";
  return undefined;
}

/**
 * The memories a stored reply saved (`metadata.memories_saved`), read
 * defensively: anything that is not a well-formed entry is skipped.
 */
export function storedMemories(message: StoredMessage): SavedMemory[] {
  const raw = message.metadata?.memories_saved;
  if (!Array.isArray(raw)) return [];
  const out: SavedMemory[] = [];
  for (const item of raw as unknown[]) {
    if (typeof item !== "object" || item === null) continue;
    const entry = item as Record<string, unknown>;
    if (typeof entry.memory_id !== "string" || entry.memory_id === "") continue;
    if (typeof entry.text !== "string") continue;
    if (out.some((m) => m.memoryId === entry.memory_id)) continue;
    out.push({
      memoryId: entry.memory_id,
      text: entry.text,
      updated: entry.updated === true,
    });
  }
  return out;
}

/** The question card answer a stored user message carried, read defensively. */
function storedAnswer(message: StoredMessage): ChatInputAnswer | null {
  const raw = message.metadata?.answer;
  if (typeof raw !== "object" || raw === null) return null;
  const e = raw as Record<string, unknown>;
  return readInputAnswer(e.ask_id, e.answers);
}

/**
 * The scheduled-report draft ids a stored reply carried: `metadata.schedule_drafts[].id`, plus
 * `metadata.schedule_draft.id` (the same draft, or the only one). Order kept, duplicates dropped.
 */
export function storedScheduleDraftIds(message: StoredMessage): string[] {
  const out: string[] = [];
  const add = (raw: unknown): void => {
    if (typeof raw !== "object" || raw === null) return;
    const id = (raw as { id?: unknown }).id;
    if (typeof id === "string" && id !== "" && !out.includes(id)) out.push(id);
  };
  const list = message.metadata?.schedule_drafts;
  if (Array.isArray(list)) (list as unknown[]).forEach(add);
  add(message.metadata?.schedule_draft);
  return out;
}

/** The agent drafts a stored reply carried (`metadata.agent_drafts`), read defensively. */
export function storedAgentDrafts(message: StoredMessage): AgentDraft[] {
  const raw = (message.metadata as Record<string, unknown> | undefined)
    ?.agent_drafts;
  if (!Array.isArray(raw)) return [];
  const out: AgentDraft[] = [];
  for (const item of raw as unknown[]) {
    if (typeof item !== "object" || item === null) continue;
    const e = item as Record<string, unknown>;
    if (typeof e.name !== "string" || e.name === "") continue;
    if (typeof e.path !== "string" || typeof e.content !== "string") continue;
    if (out.some((d) => d.name === e.name)) continue;
    out.push({
      name: e.name,
      description: typeof e.description === "string" ? e.description : "",
      path: e.path,
      content: e.content,
      capabilities: Array.isArray(e.capabilities)
        ? e.capabilities.filter((c): c is string => typeof c === "string")
        : [],
      instructions: typeof e.instructions === "string" ? e.instructions : "",
    });
  }
  return out;
}

/**
 * The files a stored user message carried (`metadata.attachments`), read defensively: anything
 * that is not a well-formed entry is skipped.
 */
export function storedAttachments(message: StoredMessage): ChatAttachment[] {
  const raw = message.metadata?.attachments;
  if (!Array.isArray(raw)) return [];
  const out: ChatAttachment[] = [];
  for (const item of raw as unknown[]) {
    if (typeof item !== "object" || item === null) continue;
    const entry = item as Record<string, unknown>;
    if (typeof entry.id !== "string" || entry.id === "") continue;
    if (typeof entry.name !== "string" || entry.name === "") continue;
    if (out.some((a) => a.id === entry.id)) continue;
    out.push({
      id: entry.id,
      name: entry.name,
      kind: typeof entry.kind === "string" ? entry.kind : "other",
    });
  }
  return out;
}

/**
 * The files a stored reply created (metadata.files_created), read defensively. The card needs
 * the session and message ids for its download route, so they are passed in.
 */
export function storedFiles(
  message: StoredMessage,
  sessionId: string,
): ChatFileCard[] {
  const raw = message.metadata?.files_created;
  if (!Array.isArray(raw)) return [];
  const out: ChatFileCard[] = [];
  for (const item of raw as unknown[]) {
    const file = readChatFileCreated(item);
    if (file === null) continue;
    if (out.some((f) => f.fileId === file.fileId)) continue;
    out.push(toFileCard(file, sessionId, message.id));
  }
  return out;
}

function toFileCard(
  file: ChatFileCreated,
  sessionId: string,
  messageId: string,
): ChatFileCard {
  return {
    fileId: file.fileId,
    name: file.name,
    fileType: file.fileType,
    mimeType: file.mimeType,
    sizeBytes: file.sizeBytes,
    action: file.action,
    sessionId,
    messageId,
  };
}

export const MAX_AUTO_ATTEMPTS = 3;
const DEFAULT_BACKOFF_BASE_MS = 600;
const DEFAULT_BACKOFF_MAX_MS = 8000;

// ---------------------------------------------------------------------------
// Errors
// ---------------------------------------------------------------------------

/**
 * The server sent an `error` event as the last event of the stream. The event's
 * `retryable` flag is deliberately not kept: the outcome is stored on the server,
 * so a same-key retry would only replay this same error.
 */
class StreamErrorEvent extends Error {
  readonly code: string;
  constructor(code: string) {
    super("The stream ended with an error event.");
    this.name = "StreamErrorEvent";
    this.code = code;
  }
}

/** The connection closed before `done` or `error`. */
class StreamEndedEarly extends Error {
  constructor() {
    super("The stream ended before the reply was complete.");
    this.name = "StreamEndedEarly";
  }
}

const ERROR_TEXT: Record<string, string> = {
  network_error:
    "The connection to the server was lost. Check your network, then retry. Retrying will not send the message twice.",
  request_in_progress:
    "This message is still being processed on the server. Wait a moment, then retry.",
  rate_limited: "You have reached the message rate limit.",
  concurrent_stream_limit:
    "Too many replies are already running for this account. Wait a moment, then retry.",
  not_authenticated: "You are not signed in. Sign in to continue.",
  invalid_token: "Your sign-in is no longer valid. Sign in again to continue.",
  session_not_found:
    "This conversation could not be found. Start a new conversation.",
  validation_error:
    "The server rejected this message. Edit it, then send it again.",
  idempotency_key_reused:
    "This message could not be retried safely. Send it again as a new message.",
  idempotency_key_required:
    "The request was missing its safety key. Reload the page and try again.",
  model_unavailable: "The model is not available right now.",
  chat_unavailable: "Chat is temporarily unavailable. Retry in a moment.",
  content_blocked: "The model declined to answer this message.",
  stream_interrupted: "The reply was interrupted. What arrived is kept above.",
  reply_deadline_exceeded:
    "The model took too long to start answering. Ask again to send the message as a new request.",
  internal_error: "Something went wrong while writing the reply.",
  unexpected_response: "The server sent a response this page cannot read.",
  stream_protocol_error: "The server sent a reply this page cannot read.",
  empty: "Type a message first.",
  message_too_long:
    "This message is too long for the model context. Shorten it or attach it as a file.",
  payload_too_large:
    "This message is too large to send. Shorten it or attach it as a file.",
  invalid_characters: "This message contains characters that cannot be sent.",
  client_error: "Something went wrong in this page. Reload and try again.",
};

/** Plain, specific text for an error code. Never includes server text. */
export function describeChatError(code: string): string {
  return Object.prototype.hasOwnProperty.call(ERROR_TEXT, code)
    ? ERROR_TEXT[code]
    : "Something went wrong. Try again.";
}

interface Failure {
  code: string;
  /** A same-key Retry is offered: the server's outcome is unknown to the client. */
  retryable: boolean;
  /** The bounded automatic retry may run. */
  auto: boolean;
  retryAfterSeconds: number | null;
  /**
   * The server may already hold the user message. Before the stream starts this
   * means the message is labelled "Not confirmed" instead of "Not sent".
   */
  unconfirmed: boolean;
}

const NOT_RETRYABLE: Pick<Failure, "retryable" | "auto" | "unconfirmed"> = {
  retryable: false,
  auto: false,
  unconfirmed: false,
};

function classify(error: unknown): Failure {
  if (error instanceof StreamErrorEvent) {
    // A stored outcome: only "Ask again" (a new key) can change anything.
    return {
      ...NOT_RETRYABLE,
      code: error.code,
      retryAfterSeconds: null,
    };
  }
  if (error instanceof NetworkError || error instanceof StreamEndedEarly) {
    return {
      code: "network_error",
      retryable: true,
      auto: true,
      retryAfterSeconds: null,
      unconfirmed: true,
    };
  }
  if (error instanceof ApiError) {
    if (
      error.status >= 200 &&
      error.status < 300 &&
      (error.code === "unexpected_response" ||
        error.code === "stream_protocol_error")
    ) {
      // The server may have processed the message even though its successful
      // response could not be decoded. Retry only with the original key.
      return {
        code: error.code,
        retryable: true,
        auto: false,
        retryAfterSeconds: null,
        unconfirmed: true,
      };
    }
    if (error.status === 409 && error.code === "request_in_progress") {
      return {
        code: error.code,
        retryable: true,
        auto: true,
        retryAfterSeconds: null,
        unconfirmed: true,
      };
    }
    if (error.status === 429) {
      return {
        ...NOT_RETRYABLE,
        code:
          error.code === "concurrent_stream_limit"
            ? "concurrent_stream_limit"
            : "rate_limited",
        retryable: true,
        retryAfterSeconds: error.retryAfterSeconds,
      };
    }
    if (error.code === "chat_unavailable") {
      // Documented 503: chat failed closed, nothing was stored.
      return {
        ...NOT_RETRYABLE,
        code: error.code,
        retryable: true,
        retryAfterSeconds: null,
      };
    }
    if (error.status >= 500 && error.code !== "model_unavailable") {
      // A 5xx without a documented code (for example a proxy's 502 or 504 with an
      // HTML body): what happened behind it is unknown, so retrying with the
      // same key is safe and useful.
      return {
        code: error.code,
        retryable: true,
        auto: false,
        retryAfterSeconds: null,
        unconfirmed: true,
      };
    }
    return { ...NOT_RETRYABLE, code: error.code, retryAfterSeconds: null };
  }
  return {
    ...NOT_RETRYABLE,
    code: "client_error",
    retryAfterSeconds: null,
  };
}

function toChatError(failure: Failure): ChatError {
  return {
    code: failure.code,
    message: describeChatError(failure.code),
    retryable: failure.retryable,
    retryAfterSeconds: failure.retryAfterSeconds,
  };
}

// ---------------------------------------------------------------------------
// Reducer
// ---------------------------------------------------------------------------

interface State {
  messages: ChatMessage[];
  status: ChatStatus;
  sessionId: string | null;
  error: ChatError | null;
  retrying: ChatRetryInfo | null;
}

const initialState: State = {
  messages: [],
  status: "idle",
  sessionId: null,
  error: null,
  retrying: null,
};

type Action =
  | {
      type: "turn_started";
      userKey: string;
      content: string;
      attachments: readonly ChatAttachment[];
      answer?: ChatInputAnswer;
      mode?: ChatRequestMode;
    }
  | { type: "input_requested"; assistantKey: string; request: ChatInputRequest }
  | { type: "schedule_draft"; assistantKey: string; draftId: string }
  | { type: "run_started"; userKey: string; assistantKey: string }
  | { type: "retrying"; info: ChatRetryInfo }
  | {
      type: "accepted";
      userKey: string;
      assistantKey: string;
      sessionId: string;
      userMessageId: string;
      assistantMessageId: string;
      replayed: boolean;
    }
  | { type: "delta"; assistantKey: string; text: string; replace: boolean }
  | { type: "memory_saved"; assistantKey: string; memory: SavedMemory }
  | { type: "agent_draft"; assistantKey: string; draft: AgentDraft }
  | {
      type: "tool_started";
      assistantKey: string;
      callId: string;
      name: string;
    }
  | {
      type: "tool_finished";
      assistantKey: string;
      callId: string;
      status: ChatToolStatus;
    }
  | { type: "file_created"; assistantKey: string; file: ChatFileCreated }
  | { type: "progress"; assistantKey: string; step: ChatProgressStep }
  | {
      type: "done";
      assistantKey: string;
      assistantMessageId: string;
      finishReason: string;
      usage: ChatUsage | null;
    }
  | {
      type: "failed";
      userKey: string;
      assistantKey: string;
      error: ChatError;
      accepted: boolean;
      unconfirmed: boolean;
    }
  | { type: "stopped"; userKey: string; assistantKey: string }
  | { type: "rejected"; error: ChatError }
  | { type: "dismissed"; removeUserKey: string | null }
  | { type: "history_loaded"; sessionId: string; messages: ChatMessage[] }
  | { type: "session_adopted"; sessionId: string }
  | { type: "append_stored"; sessionId: string; messages: ChatMessage[] }
  | { type: "restore"; state: State }
  | { type: "reset" };

function patch(
  messages: ChatMessage[],
  key: string,
  change: Partial<ChatMessage>,
): ChatMessage[] {
  return messages.map((message) =>
    message.key === key ? { ...message, ...change } : message,
  );
}

function reducer(state: State, action: Action): State {
  switch (action.type) {
    case "turn_started":
      return {
        ...state,
        status: "sending",
        error: null,
        retrying: null,
        messages: [
          ...state.messages,
          {
            key: action.userKey,
            id: null,
            role: "user",
            content: action.content,
            state: "pending",
            finishReason: null,
            usage: null,
            ...(action.attachments.length > 0
              ? { attachments: action.attachments }
              : {}),
            ...(action.answer !== undefined
              ? { inputAnswer: action.answer }
              : {}),
            ...(action.mode !== undefined ? { mode: action.mode } : {}),
          },
        ],
      };
    case "schedule_draft": {
      const target = state.messages.find((m) => m.key === action.assistantKey);
      if (
        target === undefined ||
        (target.scheduleDraftIds ?? []).includes(action.draftId)
      ) {
        return state; // a replayed stream sends it again
      }
      return {
        ...state,
        messages: patch(state.messages, action.assistantKey, {
          scheduleDraftIds: [
            ...(target.scheduleDraftIds ?? []),
            action.draftId,
          ],
        }),
      };
    }
    case "input_requested": {
      const target = state.messages.find((m) => m.key === action.assistantKey);
      if (target === undefined) return state;
      return {
        ...state,
        messages: patch(state.messages, action.assistantKey, {
          inputRequest: action.request,
        }),
      };
    }
    case "run_started": {
      let messages = state.messages;
      const user = messages.find((m) => m.key === action.userKey);
      if (
        user !== undefined &&
        (user.state === "failed" || user.state === "unconfirmed")
      ) {
        messages = patch(messages, action.userKey, { state: "pending" });
      }
      const assistant = messages.find((m) => m.key === action.assistantKey);
      if (
        assistant !== undefined &&
        (assistant.state === "interrupted" || assistant.state === "failed")
      ) {
        messages = patch(messages, action.assistantKey, {
          state: "streaming",
        });
      }
      return {
        ...state,
        messages,
        status: "sending",
        error: null,
        retrying: null,
      };
    }
    case "retrying":
      return { ...state, status: "sending", retrying: action.info };
    case "accepted": {
      let messages = patch(state.messages, action.userKey, {
        id: action.userMessageId,
        state: "sent",
      });
      const existing = messages.find((m) => m.key === action.assistantKey);
      if (existing !== undefined) {
        messages = patch(messages, action.assistantKey, {
          id: action.assistantMessageId,
          state: "streaming",
          // A replay keeps the partial text until the stored reply arrives; a
          // fresh stream starts again from empty so nothing is duplicated.
          content: action.replayed ? existing.content : "",
        });
      } else {
        messages = [
          ...messages,
          {
            key: action.assistantKey,
            id: action.assistantMessageId,
            role: "assistant",
            content: "",
            state: "streaming",
            finishReason: null,
            usage: null,
          },
        ];
      }
      return {
        ...state,
        messages,
        sessionId: action.sessionId,
        status: "streaming",
        retrying: null,
      };
    }
    case "delta": {
      const target = state.messages.find((m) => m.key === action.assistantKey);
      if (target === undefined) {
        return state;
      }
      return {
        ...state,
        status: "streaming",
        messages: patch(state.messages, action.assistantKey, {
          content: action.replace ? action.text : target.content + action.text,
        }),
      };
    }
    case "memory_saved": {
      const target = state.messages.find((m) => m.key === action.assistantKey);
      if (target === undefined) {
        return state;
      }
      const existing = target.memoriesSaved ?? [];
      if (existing.some((m) => m.memoryId === action.memory.memoryId)) {
        return state; // a replayed stream sends it again: never show it twice
      }
      return {
        ...state,
        messages: patch(state.messages, action.assistantKey, {
          memoriesSaved: [...existing, action.memory],
        }),
      };
    }
    case "agent_draft": {
      const target = state.messages.find((m) => m.key === action.assistantKey);
      if (target === undefined) {
        return state;
      }
      const existing = target.agentDrafts ?? [];
      if (existing.some((d) => d.name === action.draft.name)) {
        return state; // a replayed stream sends it again: never show it twice
      }
      return {
        ...state,
        messages: patch(state.messages, action.assistantKey, {
          agentDrafts: [...existing, action.draft],
        }),
      };
    }
    case "tool_started": {
      const target = state.messages.find((m) => m.key === action.assistantKey);
      if (target === undefined) {
        return state;
      }
      const running = (target.runningTools ?? []).filter(
        (t) => t.callId !== action.callId,
      );
      return {
        ...state,
        messages: patch(state.messages, action.assistantKey, {
          runningTools: [
            ...running,
            { callId: action.callId, name: action.name },
          ],
        }),
      };
    }
    case "tool_finished": {
      const target = state.messages.find((m) => m.key === action.assistantKey);
      if (target === undefined || target.runningTools === undefined) {
        return state;
      }
      return {
        ...state,
        messages: patch(state.messages, action.assistantKey, {
          runningTools: target.runningTools.filter(
            (t) => t.callId !== action.callId,
          ),
        }),
      };
    }
    case "progress": {
      const target = state.messages.find((m) => m.key === action.assistantKey);
      if (target === undefined) {
        return state;
      }
      // A new note replaces text already streamed as the reply body: take that text out, so the
      // narration shows once, in its place among the steps.
      const isNewNote =
        action.step.kind === "note" &&
        !(target.progress ?? []).some((s) => s.stepId === action.step.stepId);
      const replaced = isNewNote ? (action.step.textLen ?? 0) : 0;
      return {
        ...state,
        messages: patch(state.messages, action.assistantKey, {
          progress: applyProgressStep(target.progress ?? [], action.step),
          ...(replaced > 0 && replaced <= target.content.length
            ? { content: target.content.slice(0, -replaced) }
            : {}),
        }),
      };
    }
    case "file_created": {
      const target = state.messages.find((m) => m.key === action.assistantKey);
      if (
        target === undefined ||
        target.id === null ||
        state.sessionId === null
      ) {
        return state;
      }
      const existing = target.files ?? [];
      if (existing.some((f) => f.fileId === action.file.fileId)) {
        return state; // a replayed stream sends it again: never show it twice
      }
      return {
        ...state,
        messages: patch(state.messages, action.assistantKey, {
          files: [
            ...existing,
            toFileCard(action.file, state.sessionId, target.id),
          ],
        }),
      };
    }
    case "done":
      return {
        ...state,
        status: "idle",
        retrying: null,
        messages: patch(state.messages, action.assistantKey, {
          id: action.assistantMessageId,
          state:
            action.finishReason === "interrupted" ? "interrupted" : "complete",
          finishReason: action.finishReason,
          usage: action.usage,
          runningTools: undefined,
          progress: settleProgress(
            state.messages.find((m) => m.key === action.assistantKey)?.progress,
            action.finishReason === "interrupted" ? "failed" : "done",
          ),
        }),
      };
    case "failed": {
      let messages = state.messages;
      if (action.accepted) {
        const target = messages.find((m) => m.key === action.assistantKey);
        messages = patch(messages, action.assistantKey, {
          state: "interrupted",
          runningTools: undefined,
          progress: settleProgress(target?.progress, "failed"),
        });
      } else {
        messages = patch(messages, action.userKey, {
          state: action.unconfirmed ? "unconfirmed" : "failed",
        });
      }
      return {
        ...state,
        messages,
        status: "error",
        error: action.error,
        retrying: null,
      };
    }
    case "stopped": {
      let messages = state.messages;
      const assistant = messages.find((m) => m.key === action.assistantKey);
      if (assistant !== undefined) {
        messages = patch(messages, action.assistantKey, {
          state: "stopped",
          runningTools: undefined,
          progress: settleProgress(assistant.progress, "skipped"),
        });
      } else {
        // The request was already sent and the server may have stored it.
        const user = messages.find((m) => m.key === action.userKey);
        if (user !== undefined && user.state === "pending") {
          messages = patch(messages, action.userKey, { state: "unconfirmed" });
        }
      }
      return {
        ...state,
        messages,
        status: "idle",
        error: null,
        retrying: null,
      };
    }
    case "rejected":
      return { ...state, status: "error", error: action.error, retrying: null };
    case "dismissed":
      return {
        ...state,
        status: "idle",
        error: null,
        retrying: null,
        messages:
          action.removeUserKey === null
            ? state.messages
            : state.messages.filter((m) => m.key !== action.removeUserKey),
      };
    case "history_loaded":
      return {
        messages: action.messages,
        status: "idle",
        sessionId: action.sessionId,
        error: null,
        retrying: null,
      };
    case "session_adopted":
      return { ...state, sessionId: action.sessionId };
    case "append_stored": {
      // Messages the server wrote on its own (a finished task's result): added at the end of
      // the thread, never twice, without touching a reply that is still streaming.
      if (state.sessionId !== action.sessionId) return state;
      const known = new Set(
        state.messages
          .map((m) => m.id)
          .filter((id): id is string => id !== null),
      );
      const added = action.messages.filter(
        (m) => m.id === null || !known.has(m.id),
      );
      return added.length === 0
        ? state
        : { ...state, messages: [...state.messages, ...added] };
    }
    case "restore":
      return action.state;
    case "reset":
      return initialState;
  }
}

// ---------------------------------------------------------------------------
// Runner helpers
// ---------------------------------------------------------------------------

interface Turn {
  userKey: string;
  assistantKey: string;
  idempotencyKey: string;
  content: string;
  /** Uploaded files sent with this message (the same ids on every retry). */
  attachments: readonly ChatAttachment[];
  /** The answer to a question card, sent with the same key on every retry. */
  answer?: ChatInputAnswer;
  /** Request mode, sent unchanged on every retry. */
  mode?: ChatRequestMode;
  /** The assistant message this request acts on, sent unchanged on every retry. */
  sourceMessageId?: string;
  /** The session_id of the FIRST request, reused unchanged on every retry. */
  requestSessionId: string | null;
  accepted: boolean;
  failed: boolean;
  /** A same-key retry may be offered for the failure that ended this turn. */
  canRetry: boolean;
  /** The failure left the server's outcome unknown ("Not confirmed"). */
  unconfirmed: boolean;
}

/**
 * A reply that keeps running while another conversation is on screen. Its events are applied to
 * `state` (a private copy of that conversation's view) instead of the visible one; coming back
 * restores it, so the work block and the streaming text are exactly where they were.
 */
interface ParkedRun {
  key: string;
  state: State;
  turn: Turn | null;
  run: ActiveRun;
}

interface ActiveRun {
  /** Set while the reply runs in the background (another conversation is on screen). */
  parked: ParkedRun | null;
  /** The run ended (done, failed or stopped); a parked copy is then only history. */
  finished: boolean;
  controller: AbortController;
  /** Apply the buffered text now (the run is still live). */
  flush: () => void;
  /** Drop the buffered text and any scheduled frame. */
  cancel: () => void;
}

/**
 * Collects `delta` text and applies it at most once per animation frame. A reply
 * arrives in many small pieces; without this each piece would re-parse and
 * re-render the whole message. A "replace" delta (a replayed stored reply) wins
 * over text collected before it. Without `requestAnimationFrame` (no window) text
 * is applied immediately.
 */
class DeltaBatcher {
  private text = "";
  private replace = false;
  private pending = false;
  private frame: number | null = null;

  constructor(
    private readonly apply: (text: string, replace: boolean) => void,
  ) {}

  push(text: string, replace: boolean): void {
    if (replace) {
      this.text = text;
      this.replace = true;
    } else {
      this.text += text;
    }
    this.pending = true;
    if (typeof requestAnimationFrame !== "function") {
      this.flush();
      return;
    }
    if (this.frame === null) {
      this.frame = requestAnimationFrame(() => {
        this.frame = null;
        this.flush();
      });
    }
  }

  /** Apply what is buffered, right now. */
  flush(): void {
    this.cancelFrame();
    if (!this.pending) {
      return;
    }
    const { text, replace } = this;
    this.clear();
    this.apply(text, replace);
  }

  /** Forget what is buffered without applying it. */
  cancel(): void {
    this.cancelFrame();
    this.clear();
  }

  private cancelFrame(): void {
    if (this.frame !== null) {
      if (typeof cancelAnimationFrame === "function") {
        cancelAnimationFrame(this.frame);
      }
      this.frame = null;
    }
  }

  private clear(): void {
    this.text = "";
    this.replace = false;
    this.pending = false;
  }
}

/** Wait `ms`, or return false early if `signal` aborts. */
function abortableDelay(ms: number, signal: AbortSignal): Promise<boolean> {
  return new Promise((resolve) => {
    if (signal.aborted) {
      resolve(false);
      return;
    }
    const onAbort = () => {
      clearTimeout(timer);
      resolve(false);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", onAbort);
      resolve(true);
    }, ms);
    signal.addEventListener("abort", onAbort, { once: true });
  });
}

/** Exponential backoff with equal jitter: between half and all of the step. */
function backoffDelay(
  attempt: number,
  baseMs: number,
  maxMs: number,
  random: () => number,
): number {
  const step = Math.min(maxMs, baseMs * 2 ** (attempt - 1));
  return Math.round(step / 2 + random() * (step / 2));
}

function localProblemError(problem: ChatContentProblem): ChatError {
  return {
    code: problem,
    message: describeChatError(problem),
    retryable: false,
    retryAfterSeconds: null,
  };
}

// ---------------------------------------------------------------------------
// Hook
// ---------------------------------------------------------------------------

export function useChatStream(
  options: UseChatStreamOptions = {},
): UseChatStreamResult {
  const [state, dispatch] = useReducer(reducer, initialState);
  const optionsRef = useRef(options);
  useEffect(() => {
    optionsRef.current = options;
  });
  const activeRef = useRef<ActiveRun | null>(null);
  const turnRef = useRef<Turn | null>(null);
  const sessionIdRef = useRef<string | null>(null);
  const counterRef = useRef(0);
  const stateRef = useRef<State>(state);
  stateRef.current = state;
  // Replies that run in the background while another conversation is open, by conversation id.
  const parkedRef = useRef(new Map<string, ParkedRun>());

  useEffect(
    () => () => {
      // Leaving the page (or logging out): drop the buffered text and the pending frame, then
      // stop every request, the background ones too. Nothing is applied to an unmounted
      // component.
      activeRef.current?.cancel();
      activeRef.current?.controller.abort();
      activeRef.current = null;
      for (const parked of parkedRef.current.values()) {
        parked.run.cancel();
        parked.run.controller.abort();
      }
      parkedRef.current.clear();
    },
    [],
  );

  /**
   * Moves the running reply (if any) to the background instead of stopping it: from now on its
   * events go to a private copy of this conversation's view. Opening another conversation or
   * starting a new one must not cancel work the user asked for; only Stop and logout do.
   */
  const parkActive = useCallback((): void => {
    const run = activeRef.current;
    if (run === null) {
      return;
    }
    const turn = turnRef.current;
    const key =
      sessionIdRef.current ??
      stateRef.current.sessionId ??
      `pending-${turn?.userKey ?? "turn"}`;
    const parked: ParkedRun = { key, state: stateRef.current, turn, run };
    run.parked = parked;
    run.flush(); // buffered text lands in the parked copy
    parkedRef.current.set(key, parked);
    activeRef.current = null;
    turnRef.current = null;
  }, []);

  const execute = useCallback(async (turn: Turn): Promise<void> => {
    const opts = optionsRef.current;
    const open = opts.stream ?? streamChatMessage;
    const maxAttempts = opts.maxAttempts ?? MAX_AUTO_ATTEMPTS;
    const baseMs = opts.backoff?.baseMs ?? DEFAULT_BACKOFF_BASE_MS;
    const maxMs = opts.backoff?.maxMs ?? DEFAULT_BACKOFF_MAX_MS;
    const random = opts.backoff?.random ?? Math.random;

    const controller = new AbortController();
    const { signal } = controller;
    const deliver = (action: Action): void => {
      const parked = run.parked;
      if (parked !== null) {
        parked.state = reducer(parked.state, action);
      } else {
        dispatch(action);
      }
    };
    const batcher = new DeltaBatcher((text, replace) => {
      if (!signal.aborted) {
        deliver({
          type: "delta",
          assistantKey: turn.assistantKey,
          text,
          replace,
        });
      }
    });
    const run: ActiveRun = {
      parked: null,
      finished: false,
      controller,
      flush: () => batcher.flush(),
      cancel: () => batcher.cancel(),
    };
    activeRef.current = run;
    // Every dispatch of this run goes through here, so nothing is applied after
    // a stop, a reset or an unmount. Text is batched per frame; every other
    // action first applies the text that arrived before it, so order is kept.
    const emit = (action: Action) => {
      if (signal.aborted) {
        return;
      }
      if (action.type === "delta") {
        batcher.push(action.text, action.replace);
        return;
      }
      batcher.flush();
      deliver(action);
    };

    const attemptOnce = async (): Promise<void> => {
      const events = await open({
        content: turn.content,
        sessionId: turn.requestSessionId,
        idempotencyKey: turn.idempotencyKey,
        signal,
        ...(turn.mode !== undefined ? { mode: turn.mode } : {}),
        ...(turn.sourceMessageId !== undefined
          ? { sourceMessageId: turn.sourceMessageId }
          : {}),
        ...(turn.attachments.length > 0
          ? { attachments: turn.attachments.map((a) => a.id) }
          : {}),
        ...(turn.answer !== undefined
          ? {
              answer: answerToWire(
                turn.answer.askId,
                turn.answer.answers.map((a) => ({
                  selected: a.selected,
                  text: a.text,
                })),
              ),
            }
          : {}),
      });
      let replaceNext = false;
      for await (const event of events) {
        if (signal.aborted) {
          return;
        }
        switch (event.type) {
          case "message.accepted":
            turn.accepted = true;
            if (run.parked === null) {
              sessionIdRef.current = event.sessionId;
            } else {
              // A new conversation that was left before the server named it: file it under its id.
              parkedRef.current.delete(run.parked.key);
              run.parked.key = event.sessionId;
              parkedRef.current.set(event.sessionId, run.parked);
            }
            replaceNext = event.replayed;
            emit({
              type: "accepted",
              userKey: turn.userKey,
              assistantKey: turn.assistantKey,
              sessionId: event.sessionId,
              userMessageId: event.userMessageId,
              assistantMessageId: event.assistantMessageId,
              replayed: event.replayed,
            });
            break;
          case "delta":
            emit({
              type: "delta",
              assistantKey: turn.assistantKey,
              text: event.text,
              replace: replaceNext,
            });
            replaceNext = false;
            break;
          case "memory.saved":
            emit({
              type: "memory_saved",
              assistantKey: turn.assistantKey,
              memory: {
                memoryId: event.memoryId,
                text: event.text,
                updated: event.updated,
              },
            });
            break;
          case "agent.draft":
            emit({
              type: "agent_draft",
              assistantKey: turn.assistantKey,
              draft: {
                name: event.name,
                description: event.description,
                path: event.path,
                content: event.content,
                capabilities: event.capabilities,
                instructions: event.instructions,
              },
            });
            break;
          case "schedule.draft":
            emit({
              type: "schedule_draft",
              assistantKey: turn.assistantKey,
              draftId: event.draftId,
            });
            break;
          case "input.requested":
            emit({
              type: "input_requested",
              assistantKey: turn.assistantKey,
              request: event.request,
            });
            break;
          case "task.created":
            if (!signal.aborted && run.parked === null) {
              // (a background reply's task card comes back from the stored message on return)
              optionsRef.current.onTaskCreated?.({
                taskId: event.taskId,
                title: event.title,
                status: event.status,
                promoted: event.promoted,
              });
            }
            break;
          case "tool.started":
            emit({
              type: "tool_started",
              assistantKey: turn.assistantKey,
              callId: event.callId,
              name: event.name,
            });
            break;
          case "tool.finished":
            emit({
              type: "tool_finished",
              assistantKey: turn.assistantKey,
              callId: event.callId,
              status: event.status,
            });
            break;
          case "progress":
            emit({
              type: "progress",
              assistantKey: turn.assistantKey,
              step: event.step,
            });
            break;
          case "file.created":
            emit({
              type: "file_created",
              assistantKey: turn.assistantKey,
              file: event.file,
            });
            break;
          case "done":
            emit({
              type: "done",
              assistantKey: turn.assistantKey,
              assistantMessageId: event.assistantMessageId,
              finishReason: event.finishReason,
              usage: event.usage,
            });
            return;
          case "error":
            throw new StreamErrorEvent(event.code);
        }
      }
      if (!signal.aborted) {
        throw new StreamEndedEarly();
      }
    };

    emit({
      type: "run_started",
      userKey: turn.userKey,
      assistantKey: turn.assistantKey,
    });
    try {
      for (let attempt = 1; ; attempt += 1) {
        try {
          await attemptOnce();
          return;
        } catch (error) {
          if (signal.aborted) {
            return;
          }
          const failure = classify(error);
          if (failure.auto && attempt < maxAttempts) {
            emit({
              type: "retrying",
              info: { attempt: attempt + 1, maxAttempts },
            });
            const waited = await abortableDelay(
              backoffDelay(attempt, baseMs, maxMs, random),
              signal,
            );
            if (!waited) {
              return;
            }
            continue;
          }
          turn.failed = true;
          turn.canRetry = failure.retryable;
          turn.unconfirmed = failure.unconfirmed;
          emit({
            type: "failed",
            userKey: turn.userKey,
            assistantKey: turn.assistantKey,
            error: toChatError(failure),
            accepted: turn.accepted,
            unconfirmed: failure.unconfirmed,
          });
          return;
        }
      }
    } finally {
      // Every path that should show buffered text has already flushed it.
      batcher.cancel();
      run.finished = true;
      if (activeRef.current === run) {
        activeRef.current = null;
      }
    }
  }, []);

  const send = useCallback(
    async (
      text: string,
      sendOptions?: {
        attachments?: readonly ChatAttachment[];
        answer?: ChatInputAnswer;
        mode?: ChatRequestMode;
        sourceMessageId?: string;
      },
    ): Promise<void> => {
      if (activeRef.current !== null) {
        return;
      }
      const opts = optionsRef.current;
      const problem = validateChatContent(text);
      if (problem !== null) {
        dispatch({ type: "rejected", error: localProblemError(problem) });
        return;
      }
      counterRef.current += 1;
      const n = counterRef.current;
      const turn: Turn = {
        userKey: `user-${n}`,
        assistantKey: `assistant-${n}`,
        idempotencyKey: (opts.createKey ?? createIdempotencyKey)(),
        content: text,
        attachments: sendOptions?.attachments ?? [],
        ...(sendOptions?.answer !== undefined
          ? { answer: sendOptions.answer }
          : {}),
        ...(sendOptions?.mode !== undefined ? { mode: sendOptions.mode } : {}),
        ...(sendOptions?.sourceMessageId !== undefined
          ? { sourceMessageId: sendOptions.sourceMessageId }
          : {}),
        requestSessionId: sessionIdRef.current,
        accepted: false,
        failed: false,
        canRetry: false,
        unconfirmed: false,
      };
      turnRef.current = turn;
      dispatch({
        type: "turn_started",
        userKey: turn.userKey,
        content: text,
        attachments: turn.attachments,
        ...(turn.answer !== undefined ? { answer: turn.answer } : {}),
        ...(turn.mode !== undefined ? { mode: turn.mode } : {}),
      });
      await execute(turn);
    },
    [execute],
  );

  const retry = useCallback(async (): Promise<void> => {
    const turn = turnRef.current;
    if (turn === null || !turn.failed || !turn.canRetry) {
      return;
    }
    if (activeRef.current !== null) {
      return;
    }
    turn.failed = false;
    await execute(turn);
  }, [execute]);

  const dismissError = useCallback((): string | null => {
    const turn = turnRef.current;
    // Only a message the server answered without accepting ("Not sent") goes
    // back to the composer. An unconfirmed one may already be stored, so it stays
    // in the thread and its text is not offered again; the person can still retry
    // it or write a new message on purpose.
    const restorable =
      turn !== null && turn.failed && !turn.accepted && !turn.unconfirmed;
    dispatch({
      type: "dismissed",
      removeUserKey: restorable ? turn.userKey : null,
    });
    if (turn !== null && turn.failed) {
      turn.failed = false;
      turn.canRetry = false;
    }
    return restorable ? turn.content : null;
  }, []);

  // The last message is an interrupted reply: derived from the messages, so it
  // holds however the reply came to be interrupted (an error event, a failed
  // retry, or a replayed `done` with finish_reason "interrupted").
  const lastMessage = state.messages[state.messages.length - 1];
  const canAskAgain =
    lastMessage !== undefined &&
    lastMessage.role === "assistant" &&
    lastMessage.state === "interrupted" &&
    turnRef.current !== null &&
    lastMessage.key === turnRef.current.assistantKey;

  const askAgain = useCallback(async (): Promise<void> => {
    const turn = turnRef.current;
    if (!canAskAgain || turn === null || activeRef.current !== null) {
      return;
    }
    // A new user action: a new Idempotency-Key for the same text. `send` clears
    // the error notice when it starts.
    await send(turn.content, { attachments: turn.attachments });
  }, [canAskAgain, send]);

  const stop = useCallback((): void => {
    const run = activeRef.current;
    if (run === null) {
      return;
    }
    // Text that already arrived is kept: apply it before the run is cancelled.
    run.flush();
    activeRef.current = null;
    run.controller.abort();
    const turn = turnRef.current;
    if (turn !== null) {
      dispatch({
        type: "stopped",
        userKey: turn.userKey,
        assistantKey: turn.assistantKey,
      });
    }
  }, []);

  const reset = useCallback((): void => {
    parkActive();
    turnRef.current = null;
    sessionIdRef.current = null;
    dispatch({ type: "reset" });
  }, [parkActive]);

  const openSession = useCallback(
    (sessionId: string, messages: readonly StoredMessage[]): void => {
      parkActive();
      turnRef.current = null;
      const parked = parkedRef.current.get(sessionId);
      if (parked !== undefined) {
        parkedRef.current.delete(sessionId);
        if (!parked.run.finished) {
          // The reply is still running here: show it as it is (work block, streamed text) and
          // keep applying its events live.
          parked.run.parked = null;
          activeRef.current = parked.run;
          turnRef.current = parked.turn;
          sessionIdRef.current = sessionId;
          dispatch({ type: "restore", state: parked.state });
          return;
        }
      }
      sessionIdRef.current = sessionId;
      dispatch({
        type: "history_loaded",
        sessionId,
        messages: messages.map((message) => ({
          key: `history-${message.id}`,
          id: message.id,
          role: message.role,
          content: message.content,
          state:
            message.role === "assistant"
              ? message.finish_reason === "interrupted"
                ? "interrupted"
                : "complete"
              : "sent",
          finishReason: message.finish_reason ?? null,
          usage:
            message.role === "assistant"
              ? readUsage(message.metadata?.usage)
              : null,
          modality: storedModality(message),
          turnId: message.turn_id ?? null,
          ...(message.feedback !== undefined
            ? { feedback: message.feedback }
            : {}),
          ...(message.role === "assistant" && storedMemories(message).length > 0
            ? { memoriesSaved: storedMemories(message) }
            : {}),
          ...(message.role === "assistant" &&
          storedAgentDrafts(message).length > 0
            ? { agentDrafts: storedAgentDrafts(message) }
            : {}),
          ...(message.role === "assistant" &&
          readInputRequest(message.metadata?.input_request) !== null
            ? {
                inputRequest: readInputRequest(
                  message.metadata?.input_request,
                ) as ChatInputRequest,
              }
            : {}),
          ...(message.role === "assistant" &&
          storedScheduleDraftIds(message).length > 0
            ? { scheduleDraftIds: storedScheduleDraftIds(message) }
            : {}),
          ...(message.role === "user" && storedAnswer(message) !== null
            ? { inputAnswer: storedAnswer(message) as ChatInputAnswer }
            : {}),
          ...(message.role === "user" && storedAttachments(message).length > 0
            ? { attachments: storedAttachments(message) }
            : {}),
          ...(message.role === "user" &&
          message.metadata?.mode === "deep_research"
            ? { mode: "deep_research" as const }
            : {}),
          ...(message.role === "assistant" &&
          storedFiles(message, sessionId).length > 0
            ? { files: storedFiles(message, sessionId) }
            : {}),
          ...(message.role === "assistant" && storedProgress(message).length > 0
            ? {
                // The reply has ended, so a step still marked running is settled, not spun.
                progress: settleProgress(
                  storedProgress(message),
                  message.finish_reason === "interrupted" ? "failed" : "done",
                ),
              }
            : {}),
        })),
      });
    },
    [parkActive],
  );

  const appendStored = useCallback(
    (sessionId: string, messages: readonly StoredMessage[]): void => {
      dispatch({
        type: "append_stored",
        sessionId,
        messages: messages.map((message) => ({
          key: `history-${message.id}`,
          id: message.id,
          role: message.role,
          content: message.content,
          state: "complete" as const,
          finishReason: message.finish_reason ?? null,
          usage: null,
          ...(message.role === "assistant" &&
          storedFiles(message, sessionId).length > 0
            ? { files: storedFiles(message, sessionId) }
            : {}),
        })),
      });
    },
    [],
  );

  const adoptSession = useCallback((sessionId: string): void => {
    sessionIdRef.current = sessionId;
    dispatch({ type: "session_adopted", sessionId });
  }, []);

  return {
    messages: state.messages,
    status: state.status,
    sessionId: state.sessionId,
    openSession,
    adoptSession,
    appendStored,
    error: state.error,
    retrying: state.retrying,
    isBusy: state.status === "sending" || state.status === "streaming",
    canRetry: state.status === "error" && state.error?.retryable === true,
    canAskAgain,
    send,
    retry,
    askAgain,
    stop,
    dismissError,
    reset,
  };
}
