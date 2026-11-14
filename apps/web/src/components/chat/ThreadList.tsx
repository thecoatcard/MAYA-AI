"use client";

import { ArrowDown, Info, TriangleAlert } from "lucide-react";
import React, {
  useEffect,
  useLayoutEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";

import type { ChatStatus } from "../../hooks/useChatStream";
import type {
  AskAnswerItem,
  ChatInputAnswer,
  ChatInputRequest,
} from "../../lib/ask-user";
import { TaskCard } from "../tasks/TaskCard";
import { MessageItem, PendingIndicator, SpokenMarker } from "./MessageItem";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { MemorySavedChip } from "./MemorySavedChip";
import type { ThreadItem } from "./threadItems";
import { usePrefersReducedMotion } from "./useReducedMotion";

const BOTTOM_THRESHOLD_PX = 32;

export interface ThreadListProps {
  readonly items: readonly ThreadItem[];
  readonly status: ChatStatus;
  readonly empty?: ReactNode;
  readonly onAskAgain?: () => void;
  /** A failed task was started again: add the new task's card to the thread. */
  readonly onTaskRetried?: (taskId: string) => void;
  readonly onTaskAborted?: (taskId: string) => void;
  /** A task card saw its task end: the result message is on its way into the thread. */
  readonly onTaskFinished?: (taskId: string) => void;
  /** An approval was decided on a task card: refresh the pending counters. */
  readonly onApprovalDecided?: (taskId: string) => void;
  /** The user answered a question card: send the answer as the next message. */
  readonly onAnswerInput?: (
    request: ChatInputRequest,
    answers: readonly AskAnswerItem[],
  ) => void;
  /** The user skipped a question card to type instead (move focus to the composer). */
  readonly onSkipInput?: () => void;
  /** A task card's missing-detail question was answered: send it as a chat message. */
  readonly onTaskDetail?: (
    taskId: string,
    title: string,
    question: string,
    answer: string,
  ) => void;
}

/** The answer a later user message gave to this card, if any. */
function answerFor(
  items: readonly ThreadItem[],
  from: number,
  askId: string,
): {
  state: "awaiting_input" | "answered" | "dismissed";
  answer: ChatInputAnswer | null;
} {
  for (let i = from + 1; i < items.length; i += 1) {
    const next = items[i];
    if (next?.kind === "message" && next.message.role === "user") {
      const given = next.message.inputAnswer;
      return given?.askId === askId
        ? { state: "answered", answer: given }
        : { state: "dismissed", answer: null };
    }
  }
  return { state: "awaiting_input", answer: null };
}

function VoiceTurnItem({
  item,
}: {
  item: Extract<ThreadItem, { kind: "voiceTurn" }>;
}) {
  const user = item.role === "user";
  if (item.markdown) {
    return (
      <article
        className="maya-msg motion-message maya-msg--assistant"
        data-state="final"
        data-testid="voice-turn"
      >
        <span className="maya-sr-only">MAYA put this in the chat</span>
        <div className="maya-msg__head">
          <span className="maya-msg__mark" aria-hidden="true" />
          <span className="maya-msg__who" aria-hidden="true">
            MAYA
          </span>
        </div>
        <MarkdownRenderer content={item.text} />
      </article>
    );
  }
  return (
    <article
      className={`maya-msg motion-message ${user ? "maya-msg--user" : "maya-msg--assistant"}`}
      data-state={item.isFinal ? "final" : "interim"}
      data-testid="voice-turn"
      aria-busy={!item.isFinal}
    >
      <span className="maya-sr-only">{user ? "You said" : "MAYA said"}</span>
      {!user && (
        <div className="maya-msg__head">
          <span className="maya-msg__mark" aria-hidden="true" />
          <span className="maya-msg__who" aria-hidden="true">
            MAYA
          </span>
          {item.source === "spoken" && <SpokenMarker />}
        </div>
      )}
      {user && item.source === "spoken" && (
        <div className="maya-msg__meta">
          <SpokenMarker />
        </div>
      )}
      <p
        dir="auto"
        className={`maya-msg__text ${item.isFinal ? "" : "text-maya-muted"}`}
        data-testid="voice-turn-text"
      >
        {item.text}
        {!item.isFinal && <span className="maya-caret" aria-hidden="true" />}
      </p>
    </article>
  );
}

function SystemNoteItem({
  item,
}: {
  item: Extract<ThreadItem, { kind: "systemNote" }>;
}) {
  const error = item.tone === "error";
  const Icon = error ? TriangleAlert : Info;
  return (
    <p
      className={`my-1 flex min-w-0 items-start gap-2 text-maya-small ${
        error ? "text-maya-danger" : "text-maya-muted"
      }`}
      role={error ? "alert" : "status"}
      data-testid="system-note"
    >
      <Icon size={14} aria-hidden="true" className="mt-1 shrink-0" />
      <span className="min-w-0 [overflow-wrap:anywhere]">{item.text}</span>
    </p>
  );
}

/**
 * Renders the single ordered thread. The container is a polite log: new items
 * are announced once, streamed tokens are not (the text itself is aria-busy
 * while it streams). Errors use role="alert" (assertive).
 */
export function ThreadList({
  items,
  status,
  empty = null,
  onAskAgain,
  onTaskRetried,
  onTaskAborted,
  onTaskFinished,
  onApprovalDecided,
  onAnswerInput,
  onSkipInput,
  onTaskDetail,
}: ThreadListProps): React.JSX.Element {
  const [skippedAsks, setSkippedAsks] = useState<ReadonlySet<string>>(
    new Set(),
  );
  const scrollRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);
  const [showJump, setShowJump] = useState(false);
  const [unreadCount, setUnreadCount] = useState(0);
  const prevItemsCountRef = useRef(items.length);
  // A finished task's card can be dismissed from the thread (the task itself is untouched).
  const [dismissedTasks, setDismissedTasks] = useState<ReadonlySet<string>>(
    new Set(),
  );
  const reducedMotion = usePrefersReducedMotion();

  useEffect(() => {
    if (!followRef.current) {
      const added = items.length - prevItemsCountRef.current;
      if (added > 0) {
        setUnreadCount((c) => c + added);
      }
    } else {
      setUnreadCount(0);
    }
    prevItemsCountRef.current = items.length;
  }, [items.length]);

  const handleScroll = () => {
    const el = scrollRef.current;
    if (el === null) return;
    const atBottom =
      el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_THRESHOLD_PX;
    followRef.current = atBottom;
    setShowJump(!atBottom);
    if (atBottom) {
      setUnreadCount(0);
    }
  };

  const lastId = items[items.length - 1]?.id;
  const lastText = (() => {
    const last = items[items.length - 1];
    if (last?.kind === "message") return last.message.content;
    if (last?.kind === "voiceTurn") return last.text;
    return "";
  })();

  useLayoutEffect(() => {
    const el = scrollRef.current;
    if (el === null) return;
    if (followRef.current) {
      el.scrollTop = el.scrollHeight;
      setShowJump(false);
    }
  }, [lastId, lastText, items.length, status]);

  // Content can grow after it was added (a table or code block settling, fonts loading, a task
  // card changing state). While the reader is at the bottom the view stays anchored there.
  const innerRef = useRef<HTMLDivElement>(null);
  useEffect(() => {
    const inner = innerRef.current;
    if (inner === null || typeof ResizeObserver === "undefined") return;
    const observer = new ResizeObserver(() => {
      const el = scrollRef.current;
      if (el !== null && followRef.current) el.scrollTop = el.scrollHeight;
    });
    observer.observe(inner);
    return () => observer.disconnect();
  }, []);

  const jump = () => {
    const el = scrollRef.current;
    if (el === null) return;
    followRef.current = true;
    setUnreadCount(0);
    if (typeof el.scrollTo === "function") {
      el.scrollTo({
        top: el.scrollHeight,
        behavior: reducedMotion ? "auto" : "smooth",
      });
    } else {
      el.scrollTop = el.scrollHeight;
    }
    setShowJump(false);
  };

  const last = items[items.length - 1];
  const showPending =
    status === "sending" &&
    (last === undefined ||
      (last.kind === "message" && last.message.role === "user"));

  return (
    <div className="maya-thread">
      <div
        ref={scrollRef}
        className="maya-thread__scroll"
        role="log"
        aria-live="polite"
        aria-relevant="additions"
        aria-label="Conversation"
        tabIndex={0}
        onScroll={handleScroll}
      >
        <div ref={innerRef} className="maya-thread__inner">
          {items.length === 0 && status === "idle" ? empty : null}
          {items.map((item, index) => {
            switch (item.kind) {
              case "message": {
                const request = item.message.inputRequest;
                const found =
                  request === undefined
                    ? null
                    : answerFor(items, index, request.askId);
                return (
                  <MessageItem
                    key={item.id}
                    message={item.message}
                    spoken={item.spoken}
                    ask={
                      request === undefined || found === null
                        ? undefined
                        : {
                            state:
                              found.state === "awaiting_input" &&
                              skippedAsks.has(request.askId)
                                ? "skipped"
                                : found.state,
                            answer: found.answer,
                            submitting: status === "sending",
                            onSubmit: (answers) =>
                              onAnswerInput?.(request, answers),
                            onSkip: () => {
                              setSkippedAsks((prev) =>
                                new Set(prev).add(request.askId),
                              );
                              onSkipInput?.();
                            },
                          }
                    }
                    onAskAgain={
                      item === last && item.message.state === "interrupted"
                        ? onAskAgain
                        : undefined
                    }
                  />
                );
              }
              case "voiceTurn":
                return <VoiceTurnItem key={item.id} item={item} />;
              case "systemNote":
                return <SystemNoteItem key={item.id} item={item} />;
              case "taskCard":
                if (dismissedTasks.has(item.taskId)) return null;
                return (
                  <TaskCard
                    key={item.id}
                    taskId={item.taskId}
                    fallbackTitle={item.title}
                    onRetried={onTaskRetried}
                    onAborted={onTaskAborted}
                    onFinished={onTaskFinished}
                    onApprovalDecided={onApprovalDecided}
                    onDetailAnswered={onTaskDetail}
                    onDismiss={(taskId) =>
                      setDismissedTasks((prev) => new Set(prev).add(taskId))
                    }
                  />
                );
              case "memoryChip":
                return (
                  <div key={item.id} className="min-w-0">
                    <MemorySavedChip memory={item.memory} />
                  </div>
                );
            }
          })}
          {showPending ? (
            <article className="maya-msg maya-msg--assistant motion-message">
              <PendingIndicator label="Sending" />
            </article>
          ) : null}
        </div>
      </div>
      {showJump ? (
        <button
          type="button"
          className="maya-jump"
          onClick={jump}
          aria-label={
            unreadCount > 0
              ? `Jump to latest (${unreadCount} unread)`
              : "Jump to latest"
          }
        >
          <ArrowDown aria-hidden="true" size={14} className="maya-jump__icon" />
          <span>Jump to latest</span>
          {unreadCount > 0 && (
            <span
              className="maya-jump__badge"
              data-testid="jump-unread-badge"
              aria-hidden="true"
            >
              {unreadCount}
            </span>
          )}
        </button>
      ) : null}
    </div>
  );
}
