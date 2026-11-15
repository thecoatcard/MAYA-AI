/**
 * Pure helpers for the work log under an assistant reply: which family a step belongs to, how
 * consecutive steps of one family collapse into one row, the header wording and the footer
 * figures. The component (components/chat/ProgressSteps.tsx) only renders what these return.
 *
 * Nothing here invents a number: a figure that the server did not report is left out.
 */

import type { ChatProgressStep, ChatUsage } from "./api-client";
import {
  countFailed,
  formatElapsed,
  isApprovalWait,
  userFacingDetail,
  type ProgressNode,
  type ProgressReplyState,
} from "./progress-steps";

export type StepFamily =
  "web" | "notion" | "github" | "mail" | "memory" | "document" | "tool";

/** Labels the server gives its tool steps (services/progress.py tool_step), by family. */
const FAMILY_BY_LABEL: Readonly<Record<string, StepFamily>> = {
  "searching the web": "web",
  "reading a web page": "web",
  "reading notion": "notion",
  "using notion": "notion",
  "reading github": "github",
  "using github": "github",
  "reading your mail": "mail",
  "checking saved memory": "memory",
  "saving to memory": "memory",
  "updating memory": "memory",
  "creating document": "document",
  "previewing document": "document",
  "reading the sheet": "document",
  "editing the sheet": "document",
  "creating file": "document",
  "editing file": "document",
  "using a tool": "tool",
  "running a tool": "tool",
  "looking for a tool": "tool",
  "reading code": "tool",
};

/**
 * The family of a step, or null when it is not a tool call that may be grouped (a plan, a
 * thought, a note, a task's own steps). Only the known tool labels group, so the steps of a
 * background task are never merged by accident.
 */
export function stepFamily(
  step: Pick<ChatProgressStep, "kind" | "label">,
): StepFamily | null {
  if (step.kind === "note" || step.kind === "think") return null;
  const byLabel = FAMILY_BY_LABEL[step.label.trim().toLowerCase()];
  if (byLabel !== undefined) return byLabel;
  return step.kind === "tool" ? "tool" : null;
}

/** One entry of the work log, in the order things happened. */
export type TimelineEntry =
  | { readonly type: "note"; readonly step: ChatProgressStep }
  | {
      readonly type: "group";
      readonly family: StepFamily;
      readonly nodes: readonly ProgressNode[];
    }
  | { readonly type: "node"; readonly node: ProgressNode };

/**
 * Turn the top-level steps into timeline entries. Two or more consecutive steps of one family
 * become one group; a note, a thought or a step of another family ends the run, so the order of
 * what happened stays visible. A single step is never wrapped in a group.
 */
export function buildTimeline(nodes: readonly ProgressNode[]): TimelineEntry[] {
  const out: TimelineEntry[] = [];
  let run: ProgressNode[] = [];
  let runFamily: StepFamily | null = null;
  const flush = () => {
    if (run.length >= 2 && runFamily !== null) {
      out.push({ type: "group", family: runFamily, nodes: run });
    } else {
      for (const node of run) out.push({ type: "node", node });
    }
    run = [];
    runFamily = null;
  };
  for (const node of nodes) {
    if (node.step.kind === "note") {
      flush();
      out.push({ type: "note", step: node.step });
      continue;
    }
    const family = stepFamily(node.step);
    if (family === null) {
      flush();
      out.push({ type: "node", node });
      continue;
    }
    if (family !== runFamily) flush();
    runFamily = family;
    run.push(node);
  }
  flush();
  return out;
}

/** Counts a group row shows: how many steps failed, and whether any is still running. */
export function groupStatus(nodes: readonly ProgressNode[]): {
  readonly failed: number;
  readonly running: boolean;
} {
  const steps = nodes.map((n) => n.step);
  return {
    failed: countFailed(steps),
    running: steps.some((s) => s.state === "running"),
  };
}

/** The row label of a group, for example "Searched the web (3)" or "Ran 4 tools". */
export function groupLabel(
  family: StepFamily,
  nodes: readonly ProgressNode[],
): string {
  const n = nodes.length;
  const reading = nodes.every((node) => /^reading\b/i.test(node.step.label));
  switch (family) {
    case "web":
      return `Searched the web (${n})`;
    case "notion":
      return `${reading ? "Read" : "Used"} Notion (${n})`;
    case "github":
      return `${reading ? "Read" : "Used"} GitHub (${n})`;
    case "mail":
      return `Read your mail (${n})`;
    case "memory":
      return `Used memory (${n})`;
    case "document":
      return `Worked on documents and files (${n})`;
    case "tool":
      return `Ran ${n} tools`;
  }
}

