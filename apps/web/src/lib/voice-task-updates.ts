/**
 * Spoken task updates as ONE compact block per task, and tidy voice transcripts.
 *
 * The server sends a TASK_UPDATE_SPOKEN frame when it asks MAYA to say something about a task
 * (`update_id`, the exact "Step 3 of 5 done: <title>" line), then the same `update_id` again with
 * `said` once she finished speaking, and STEP_REVIEW when she recorded a verdict. They are merged
 * into one entry per update, so a task shows one block that updates in place instead of one chat
 * message per update. A review is shown only when a verdict frame arrived: never assumed.
 */

export interface TaskUpdateLine {
  readonly id: string;
  readonly text: string;
  readonly said?: string;
  readonly review?: {
    readonly verdict: "ok" | "problem";
    readonly note: string;
  };
  readonly milestone: string;
  readonly timestamp: number;
}

export interface TaskUpdateBlock {
  /** The task, or `null` for the catch-up after voice was off. */
  readonly taskId: string | null;
  readonly lines: readonly TaskUpdateLine[];
}

export interface SpokenUpdateFrame {
  readonly update_id?: string;
  readonly task_id: string | null;
  readonly milestone: string;
  readonly text: string;
  readonly said?: string;
}

export interface StepReviewFrame {
  readonly update_id?: string;
  readonly task_id: string;
  readonly verdict: string;
  readonly note?: string;
}

const MAX_LINES_PER_BLOCK = 30;
const MAX_BLOCKS = 5;

function sameBlock(block: TaskUpdateBlock, taskId: string | null): boolean {
  return block.taskId === taskId;
}

function upsertLine(
  blocks: readonly TaskUpdateBlock[],
  taskId: string | null,
  id: string,
  change: (line: TaskUpdateLine | undefined) => TaskUpdateLine | undefined,
): readonly TaskUpdateBlock[] {
  const index = blocks.findIndex((b) => sameBlock(b, taskId));
  const block = index >= 0 ? blocks[index] : { taskId, lines: [] };
  const at = block.lines.findIndex((l) => l.id === id);
  const next = change(at >= 0 ? block.lines[at] : undefined);
  if (next === undefined) return blocks;
  const lines =
    at >= 0
      ? block.lines.map((l, i) => (i === at ? next : l))
      : [...block.lines, next].slice(-MAX_LINES_PER_BLOCK);
  const updated: TaskUpdateBlock = { taskId, lines };
  // The block that just changed moves to the end (newest), the others keep their order.
  const rest = blocks.filter((_, i) => i !== index);
  return [...rest, updated].slice(-MAX_BLOCKS);
}

/** Merge a TASK_UPDATE_SPOKEN frame: a new line, or the spoken text of an existing one. */
export function applySpokenUpdate(
  blocks: readonly TaskUpdateBlock[],
  frame: SpokenUpdateFrame,
  now: number,
): readonly TaskUpdateBlock[] {
  const id = frame.update_id ?? `${frame.task_id ?? "catch-up"}:${now}`;
  return upsertLine(blocks, frame.task_id, id, (line) => {
    if (
      line === undefined &&
      frame.said !== undefined &&
      frame.text === undefined
    ) {
      return undefined;
    }
    return {
      id,
      text: frame.text ?? line?.text ?? "",
      said: frame.said ?? line?.said,
      review: line?.review,
      milestone: frame.milestone,
      timestamp: line?.timestamp ?? now,
    };
  });
}

/** Attach a recorded verdict to its update line. Ignored when the line is unknown. */
export function applyStepReview(
  blocks: readonly TaskUpdateBlock[],
  frame: StepReviewFrame,
): readonly TaskUpdateBlock[] {
  if (frame.update_id === undefined) return blocks;
  const verdict = frame.verdict === "ok" ? "ok" : "problem";
  const id = frame.update_id;
  return upsertLine(blocks, frame.task_id, id, (line) =>
    line === undefined
      ? undefined
      : { ...line, review: { verdict, note: frame.note ?? "" } },
  );
}

export function lineLabel(line: TaskUpdateLine): string {
  return line.said ? `${line.text}. ${line.said}` : line.text;
}

export function reviewLabel(line: TaskUpdateLine): string | null {
  if (line.review === undefined) return null;
  if (line.review.verdict === "ok") return "Reviewed: OK";
  return line.review.note
    ? `Review note: ${line.review.note}`
    : "Review note: a problem was found";
}

// ---- transcripts --------------------------------------------------------------------------

/** Short answers that are real speech, so the noise filter never drops them. */
const SHORT_ANSWERS = new Set([
  "ok",
  "yes",
  "no",
  "ya",
  "yep",
  "nah",
  "ha",
  "haan",
  "han",
  "na",
  "nahi",
  "hi",
  "hey",
  "hmm",
  "stop",
  "go",
]);
const MIN_USER_CHARS = 3;

/** True for a final user turn that is only a stray fragment ("de") and not a real short answer. */
export function isNoiseUserTurn(text: string, isFinal: boolean): boolean {
  if (!isFinal) return false;
  const clean = text
    .trim()
    .toLowerCase()
    .replace(/[.!?,]+$/u, "");
  if (clean.length >= MIN_USER_CHARS) return false;
  return !SHORT_ANSWERS.has(clean);
}

interface TurnLike {
  readonly role: "user" | "assistant";
  readonly text: string;
  readonly isFinal: boolean;
}

/**
 * Drop stray fragments of the user and collapse consecutive identical assistant lines into one
 * with a count ("... (x6)"). Defensive: the server sends one task update at a time, but a repeated
 * model line must never fill the chat.
 */
export function tidyTranscripts<T extends TurnLike>(turns: readonly T[]): T[] {
  const kept = turns.filter(
    (t) => !(t.role === "user" && isNoiseUserTurn(t.text, t.isFinal)),
  );
  const out: T[] = [];
  const counts = new Map<number, number>();
  for (const turn of kept) {
    const last = out[out.length - 1];
    const same =
      last !== undefined &&
      turn.role === "assistant" &&
      last.role === "assistant" &&
      turn.isFinal &&
      last.isFinal &&
      turn.text.trim() !== "" &&
      turn.text.trim() === stripCount(last.text);
    if (same) {
      const at = out.length - 1;
      const total = (counts.get(at) ?? 1) + 1;
      counts.set(at, total);
      out[at] = { ...last, text: `${turn.text.trim()} (x${total})` };
    } else {
      out.push(turn);
    }
  }
  return out;
}

function stripCount(text: string): string {
  return text.replace(/ \(x\d+\)$/u, "").trim();
}

/** The stored review of a step (`output_result.voice_review`), as a short line, or null. */
export function storedReviewLabel(output: unknown): string | null {
  if (typeof output !== "object" || output === null) return null;
  const review = (output as Record<string, unknown>).voice_review;
  if (typeof review !== "object" || review === null) return null;
  const { verdict, note } = review as Record<string, unknown>;
  if (verdict === "ok") return "Reviewed: OK";
  if (verdict === "problem") {
    return typeof note === "string" && note !== ""
      ? `Review note: ${note}`
      : "Review note: a problem was found";
  }
  return null;
}
