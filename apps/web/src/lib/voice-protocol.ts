/**
 * Voice WebSocket Protocol v2 typed frames and schema definitions.
 *
 * Implements bidirectional WebSocket frames exactly as defined in
 * docs/api/VOICE_SPECIFICATION.md and backend/app/voice/protocol.py (ADR-0015).
 */

export const VOICE_PROTOCOL_VERSION = 2 as const;

// ---------------------------------------------------------------------------
// Client to Server Frames
// ---------------------------------------------------------------------------

export interface HelloClientFrame {
  readonly type: "HELLO";
  readonly protocol: 2;
  readonly token?: string;
  readonly session_id?: string;
  readonly client?: string;
  /** "master" = app-wide progress tracker (top-bar voice). Omitted = conversation-bound. */
  readonly scope?: "master";
}

export interface UserTextClientFrame {
  readonly type: "USER_TEXT";
  readonly text: string;
}

export interface VadSpeechStartClientFrame {
  readonly type: "VAD_SPEECH_START";
}

export interface VadSpeechStopClientFrame {
  readonly type: "VAD_SPEECH_STOP";
}

export interface ClientInterruptClientFrame {
  readonly type: "CLIENT_INTERRUPT";
}

export type VoiceClientFrame =
  | HelloClientFrame
  | UserTextClientFrame
  | VadSpeechStartClientFrame
  | VadSpeechStopClientFrame
  | ClientInterruptClientFrame;

// ---------------------------------------------------------------------------
// Server to Client Frames
// ---------------------------------------------------------------------------

export interface ReadyServerFrame {
  readonly type: "READY";
  readonly protocol: 2;
  readonly session_id: string;
  readonly mode: string;
  readonly input: {
    readonly rate: number;
    readonly frame_bytes: number;
  };
  readonly output: {
    readonly rate: number;
  };
  readonly resumable: boolean;
  readonly resumed: boolean;
  readonly persisted: boolean;
  readonly title?: string | null;
}

export interface LiveUnavailableServerFrame {
  readonly type: "LIVE_UNAVAILABLE";
  readonly reason: "no_key" | "connect_failed" | "quota" | "disabled" | string;
  readonly session_id?: string | null;
}

export interface TurnStateServerFrame {
  readonly type: "TURN_STATE";
  readonly turn_id: string | null;
  readonly state: "listening" | "thinking" | "speaking" | "interrupted";
}

export interface TranscriptServerFrame {
  readonly type: "TRANSCRIPT";
  readonly turn_id: string;
  readonly role: "user" | "assistant";
  readonly source?: "spoken" | "typed";
  readonly delta: string;
  readonly final: boolean;
  readonly text?: string;
}

export interface MessagePersistedServerFrame {
  readonly type: "MESSAGE_PERSISTED";
  readonly turn_id: string;
  readonly message_id: string;
  readonly role: "user" | "assistant";
}

/** Optional frame: MAYA saved a memory while talking (older clients may ignore it). */
export interface MemorySavedServerFrame {
  readonly type: "MEMORY_SAVED";
  readonly memory_id: string;
  readonly text: string;
  readonly updated?: boolean;
}

/**
 * Additive frame: a voice tool (show_in_chat, show_code, a long tool result) put an assistant
 * message into the chat. It is already stored under `message_id`; show it live and dedupe by id.
 */
export interface ChatMessageServerFrame {
  readonly type: "CHAT_MESSAGE";
  readonly message_id: string;
  readonly role: "assistant";
  readonly content: string;
  readonly tool?: string;
}

/** MAYA drafted a scheduled report by voice; the card is the same one chat shows (click-only). */
export interface ScheduleDraftServerFrame {
  readonly type: "SCHEDULE_DRAFT";
  readonly message_id: string;
  readonly card: { readonly id?: string; readonly draft_id?: string };
}

/** The draft id of a SCHEDULE_DRAFT card (`id`, or `draft_id`), or null. */
export function scheduleDraftCardId(
  frame: ScheduleDraftServerFrame,
): string | null {
  const id = frame.card.id ?? frame.card.draft_id;
  return typeof id === "string" && id !== "" ? id : null;
}

export interface AudioInterruptServerFrame {
  readonly type: "AUDIO_INTERRUPT";
  readonly epoch: number;
}

export interface TaskEventServerFrame {
  readonly type: "TASK_EVENT";
  readonly task_id: string;
  readonly event_type: string;
  readonly subtask_id: string | null;
  readonly title: string | null;
  readonly status: string | null;
  /** The progress step of a `task_progress` event (wire shape; read with readChatProgressStep). */
  readonly step?: unknown;
}

export interface TaskStatusServerFrame {
  readonly type: "TASK_STATUS";
  readonly task_id: string;
  readonly status: string;
  readonly intent?: string;
  readonly tool_name?: string;
  readonly args?: Record<string, unknown>;
  readonly result?: Record<string, unknown>;
}

/**
 * Additive frame: MAYA just spoke a task update on her own (a task finished, failed, was
 * cancelled, waits for approval or needs a detail), or gave the catch-up for what happened while
 * voice was off. `text` is the short factual line the model was given, shown as a status entry.
 */