/** Milliseconds the top-level steps report, summed; null when none reports a time. */
export function stepsElapsedMs(
  steps: readonly ChatProgressStep[],
): number | null {
  const timed = steps.filter(
    (s) => s.parentId === null && s.elapsedMs !== null,
  );
  if (timed.length === 0) return null;
  return timed.reduce((sum, s) => sum + (s.elapsedMs ?? 0), 0);
}

/**
 * The wall-clock time of the reply: the server's measurement when it sent one, otherwise the sum
 * of the step times, otherwise null (unknown).
 */
export function replyElapsedMs(
  steps: readonly ChatProgressStep[],
  usage: ChatUsage | null | undefined,
): number | null {
  const wall = usage?.elapsed_ms;
  if (typeof wall === "number" && wall > 0) return wall;
  return stepsElapsedMs(steps);
}

/**
 * The header of the work log. While the reply runs it says "Working" (the live timer sits beside
 * it); afterwards it says how it ended and how long it took.
 */
export function workLogTitle(
  steps: readonly ChatProgressStep[],
  replyState: ProgressReplyState,
  elapsedMs: number | null,
): string {
  if (replyState === "live") return "Working";
  const base =
    replyState === "interrupted"
      ? "Interrupted"
      : replyState === "stopped"
        ? "Stopped"
        : countFailed(steps) > 0
          ? "Completed with errors"
          : "Completed";
  return elapsedMs !== null && elapsedMs > 0
    ? `${base} in ${formatElapsed(elapsedMs)}`
    : base;
}

function plural(count: number, one: string, many: string): string {
  return `${count} ${count === 1 ? one : many}`;
}

/** Tokens per second as people read it: one decimal below 10, whole numbers above. */
function formatRate(rate: number): string {
  return rate < 10 ? rate.toFixed(1) : String(Math.round(rate));
}

/**
 * The quiet footer figures, only those that are real: elapsed time, steps, model turns, output
 * tokens per second (provider usage and a measured time, never an estimate) and the share of the
 * prompt the provider served from its cache (only when it reported cached tokens).
 */
export function workLogFooter(
  steps: readonly ChatProgressStep[],
  usage: ChatUsage | null | undefined,
  elapsedMs: number | null,
): string[] {
  const out: string[] = [];
  if (elapsedMs !== null && elapsedMs > 0) out.push(formatElapsed(elapsedMs));
  const stepCount = steps.filter(
    (s) => s.kind !== "note" && s.kind !== "think",
  ).length;
  if (stepCount > 0) out.push(plural(stepCount, "step", "steps"));
  if (usage === null || usage === undefined) return out;
  if (typeof usage.turns === "number" && usage.turns > 0) {
    out.push(plural(usage.turns, "turn", "turns"));
  }
  if (!usage.is_estimate) {
    const wall = usage.elapsed_ms;
    if (typeof wall === "number" && wall > 0 && usage.candidates_tokens > 0) {
      out.push(`${formatRate(usage.candidates_tokens / (wall / 1000))} tok/s`);
    }
    const cached = usage.cached_tokens;
    if (typeof cached === "number" && cached > 0 && usage.prompt_tokens > 0) {
      const share = Math.min(
        100,
        Math.round((cached / usage.prompt_tokens) * 100),
      );
      out.push(`${share}% cached`);
    }
  }
  return out;
}

/**
 * Reasoning summaries arrive as Markdown (`**Heading**` then a paragraph). Older stored ones lost
 * their line breaks, so a bold heading glued into a sentence is put back on its own line. Text
 * that already has line breaks is left alone.
 */
export function prepareThought(text: string): string {
  if (text.includes("\n")) return text;
  return text
    .replace(/\s*(\*\*[^*\n]{2,80}\*\*)\s+/g, "\n\n$1\n\n")
    .replace(/^\n+/, "")
    .trim();
}

/** Shown when no step says what MAYA is doing, so the header never invents a phase. */
export const WORKING_FALLBACK = "Working on it";

type PhaseStep = Pick<ChatProgressStep, "kind" | "label" | "state">;

const HOUSEKEEPING_LABEL =
  /^(?:planning the reply|preparing the reply|context injection|injecting context|loading context|preparing context)\b/i;

/**
 * Internal housekeeping (planning the reply, injecting context): not something a person needs to
 * read. It is tucked behind "Show background steps" unless it failed.
 */
export function isHousekeeping(
  step: Pick<ChatProgressStep, "label" | "state">,
): boolean {
  return step.state !== "failed" && HOUSEKEEPING_LABEL.test(step.label.trim());
}

const ANSWER_LABEL =
  /^(?:answer|responding|writing (?:the|your) (?:answer|reply|response)|(?:drafting|composing) (?:the|your) (?:answer|reply|response))\b/i;

/**
 * The human phase one step stands for, for example "Searching the web" or "Building the
 * document". Unknown steps (a task's own step names, new kinds) give the generic fallback.
 */
