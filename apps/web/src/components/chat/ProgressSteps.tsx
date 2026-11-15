import {
  BookOpen,
  Bookmark,
  Brain,
  ChevronRight,
  CircleDot,
  FileText,
  GitBranch,
  Globe,
  Image as ImageIcon,
  ListChecks,
  Loader2,
  Mail,
  Minus,
  Search,
  Sparkles,
  ShieldCheck,
  Wrench,
  X,
  type LucideIcon,
} from "lucide-react";
import React, { useEffect, useId, useRef, useState } from "react";

import type {
  ChatProgressState,
  ChatProgressStep,
  ChatUsage,
} from "../../lib/api-client";
import {
  buildProgressTree,
  formatElapsed,
  isAdjusted,
  isApprovalWait,
  type ProgressNode,
  type ProgressReplyState,
} from "../../lib/progress-steps";
import {
  buildTimeline,
  currentPhase,
  finishedHeading,
  groupLabel,
  groupStatus,
  humanizeDetail,
  isHousekeeping,
  prepareThought,
  replyElapsedMs,
  statusLineText,
  stepFamily,
  workLogFooter,
  type StepFamily,
  type TimelineEntry,
} from "../../lib/work-log";
import { MarkdownRenderer } from "./MarkdownRenderer";
import "./worklog.css";

/** One icon size and stroke for the whole work log. */
const ICON = { size: 14, strokeWidth: 1.75 } as const;

const FAMILY_ICON: Readonly<Record<StepFamily, LucideIcon>> = {
  web: Globe,
  notion: BookOpen,
  github: GitBranch,
  mail: Mail,
  memory: Bookmark,
  document: FileText,
  tool: Wrench,
};

const KIND_ICON: Readonly<Record<string, LucideIcon>> = {
  plan: ListChecks,
  think: Brain,
  search: Search,
  email: Mail,
  image: ImageIcon,
  render: FileText,
  save: FileText,
  check: ShieldCheck,
  repair: Wrench,
};

/** The outline icon of a finished step: its tool family, else its kind, else a quiet dot. */
function stepKindIcon(step: ChatProgressStep): LucideIcon {
  const family = stepFamily(step);
  if (family !== null) return FAMILY_ICON[family];
  return KIND_ICON[step.kind] ?? CircleDot;
}

/** The step's visible state word. Running and done are announced only, so the list stays quiet. */
function StatusText({
  state,
  detail,
}: {
  readonly state: ChatProgressState;
  readonly detail?: string | null | undefined;
}) {
  switch (state) {
    case "running":
      return <span className="maya-sr-only">Working</span>;
    case "failed":
      return (
        <span className="maya-steps__status maya-steps__status--failed">
          Failed
        </span>
      );
    case "skipped":
      // A step paused for the user's approval is not skipped: say what it waits for.
      return (
        <span className="maya-steps__status">
          {isApprovalWait(detail)
            ? "Waiting for approval"
            : isAdjusted({ state, detail: detail ?? null })
              ? "Adjusted"
              : "Skipped"}
        </span>
      );
    case "done":
      return <span className="maya-sr-only">Done</span>;
  }
}

function StepIcon({ step }: { readonly step: ChatProgressStep }) {
  switch (step.state) {
    case "running":
      return (
        <Loader2
          aria-hidden="true"
          {...ICON}
          className="maya-steps__icon maya-steps__icon--running animate-spin motion-reduce:animate-none"
        />
      );
    case "done": {
      const Icon = stepKindIcon(step);
      return (
        <Icon
          aria-hidden="true"
          {...ICON}
          className="maya-steps__icon maya-steps__icon--done"
        />
      );
    }
    case "failed":
      return (
        <X
          aria-hidden="true"
          {...ICON}
          className="maya-steps__icon maya-steps__icon--failed"
        />
      );
    case "skipped":
      return (
        <Minus aria-hidden="true" {...ICON} className="maya-steps__icon" />
      );
  }
}

/**
 * The model's reasoning summary as a dropdown. While the model is thinking it is open and the text
 * streams in as it arrives (the box follows the newest line); when the step ends it folds shut and
 * the user can open it again. It is a summary the provider returns, not the raw chain of thought.
 */
