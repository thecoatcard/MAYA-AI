/**
 * Pure helpers for the live "what MAYA is doing" step list of one reply. The chat hook applies
 * these when a `progress` event arrives, when a reply ends, and when a stored reply is reloaded.
 * Steps are keyed by step_id: an event for a known id updates that step in place.
 */

import {
  readChatProgressStep,
  type ChatProgressStep,
  type ChatProgressState,
  type StoredMessage,
} from "./api-client";

/** Add a step, or replace the step with the same step_id in place. */
export function applyProgressStep(
  steps: readonly ChatProgressStep[],
  step: ChatProgressStep,
): ChatProgressStep[] {
  const index = steps.findIndex((s) => s.stepId === step.stepId);
  if (index === -1) {
    return [...steps, step];
  }
  const next = steps.slice();
  next[index] = step;
  return next;
}

/**
 * Give every step still running a final state. Used when the reply ends: "done" or "failed" for
 * a finished reply, "skipped" when the user stopped it. A running step is never left spinning.
 */
export function settleProgress(
  steps: readonly ChatProgressStep[] | undefined,
  state: Exclude<ChatProgressState, "running">,
): ChatProgressStep[] | undefined {
  if (steps === undefined) return undefined;
  return steps.map((step) =>
    step.state === "running" ? { ...step, state } : step,
  );
}

/** The steps a stored reply kept in metadata.progress, read defensively. */
export function storedProgress(message: StoredMessage): ChatProgressStep[] {
  const raw = message.metadata?.progress;
  if (!Array.isArray(raw)) return [];
  let out: ChatProgressStep[] = [];
  for (const item of raw as unknown[]) {
    const step = readChatProgressStep(item);
    if (step !== null) out = applyProgressStep(out, step);
  }
  return out;
}

/** A task event as the task stream delivers it (only the fields read here). */
interface TaskEventLike {
  readonly event_type: string;
  readonly payload?: Record<string, unknown>;
}

/**
 * The step list of a background task, built from its `task_progress` events in arrival order
 * (payload.step is the same step shape as a reply's). Other events and bad steps are skipped.
 */
export function taskProgressSteps(
  events: readonly TaskEventLike[] | undefined,
): ChatProgressStep[] {
  let out: ChatProgressStep[] = [];
  for (const event of events ?? []) {
    if (event.event_type !== "task_progress") continue;
    const step = readChatProgressStep(event.payload?.step);
    if (step !== null) out = applyProgressStep(out, step);
  }
  return out;
}

/**
 * The label of the running step a voice TASK_EVENT frame carries, or null. A frame for a finished
 * step (or any other task event) gives null, so the status line never keeps a stale label.
 */
export function runningStepLabel(
  frame: { readonly step?: unknown } | null | undefined,
): string | null {
  const step = readChatProgressStep(frame?.step);
  return step !== null && step.state === "running" && step.label !== ""
    ? step.label
    : null;
}

/** A step and the steps nested under it (by parent_id). */
export interface ProgressNode {
  readonly step: ChatProgressStep;
  readonly children: readonly ProgressNode[];
}

/**
 * Nest steps by parent_id, keeping arrival order. A step whose parent is not in the list (or that
 * has none) is a root, so nothing is hidden by a missing parent.
 */
export function buildProgressTree(
  steps: readonly ChatProgressStep[],
): ProgressNode[] {
  const ids = new Set(steps.map((s) => s.stepId));
  const childrenOf = new Map<string, ChatProgressStep[]>();
  const roots: ChatProgressStep[] = [];
  for (const step of steps) {
    if (step.parentId !== null && ids.has(step.parentId)) {
      const list = childrenOf.get(step.parentId) ?? [];
      list.push(step);
      childrenOf.set(step.parentId, list);
    } else {
      roots.push(step);
    }
  }
  const build = (step: ChatProgressStep, depth: number): ProgressNode => ({
    step,
    children:
      depth > MAX_TREE_DEPTH
        ? []
        : (childrenOf.get(step.stepId) ?? []).map((child) =>
            build(child, depth + 1),
          ),
  });
  return roots.map((step) => build(step, 0));
}

/** Depth limit so a malformed parent chain cannot recurse forever. */
const MAX_TREE_DEPTH = 20;

/** Characters of a thinking summary shown before "Show more". */
export const THOUGHT_PREVIEW_CHARS = 160;

