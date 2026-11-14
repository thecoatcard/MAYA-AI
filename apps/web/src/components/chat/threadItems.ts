import type { ChatMessage } from "../../hooks/useChatStream";
import type { StoredMessage } from "../../lib/api-client";

/**
 * The ONE ordered list the conversation renders. Built by a pure function from
 * typed/stored messages, live voice turns, task references and
 * system notes. Items are deduped by id, never by text.
 */

export interface TimedChatMessage extends ChatMessage {
  /** Milliseconds since epoch: server created_at for history, first-seen time otherwise. */
  readonly createdAt: number;
}

export interface VoiceTurnInput {
  /** Turn id from the voice protocol. */
  readonly id: string;
  readonly role: "user" | "assistant";
  readonly text: string;
  readonly isFinal: boolean;
  readonly timestamp: number;
  /** "typed" when the text was typed while the live session was open. */
  readonly source?: "spoken" | "typed";
  /** Stored message id once MESSAGE_PERSISTED (or reconciliation) linked it. */
  readonly messageId?: string | null;
  /** Markdown a voice tool put in the chat (CHAT_MESSAGE): rendered as Markdown, not spoken text. */
  readonly markdown?: boolean;
}

export interface TaskRef {
  readonly taskId: string;
  readonly title?: string;
  readonly createdAt: number;
}

/** A memory saved during a live voice session (MEMORY_SAVED). */
export interface MemoryChipInput {
  readonly memoryId: string;
  readonly text: string;
  readonly updated: boolean;
  readonly timestamp: number;
}

export interface SystemNoteInput {
  readonly id: string;
  readonly createdAt: number;
  readonly tone: "info" | "error";
  readonly text: string;
}

export type ThreadItem =
  | {
      readonly kind: "message";
      readonly id: string;
      readonly ts: number;
      readonly message: TimedChatMessage;
      /** True for a stored/typed message that was spoken (modality voice). */
      readonly spoken: boolean;
    }
  | {
      readonly kind: "voiceTurn";
      readonly id: string;
      readonly ts: number;
      readonly turnId: string;
      readonly role: "user" | "assistant";
      readonly text: string;
      readonly isFinal: boolean;
      readonly source: "spoken" | "typed";
      readonly markdown?: boolean;
    }
  | {
      readonly kind: "systemNote";
      readonly id: string;
      readonly ts: number;
      readonly tone: "info" | "error";
      readonly text: string;
    }
  | {
      readonly kind: "taskCard";
      readonly id: string;
      readonly ts: number;
      readonly taskId: string;
      readonly title?: string;
    }
  | {
      readonly kind: "memoryChip";
      readonly id: string;
      readonly ts: number;
      readonly memory: MemoryChipInput;
    };

const KIND_ORDER: Record<ThreadItem["kind"], number> = {
  message: 0,
  voiceTurn: 1,
  taskCard: 2,
  memoryChip: 4,
  systemNote: 5,
};

/**
 * Identity rules:
 * - a message is identified by its server id when it has one, else its local key;
 * - a voice turn is identified by the stored message id once persisted, else by
 *   the turn id. When a stored message with the same id is present it replaces
 *   the live item, so a persisted voice turn never appears twice.
 */
export function buildThreadItems(
  messages: readonly TimedChatMessage[],
  voiceTurns: readonly VoiceTurnInput[],
  tasks: readonly TaskRef[],
  notes: readonly SystemNoteInput[] = [],
  memories: readonly MemoryChipInput[] = [],
): ThreadItem[] {
  const byId = new Map<string, ThreadItem>();

  for (const turn of voiceTurns) {
    const id = turn.messageId ?? `voice:${turn.id}`;
    byId.set(turn.messageId ?? `voice:${turn.id}`, {
      kind: "voiceTurn",
      id,
      ts: turn.timestamp,
      turnId: turn.id,
      role: turn.role,
      text: turn.text,
      isFinal: turn.isFinal,
      source: turn.source ?? "spoken",
      markdown: turn.markdown === true,
    });
  }
  for (const message of messages) {
    const id = message.id ?? `local:${message.key}`;
    // A stored message wins over the live voice item that points at it.
    byId.set(id, {
      kind: "message",
      id,
      ts: message.createdAt,
      message,
      spoken: message.modality === "voice",
    });
  }
  for (const task of tasks) {
    byId.set(`task:${task.taskId}`, {
      kind: "taskCard",
      id: `task:${task.taskId}`,
      ts: task.createdAt,
      taskId: task.taskId,
      title: task.title,
    });
  }
  for (const memory of memories) {
    byId.set(`memory:${memory.memoryId}`, {
      kind: "memoryChip",
      id: `memory:${memory.memoryId}`,
      ts: memory.timestamp,
      memory,
    });
  }
  for (const note of notes) {
    byId.set(`note:${note.id}`, {
      kind: "systemNote",
      id: `note:${note.id}`,
      ts: note.createdAt,
      tone: note.tone,
      text: note.text,
    });
  }

  return [...byId.values()]
    .map((item, index) => ({ item, index }))
    .sort(
      (a, b) =>
        a.item.ts - b.item.ts ||
        KIND_ORDER[a.item.kind] - KIND_ORDER[b.item.kind] ||
        a.index - b.index,
    )
    .map(({ item }) => item);
}

export interface StoredLike {
  readonly id: string;
  readonly role: "user" | "assistant";
  readonly content: string;
  /** Voice turn id stored with a spoken message, when the server sends it. */
  readonly turn_id?: string | null;
}

/**
 * Legacy fallback link between final live voice turns and stored messages, for
 * turns whose MESSAGE_PERSISTED id never arrived (the live hook normally sets
 * `messageId`). A stored `turn_id` equal to the turn id links by id; otherwise
 * it is one-to-one by role and exact text. Turns that already carry an id are
 * left alone.
 */
export function linkPersistedTurns(
  turns: readonly VoiceTurnInput[],
  stored: readonly StoredLike[],
): Record<string, string> {
  const claimed = new Set<string>();
  const out: Record<string, string> = {};
  for (const turn of turns) {
    if (!turn.isFinal || turn.messageId) continue;
    const match =
      stored.find((m) => !claimed.has(m.id) && m.turn_id === turn.id) ??
      stored.find(
        (m) =>
          !claimed.has(m.id) &&
          !m.turn_id &&
          m.role === turn.role &&
          m.content.trim() === turn.text.trim(),
      );
    if (match !== undefined) {
      claimed.add(match.id);
      out[turn.id] = match.id;
    }
  }
  return out;
}

/** Maximum task cards rebuilt from history on one conversation load. */
export const MAX_REHYDRATED_TASKS = 10;

/**
 * Task ids stored on assistant messages (`metadata.task`), unique, in message
 * order, capped. The caller fetches the real task detail for each.
 */
export function collectTaskRefs(
  messages: readonly StoredMessage[],
): { taskId: string; title?: string }[] {
  const out: { taskId: string; title?: string }[] = [];
  for (const message of messages) {
    const task = message.metadata?.task;
    if (task === null || task === undefined) continue;
    const id = task.task_id;
    if (typeof id !== "string" || id === "") continue;
    if (out.some((t) => t.taskId === id)) continue;
    out.push({
      taskId: id,
      title: typeof task.title === "string" ? task.title : undefined,
    });
    if (out.length >= MAX_REHYDRATED_TASKS) break;
  }
  return out;
}