function ThinkingItem({ step }: { readonly step: ChatProgressStep }) {
  const bodyId = useId();
  const running = step.state === "running";
  const text = step.thought ?? "";
  const hasText = text !== "";
  // null = follow the step (open while thinking, folded once done); a click overrides it.
  const [choice, setChoice] = useState<boolean | null>(null);
  const open = hasText && (choice ?? running);
  const box = useRef<HTMLDivElement | null>(null);
  useEffect(() => {
    if (running && open && box.current !== null) {
      box.current.scrollTop = box.current.scrollHeight;
    }
  }, [text, running, open]);

  const label = running
    ? "Thinking"
    : step.elapsedMs !== null
      ? `Thought for ${formatElapsed(step.elapsedMs)}`
      : "Thought";
  return (
    <li
      className="maya-steps__item maya-think"
      data-testid="progress-step"
      data-state={step.state}
      data-kind="think"
    >
      <button
        type="button"
        className="maya-think__toggle"
        aria-expanded={open}
        aria-controls={bodyId}
        disabled={!hasText}
        onClick={() => setChoice(!open)}
        data-testid="thinking-toggle"
      >
        {hasText ? (
          <ChevronRight
            aria-hidden="true"
            {...ICON}
            className="maya-wl__chevron maya-think__chevron"
            data-open={open ? "true" : "false"}
          />
        ) : (
          <StepIcon step={step} />
        )}
        <Brain
          aria-hidden="true"
          {...ICON}
          className={
            running
              ? "maya-think__icon maya-think__icon--live"
              : "maya-think__icon"
          }
        />
        <span
          className={
            running
              ? "maya-think__label maya-think__label--live"
              : "maya-think__label"
          }
        >
          {label}
        </span>
        {running && <span className="maya-sr-only">Working</span>}
      </button>
      {open && (
        <div
          id={bodyId}
          ref={box}
          role="region"
          aria-label="Reasoning summary"
          aria-live="off"
          className="maya-think__body"
          data-testid="thinking-body"
        >
          <p className="maya-think__caption">
            Summary of the model&apos;s reasoning
            {running ? ", as it arrives" : ""}
          </p>
          <div className="maya-think__text" dir="auto">
            <MarkdownRenderer content={prepareThought(text)} />
          </div>
        </div>
      )}
    </li>
  );
}

const DETAIL_PREVIEW_CHARS = 160;

/** A step detail: at most two lines, with a control for the rest. */
function StepDetail({
  text,
  failed,
}: {
  readonly text: string;
  readonly failed: boolean;
}) {
  const [open, setOpen] = useState(false);
  const long =
    text.length > DETAIL_PREVIEW_CHARS || text.split("\n").length > 2;
  const base = failed
    ? "maya-steps__detail maya-steps__detail--failed"
    : "maya-steps__detail";
  return (
    <>
      <p
        className={long && !open ? `${base} line-clamp-2` : base}
        data-testid="progress-step-detail"
      >
        {text}
      </p>
      {long && (
        <button
          type="button"
          className="maya-steps__more"
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? "Show less" : "Show more"}
        </button>
      )}
    </>
  );
}

/** A short detail beside the label ("Label · detail"); long ones and reasons go below it. */
const INLINE_DETAIL_CHARS = 72;

function StepItem({ node }: { readonly node: ProgressNode }) {
  const { step } = node;
  if (step.kind === "think") return <ThinkingItem step={step} />;
  const label = step.label !== "" ? step.label : step.kind;
  const detail = humanizeDetail(step.detail);
  const inline =
    detail !== null &&
    step.state !== "failed" &&
    !isAdjusted(step) &&
    detail.length <= INLINE_DETAIL_CHARS &&
    !detail.includes("\n");
  const hasCount = step.index !== null && step.total !== null;
  return (
    <li
      className="maya-steps__item"
      data-testid="progress-step"
      data-state={step.state}
      data-adjusted={isAdjusted(step) ? "true" : undefined}
      data-kind={step.kind}
    >
      <div className="maya-steps__row">
        <StepIcon step={step} />
        <div className="maya-steps__body">
          <div className="maya-steps__line">
            <span className="maya-steps__label">{label}</span>
            {inline && (
              <>
                <span className="maya-steps__sep" aria-hidden="true">
                  ·
                </span>
                <span
                  className="maya-steps__inline-detail"
                  data-testid="progress-step-detail"
                >
                  {detail}
                </span>
              </>
            )}
            {hasCount && (
              <span className="maya-steps__meta">{`${step.index} of ${step.total}`}</span>
            )}
            {step.elapsedMs !== null && step.elapsedMs >= 1000 && (
              <span className="maya-steps__meta maya-steps__elapsed">
                {formatElapsed(step.elapsedMs)}
              </span>
            )}
            <StatusText state={step.state} detail={step.detail} />
          </div>
          {detail !== null && !inline && (
            <StepDetail text={detail} failed={step.state === "failed"} />
          )}
        </div>
      </div>
      {node.children.length > 0 && (
        <ul className="maya-steps__children">
          {node.children.map((child) => (
            <StepItem key={child.step.stepId} node={child} />
          ))}
        </ul>
      )}
    </li>
  );
}