export function phaseTitle(step: PhaseStep): string {
  const label = step.label.trim().toLowerCase();
  if (step.kind === "plan" || HOUSEKEEPING_LABEL.test(label)) {
    return "Analyzing the request";
  }
  if (ANSWER_LABEL.test(label)) return "Writing the answer";
  if (step.kind === "think") return "Thinking it through";
  const family = stepFamily(step);
  switch (family) {
    case "web":
      return label === "reading a web page"
        ? "Reading a web page"
        : "Searching the web";
    case "notion":
      return /^reading\b/.test(label)
        ? "Reading your Notion page"
        : "Working in Notion";
    case "github":
      return "Checking GitHub";
    case "mail":
      return "Reading your mail";
    case "memory":
      return /^(?:saving|updating)\b/.test(label)
        ? "Updating your memory"
        : "Checking your memory";
    case "document":
      return /sheet/.test(label)
        ? "Working on the sheet"
        : "Building the document";
    case "tool":
      return "Running a tool";
    case null:
      break;
  }
  switch (step.kind) {
    case "search":
      return "Searching";
    case "email":
      return "Preparing an email";
    case "image":
      return "Creating an image";
    case "render":
      return "Building the document";
    case "save":
      return "Saving your work";
    case "check":
      return "Checking the result";
    case "repair":
      return "Fixing a detail";
    default:
      return WORKING_FALLBACK;
  }
}

/**
 * The phase of the reply right now: the newest running step, else the newest step that is not a
 * note. `waiting` is true while a step waits for the user's approval (no ticking status then).
 */
export function currentPhase(steps: readonly ChatProgressStep[]): {
  readonly title: string;
  readonly waiting: boolean;
} {
  const real = steps.filter((s) => s.kind !== "note");
  if (real.some((s) => isApprovalWait(s.detail) && s.state === "skipped")) {
    return { title: "Waiting for your approval", waiting: true };
  }
  for (let i = real.length - 1; i >= 0; i -= 1) {
    if (real[i].state === "running") {
      return { title: phaseTitle(real[i]), waiting: false };
    }
  }
  return { title: WORKING_FALLBACK, waiting: false };
}

/** Whole seconds as the live status line shows them: "3s", "1m 5s". */
export function formatLiveSeconds(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  return s < 60 ? `${s}s` : `${Math.floor(s / 60)}m ${s % 60}s`;
}

/**
 * The live status line: "Searching the web for 2s …". Under one second there is no figure yet.
 * The ellipsis is static text, nothing animates.
 */
export function statusLineText(title: string, seconds: number): string {
  return seconds >= 1
    ? `${title} for ${formatLiveSeconds(seconds)} …`
    : `${title} …`;
}

/**
 * The heading of a finished work log. A completed reply reads "Worked for 3 s · 4 steps" (the
 * time and the failure count only when real); stopped and interrupted replies keep their words.
 */
export function finishedHeading(
  steps: readonly ChatProgressStep[],
  replyState: ProgressReplyState,
  elapsedMs: number | null,
): string {
  if (replyState === "stopped" || replyState === "interrupted") {
    return workLogTitle(steps, replyState, elapsedMs);
  }
  const count = steps.filter(
    (s) => s.kind !== "note" && s.kind !== "think",
  ).length;
  const parts = [
    elapsedMs !== null && elapsedMs >= 1000
      ? `Worked for ${formatElapsed(elapsedMs)}`
      : "Worked",
  ];
  if (count > 0) parts.push(plural(count, "step", "steps"));
  const failed = countFailed(steps);
  if (failed > 0) parts.push(`${failed} failed`);
  return parts.join(" · ");
}

/** Details that are internal ids or modes, not words for a person. */
const INTERNAL_DETAIL = new Set([
  "answer",
  "reply",
  "respond",
  "final",
  "plan",
  "context",
  "runtime",
  "runtime-context",
  "default",
  "none",
  "null",
  "undefined",
]);

/**
 * A step detail fit for a person: the model-instruction filter first, then bare internal tokens
 * ("answer", "runtime-context", snake_case or kebab-case slugs, ids) are dropped. Free text,
 * sentences, queries with spaces, domains and paths stay.
 */
export function humanizeDetail(
  detail: string | null | undefined,
): string | null {
  const text = userFacingDetail(detail);
  if (text === null) return null;
  const trimmed = text.trim();
  if (trimmed === "") return null;
  if (!/\s/.test(trimmed)) {
    const lower = trimmed.toLowerCase();
    if (INTERNAL_DETAIL.has(lower)) return null;
    if (/^[a-z0-9]+(?:[-_][a-z0-9]+)+$/i.test(trimmed)) return null;
    if (/^[0-9a-f]{8,}$/i.test(trimmed)) return null;
  }
  return trimmed;
}