/** Elapsed time for people: "850 ms", "2.3 s", "12 s", "1 min 5 s". */
export function formatElapsed(ms: number): string {
  if (ms < 1000) return `${Math.max(0, Math.round(ms))} ms`;
  const seconds = ms / 1000;
  if (seconds < 10) return `${seconds.toFixed(1)} s`;
  if (seconds < 60) return `${Math.round(seconds)} s`;
  const minutes = Math.floor(seconds / 60);
  return `${minutes} min ${Math.round(seconds % 60)} s`;
}

export type ProgressReplyState =
  "live" | "complete" | "interrupted" | "stopped";

/**
 * The one-line summary of a finished reply, for example "Done in 12 s, 6 steps". The time is the
 * sum of the top-level steps that report elapsed time; it is omitted when none do.
 */
export function summarizeProgress(
  steps: readonly ChatProgressStep[],
  replyState: ProgressReplyState,
): string {
  const prefix =
    replyState === "complete"
      ? "Done"
      : replyState === "interrupted"
        ? "Interrupted"
        : replyState === "stopped"
          ? "Stopped"
          : "Working";
  const timed = steps.filter(
    (s) => s.parentId === null && s.elapsedMs !== null,
  );
  const total = timed.reduce((sum, s) => sum + (s.elapsedMs ?? 0), 0);
  const head =
    timed.length > 0 && replyState !== "live"
      ? `${prefix} in ${formatElapsed(total)}`
      : prefix;
  const count = `${steps.length} ${steps.length === 1 ? "step" : "steps"}`;
  const failed = countFailed(steps);
  const failedText = failed > 0 ? `, ${failed} failed` : "";
  return `${head}, ${count}${failedText}`;
}

/**
 * The label of the newest step still running (or the newest step when none runs). Used for the
 * single status line beside the voice controls.
 */
export function latestProgressLabel(
  steps: readonly ChatProgressStep[] | undefined,
): string | null {
  if (steps === undefined || steps.length === 0) return null;
  for (let i = steps.length - 1; i >= 0; i -= 1) {
    const step = steps[i];
    if (step.state === "running" && step.label !== "") return step.label;
  }
  const last = steps[steps.length - 1];
  return last.label !== "" ? last.label : null;
}

/**
 * A step the task engine paused for the user's approval. The backend sends it as state "skipped"
 * with the detail "Waiting for your approval.": it is a wait, not a skip.
 */
export function isApprovalWait(detail: string | null | undefined): boolean {
  return /^waiting for your approval/i.test(detail ?? "");
}

/**
 * A recoverable internal retry (a spec fix, a length adjustment): the backend sends it as state
 * "skipped" with a short plain detail. It is shown as an "Adjusted" row, never as a failure and
 * never as an approval wait.
 */
export function isAdjusted(step: {
  readonly state: ChatProgressState;
  readonly detail: string | null;
}): boolean {
  return (
    step.state === "skipped" &&
    step.detail !== null &&
    step.detail.trim() !== "" &&
    !isApprovalWait(step.detail)
  );
}

/** Only real failures: adjusted (skipped) rows are not counted. */
export function countFailed(steps: readonly ChatProgressStep[]): number {
  return steps.filter((s) => s.state === "failed").length;
}

/** Longest step detail the web will render, a second guard behind the backend's own limit. */
export const STEP_DETAIL_MAX_CHARS = 600;

/** Text that reads as an instruction to the model (a tool name, "call ... again"), not to a person. */
const MODEL_INSTRUCTION =
  /\b[a-z]+(?:_[a-z0-9]+)+\b|\b(?:call|calling|invoke|re-?run|re-?issue)\b[^.!?\n]*\bagain\b|\btool call\b/i;

/**
 * A step detail safe to show: sentences that address the model (for example "call create_document
 * again") are dropped, and the rest is capped. Returns null when nothing readable is left.
 */
export function userFacingDetail(
  detail: string | null | undefined,
): string | null {
  if (detail === null || detail === undefined) return null;
  const kept = detail
    .split(/\n+|(?<=[.!?])\s+/)
    .map((part) => part.trim())
    .filter((part) => part !== "" && !MODEL_INSTRUCTION.test(part));
  if (kept.length === 0) return null;
  const text = kept.join("\n");
  return text.length > STEP_DETAIL_MAX_CHARS
    ? `${text.slice(0, STEP_DETAIL_MAX_CHARS).trimEnd()}...`
    : text;
}