/** The model's own words between two tool calls, in the place where it said them. */
function NoteItem({ step }: { readonly step: ChatProgressStep }) {
  return (
    <li className="maya-wl__note" data-testid="progress-note">
      <p className="maya-wl__note-text" dir="auto">
        {step.note ?? ""}
      </p>
    </li>
  );
}

/**
 * Consecutive steps of one family as one row: a count, a flag when one failed, and the steps
 * themselves one click away. A group with a failed step opens by default so the failure is seen.
 */
function GroupItem({
  family,
  nodes,
}: {
  readonly family: StepFamily;
  readonly nodes: readonly ProgressNode[];
}) {
  const bodyId = useId();
  const { failed, running } = groupStatus(nodes);
  const [choice, setChoice] = useState<boolean | null>(null);
  const open = choice ?? failed > 0;
  const Icon = FAMILY_ICON[family];
  const label = groupLabel(family, nodes);
  return (
    <li
      className="maya-steps__item maya-wl__group"
      data-testid="progress-group"
      data-family={family}
      data-failed={failed > 0 ? "true" : undefined}
      data-state={running ? "running" : failed > 0 ? "failed" : "done"}
    >
      <button
        type="button"
        className="maya-wl__row"
        aria-expanded={open}
        aria-controls={bodyId}
        onClick={() => setChoice(!open)}
        data-testid="progress-group-toggle"
      >
        <ChevronRight
          aria-hidden="true"
          {...ICON}
          className="maya-wl__chevron"
          data-open={open ? "true" : "false"}
        />
        <Icon aria-hidden="true" {...ICON} className="maya-wl__family-icon" />
        <span className="maya-wl__row-label">{label}</span>
        {failed > 0 && (
          <span className="maya-wl__chip maya-wl__chip--failed">
            <X aria-hidden="true" {...ICON} />
            {`${failed} failed`}
          </span>
        )}
        {running && <span className="maya-wl__chip">Working</span>}
      </button>
      {open && (
        <ul id={bodyId} className="maya-wl__group-list" aria-label={label}>
          {nodes.map((node) => (
            <StepItem key={node.step.stepId} node={node} />
          ))}
        </ul>
      )}
    </li>
  );
}

export interface ProgressStepsProps {
  readonly steps: readonly ChatProgressStep[];
  /** "live" while the reply streams; the others once it has ended. */
  readonly replyState: ProgressReplyState;
  /** Usage the server reported for the reply; the footer shows only what it really contains. */
  readonly usage?: ChatUsage | null;
}

/** Whole seconds since the reply began streaming, ticking once a second while it is live. */
function useLiveElapsed(live: boolean): number {
  const [seconds, setSeconds] = useState(0);
  useEffect(() => {
    if (!live) return undefined;
    const startedAt = Date.now();
    const timer = setInterval(
      () => setSeconds(Math.floor((Date.now() - startedAt) / 1000)),
      1000,
    );
    return () => clearInterval(timer);
  }, [live]);
  return seconds;
}