export interface TaskUpdateSpokenServerFrame {
  readonly type: "TASK_UPDATE_SPOKEN";
  /** Same id for the line sent when MAYA is asked to speak and the later `said` frame. */
  readonly update_id?: string;
  readonly task_id: string | null;
  readonly milestone:
    | "completed"
    | "failed"
    | "cancelled"
    | "approval"
    | "needs_input"
    | "step_done"
    | "step_failed";
  /** The exact line, for example "Step 3 of 5 done: Chapter 3: Collectors". */
  readonly text: string;
  readonly catch_up: boolean;
  /** What MAYA actually said (sent after she finished; absent when she stayed silent). */
  readonly said?: string;
}

/** MAYA recorded a verdict on a finished step (report_step_review). Never sent otherwise. */
export interface StepReviewServerFrame {
  readonly type: "STEP_REVIEW";
  readonly update_id?: string;
  readonly task_id: string;
  readonly subtask_id: string;
  readonly verdict: "ok" | "problem";
  readonly note: string;
}

export interface SessionRenewedServerFrame {
  readonly type: "SESSION_RENEWED";
}

export interface GoAwayServerFrame {
  readonly type: "GO_AWAY";
  readonly retry_ms?: number | null;
  readonly resumable: boolean;
}

export interface ErrorServerFrame {
  readonly type: "ERROR";
  readonly code: string;
  readonly message: string;
  readonly fatal: boolean;
}

export type VoiceServerFrame =
  | ReadyServerFrame
  | LiveUnavailableServerFrame
  | TurnStateServerFrame
  | TranscriptServerFrame
  | MessagePersistedServerFrame
  | MemorySavedServerFrame
  | ChatMessageServerFrame
  | AudioInterruptServerFrame
  | TaskEventServerFrame
  | TaskUpdateSpokenServerFrame
  | StepReviewServerFrame
  | TaskStatusServerFrame
  | SessionRenewedServerFrame
  | GoAwayServerFrame
  | ErrorServerFrame;

// ---------------------------------------------------------------------------
// Type Guards and Frame Helpers
// ---------------------------------------------------------------------------

function isObject(val: unknown): val is Record<string, unknown> {
  return typeof val === "object" && val !== null && !Array.isArray(val);
}

export function isVoiceServerFrame(value: unknown): value is VoiceServerFrame {
  if (!isObject(value) || typeof value.type !== "string") {
    return false;
  }
  switch (value.type) {
    case "READY":
      return value.protocol === 2 && typeof value.session_id === "string";
    case "LIVE_UNAVAILABLE":
      return typeof value.reason === "string";
    case "TURN_STATE":
      return typeof value.state === "string";
    case "TRANSCRIPT":
      return (
        typeof value.turn_id === "string" &&
        (value.role === "user" || value.role === "assistant") &&
        typeof value.delta === "string" &&
        typeof value.final === "boolean"
      );
    case "MESSAGE_PERSISTED":
      return (
        typeof value.turn_id === "string" &&
        typeof value.message_id === "string" &&
        (value.role === "user" || value.role === "assistant")
      );
    case "MEMORY_SAVED":
      return (
        typeof value.memory_id === "string" &&
        value.memory_id !== "" &&
        typeof value.text === "string"
      );
    case "CHAT_MESSAGE":
      return (
        typeof value.message_id === "string" &&
        value.message_id !== "" &&
        typeof value.content === "string" &&
        value.content.trim() !== ""
      );
    case "SCHEDULE_DRAFT":
      return (
        typeof value.message_id === "string" &&
        typeof value.card === "object" &&
        value.card !== null
      );
    case "AUDIO_INTERRUPT":
      return typeof value.epoch === "number";
    case "TASK_EVENT":
      return (
        typeof value.task_id === "string" &&
        typeof value.event_type === "string"
      );
    case "TASK_STATUS":
      return (
        typeof value.task_id === "string" && typeof value.status === "string"
      );
    case "TASK_UPDATE_SPOKEN":
      return (
        typeof value.milestone === "string" && typeof value.text === "string"
      );
    case "STEP_REVIEW":
      return (
        typeof value.task_id === "string" &&
        (value.verdict === "ok" || value.verdict === "problem")
      );
    case "SESSION_RENEWED":
      return true;
    case "GO_AWAY":
      return typeof value.resumable === "boolean";
    case "ERROR":
      return (
        typeof value.code === "string" &&
        typeof value.message === "string" &&
        typeof value.fatal === "boolean"
      );
    default:
      return false;
  }
}

export function createHelloFrame(options: {
  token?: string | null;
  sessionId?: string | null;
  client?: string;
  scope?: "master" | "conversation";
}): HelloClientFrame {
  const frame: {
    type: "HELLO";
    protocol: 2;
    token?: string;
    session_id?: string;
    client?: string;
    scope?: "master";
  } = {
    type: "HELLO",
    protocol: 2,
    client: options.client ?? "web",
  };
  if (options.token) {
    frame.token = options.token;
  }
  if (options.sessionId) {
    frame.session_id = options.sessionId;
  }
  if (options.scope === "master") {
    frame.scope = "master";
  }
  return frame;
}

export function createUserTextFrame(text: string): UserTextClientFrame {
  return {
    type: "USER_TEXT",
    text,
  };
}

export function createClientInterruptFrame(): ClientInterruptClientFrame {
  return {
    type: "CLIENT_INTERRUPT",
  };
}

export function createVadSpeechStartFrame(): VadSpeechStartClientFrame {
  return {
    type: "VAD_SPEECH_START",
  };
}

export function createVadSpeechStopFrame(): VadSpeechStopClientFrame {
  return {
    type: "VAD_SPEECH_STOP",
  };
}
