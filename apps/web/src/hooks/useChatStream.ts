import { useCallback, useEffect, useReducer, useRef } from "react";

import {
  ApiError,
  CHAT_MAX_MESSAGE_CHARS,
  NetworkError,
  createIdempotencyKey,
  streamChatMessage,
  validateChatContent,
  type ChatContentProblem,
  type ChatEvent,
  type StoredMessage,
  type ChatUsage,
  type StreamChatMessageParams,
} from "../lib/api-client";

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

export interface ChatMessage {
  /** Stable local key for React lists; never changes. */
  readonly key: string;
  /** Server message id once known. */
  readonly id: string | null;
  readonly role: "user" | "assistant";
  readonly content: string;
  readonly state: ChatMessageState;
  readonly finishReason: string | null;
  readonly usage: ChatUsage | null;
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
  /** Client-side length limit. Default `CHAT_MAX_MESSAGE_CHARS`. */
  maxMessageChars?: number;
}

export interface UseChatStreamResult {
  readonly messages: readonly ChatMessage[];
  readonly status: ChatStatus;
  readonly sessionId: string | null;
  /** Replace the current thread with an already loaded server conversation. */
  openSession: (sessionId: string, messages: readonly StoredMessage[]) => void;
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
  /** Send one user message. Resolves when the whole exchange has ended. */
  send: (text: string) => Promise<void>;
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
  too_long: "This message is longer than the limit.",
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
  | { type: "turn_started"; userKey: string; content: string }
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
          },
        ],
      };
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
        }),
      };
    case "failed": {
      let messages = state.messages;
      if (action.accepted) {
        messages = patch(messages, action.assistantKey, {
          state: "interrupted",
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
        messages = patch(messages, action.assistantKey, { state: "stopped" });
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
  /** The session_id of the FIRST request, reused unchanged on every retry. */
  requestSessionId: string | null;
  accepted: boolean;
  failed: boolean;
  /** A same-key retry may be offered for the failure that ended this turn. */
  canRetry: boolean;
  /** The failure left the server's outcome unknown ("Not confirmed"). */
  unconfirmed: boolean;
}

interface ActiveRun {
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

  useEffect(
    () => () => {
      // Leaving the page: drop the buffered text and the pending frame, then
      // stop the request. Nothing is applied to an unmounted component.
      activeRef.current?.cancel();
      activeRef.current?.controller.abort();
      activeRef.current = null;
    },
    [],
  );

  const execute = useCallback(async (turn: Turn): Promise<void> => {
    const opts = optionsRef.current;
    const open = opts.stream ?? streamChatMessage;
    const maxAttempts = opts.maxAttempts ?? MAX_AUTO_ATTEMPTS;
    const baseMs = opts.backoff?.baseMs ?? DEFAULT_BACKOFF_BASE_MS;
    const maxMs = opts.backoff?.maxMs ?? DEFAULT_BACKOFF_MAX_MS;
    const random = opts.backoff?.random ?? Math.random;

    const controller = new AbortController();
    const { signal } = controller;
    const batcher = new DeltaBatcher((text, replace) => {
      if (!signal.aborted) {
        dispatch({
          type: "delta",
          assistantKey: turn.assistantKey,
          text,
          replace,
        });
      }
    });
    const run: ActiveRun = {
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
      dispatch(action);
    };

    const attemptOnce = async (): Promise<void> => {
      const events = await open({
        content: turn.content,
        sessionId: turn.requestSessionId,
        idempotencyKey: turn.idempotencyKey,
        signal,
      });
      let replaceNext = false;
      for await (const event of events) {
        if (signal.aborted) {
          return;
        }
        switch (event.type) {
          case "message.accepted":
            turn.accepted = true;
            sessionIdRef.current = event.sessionId;
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
      if (activeRef.current === run) {
        activeRef.current = null;
      }
    }
  }, []);

  const send = useCallback(
    async (text: string): Promise<void> => {
      if (activeRef.current !== null) {
        return;
      }
      const opts = optionsRef.current;
      const problem = validateChatContent(
        text,
        opts.maxMessageChars ?? CHAT_MAX_MESSAGE_CHARS,
      );
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
        requestSessionId: sessionIdRef.current,
        accepted: false,
        failed: false,
        canRetry: false,
        unconfirmed: false,
      };
      turnRef.current = turn;
      dispatch({ type: "turn_started", userKey: turn.userKey, content: text });
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
    await send(turn.content);
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
    activeRef.current?.cancel();
    activeRef.current?.controller.abort();
    activeRef.current = null;
    turnRef.current = null;
    sessionIdRef.current = null;
    dispatch({ type: "reset" });
  }, []);

  const openSession = useCallback(
    (sessionId: string, messages: readonly StoredMessage[]): void => {
      activeRef.current?.cancel();
      activeRef.current?.controller.abort();
      activeRef.current = null;
      turnRef.current = null;
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
          usage: null,
        })),
      });
    },
    [],
  );

  return {
    messages: state.messages,
    status: state.status,
    sessionId: state.sessionId,
    openSession,
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