/** Rows that appear in the list, as the timeline renders them. */
function TimelineRows({
  entries,
}: {
  readonly entries: readonly TimelineEntry[];
}) {
  return (
    <>
      {entries.map((entry) => {
        switch (entry.type) {
          case "note":
            return <NoteItem key={entry.step.stepId} step={entry.step} />;
          case "group":
            return (
              <GroupItem
                key={entry.nodes[0].step.stepId}
                family={entry.family}
                nodes={entry.nodes}
              />
            );
          case "node":
            return <StepItem key={entry.node.step.stepId} node={entry.node} />;
        }
      })}
    </>
  );
}

/** A top-level step that is internal housekeeping and did not fail (kept behind a control). */
function isBackground(entry: TimelineEntry): boolean {
  return (
    entry.type === "node" &&
    isHousekeeping(entry.node.step) &&
    entry.node.children.length === 0
  );
}

/**
 * The work log under an assistant reply. The header is the current phase in plain words ("Searching
 * the web") and a chevron; below it a tidy list of rows (icon, label, muted detail), the model's
 * narration between them, consecutive steps of one family grouped, and the reasoning dropdown in
 * the same list. While the reply streams the section is open and one accent status line at the
 * bottom ticks the seconds; once it has ended the section folds to one quiet line ("Worked for
 * 3 s · 4 steps") and a footer of real figures shows when it is open.
 */
export function ProgressSteps({
  steps,
  replyState,
  usage = null,
}: ProgressStepsProps): React.JSX.Element | null {
  const listId = useId();
  const live = replyState === "live";
  const elapsed = useLiveElapsed(live);
  const [expanded, setExpanded] = useState<boolean>(live);
  const [showBackground, setShowBackground] = useState(false);
  const [wasLive, setWasLive] = useState<boolean>(live);
  if (live !== wasLive) {
    // The reply ended (or a new one started): collapse or expand to match.
    setWasLive(live);
    setExpanded(live);
  }
  if (steps.length === 0) return null;

  const timeline = buildTimeline(buildProgressTree(steps));
  const visible = timeline.filter((entry) => !isBackground(entry));
  const background = timeline.filter(isBackground);
  const elapsedMs = replyElapsedMs(steps, usage);
  const phase = currentPhase(steps);
  const title = live
    ? phase.title
    : finishedHeading(steps, replyState, elapsedMs);
  const footer = live ? [] : workLogFooter(steps, usage, elapsedMs);
  return (
    <div
      className="maya-steps maya-wl"
      data-testid="progress-steps"
      data-live={live ? "true" : undefined}
    >
      <button
        type="button"
        className="maya-steps__toggle maya-wl__head"
        aria-expanded={expanded}
        aria-controls={listId}
        onClick={() => setExpanded((value) => !value)}
      >
        <ChevronRight
          aria-hidden="true"
          {...ICON}
          className="maya-wl__chevron"
          data-open={expanded ? "true" : "false"}
        />
        <span className="maya-steps__summary">{title}</span>
      </button>
      {expanded && (
        <ul
          id={listId}
          className="maya-steps__list maya-wl__list"
          aria-label="Steps"
        >
          <TimelineRows entries={visible} />
          {background.length > 0 && (
            <li className="maya-wl__background">
              <button
                type="button"
                className="maya-wl__more"
                aria-expanded={showBackground}
                onClick={() => setShowBackground((value) => !value)}
                data-testid="progress-background-toggle"
              >
                {showBackground
                  ? "Hide background steps"
                  : `Show background steps (${background.length})`}
              </button>
            </li>
          )}
          {showBackground && <TimelineRows entries={background} />}
        </ul>
      )}
      {live && !phase.waiting && (
        <div className="maya-wl__status" data-testid="progress-status">
          {/* Only the phase is announced, and only when it changes; the seconds never are. */}
          <span className="maya-sr-only" role="status" aria-live="polite">
            {phase.title}
          </span>
          <span aria-hidden="true" className="maya-wl__status-inner">
            <span className="maya-wl__pulse animate-pulse motion-reduce:animate-none" />
            <Sparkles {...ICON} className="maya-wl__status-icon" />
            <span data-testid="progress-timer" className="maya-wl__status-text">
              {statusLineText(phase.title, elapsed)}
            </span>
          </span>
        </div>
      )}
      {expanded && footer.length > 0 && (
        <p className="maya-wl__footer" data-testid="progress-footer">
          {footer.join(" · ")}
        </p>
      )}
    </div>
  );
}
