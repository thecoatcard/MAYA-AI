"use client";

import { ArrowDown } from "lucide-react";
import { useLayoutEffect, useRef, useState, type ReactNode } from "react";

import type {
  ChatMessage,
  ChatRetryInfo,
  ChatStatus,
} from "../../hooks/useChatStream";
import { MessageItem, PendingIndicator } from "./MessageItem";
import { usePrefersReducedMotion } from "./useReducedMotion";

export interface MessageThreadProps {
  messages: readonly ChatMessage[];
  status: ChatStatus;
  retrying?: ChatRetryInfo | null;
  /** Shown instead of messages when the conversation is empty and idle. */
  empty?: ReactNode;
  /**
   * Offered inside the note of the LAST message when it is an interrupted reply.
   * Leave it out when another control already offers Ask again.
   */
  onAskAgain?: () => void;
  onViewDeliverables?: () => void;
  onOpenTaskDetails?: (taskId: string) => void;
}

/** Within this distance of the end, the thread keeps following new text. */
const BOTTOM_THRESHOLD_PX = 32;

/**
 * One short sentence per state change for assistive technology. It changes when
 * a reply starts, ends or is cut short, never per streamed token, so screen
 * readers are not flooded while text arrives.
 */
function announcementFor(
  messages: readonly ChatMessage[],
  status: ChatStatus,
): string {
  if (status === "sending") {
    return "Sending message.";
  }
  const last = messages[messages.length - 1];
  if (last === undefined || last.role !== "assistant") {
    return "";
  }
  switch (last.state) {
    case "streaming":
      return "MAYA is replying.";
    case "complete":
      return "Reply complete.";
    case "interrupted":
      return "Reply interrupted.";
    case "stopped":
      return "Reply stopped.";
    default:
      return "";
  }
}

export function MessageThread({
  messages,
  status,
  retrying = null,
  empty = null,
  onAskAgain,
  onViewDeliverables,
  onOpenTaskDetails,
}: MessageThreadProps) {
  const scrollRef = useRef<HTMLDivElement>(null);
  const followRef = useRef(true);
  const lastUserKeyRef = useRef<string | null>(null);
  const [showJump, setShowJump] = useState(false);
  const reducedMotion = usePrefersReducedMotion();

  const handleScroll = () => {
    const element = scrollRef.current;
    if (element === null) {
      return;
    }
    const distance =
      element.scrollHeight - element.scrollTop - element.clientHeight;
    const atBottom = distance <= BOTTOM_THRESHOLD_PX;
    followRef.current = atBottom;
    setShowJump(!atBottom);
  };

  // Follow the newest text, unless the user scrolled up to read. Sending a
  // message always brings the view back to the end.
  useLayoutEffect(() => {
    const element = scrollRef.current;
    if (element === null) {
      return;
    }
    const last = messages[messages.length - 1];
    if (
      last !== undefined &&
      last.role === "user" &&
      last.key !== lastUserKeyRef.current
    ) {
      lastUserKeyRef.current = last.key;
      followRef.current = true;
    }
    if (followRef.current) {
      element.scrollTop = element.scrollHeight;
      setShowJump(false);
    }
  }, [messages, status]);

  const jumpToLatest = () => {
    const element = scrollRef.current;
    if (element === null) {
      return;
    }
    followRef.current = true;
    const behavior: ScrollBehavior = reducedMotion ? "auto" : "smooth";
    if (typeof element.scrollTo === "function") {
      element.scrollTo({ top: element.scrollHeight, behavior });
    } else {
      element.scrollTop = element.scrollHeight;
    }
    setShowJump(false);
  };

  const last = messages[messages.length - 1];
  const showPending =
    status === "sending" && (last === undefined || last.role === "user");
  const pendingLabel =
    retrying !== null
      ? `Retrying, attempt ${retrying.attempt} of ${retrying.maxAttempts}`
      : "Sending";

  return (
    <div className="maya-thread">
      {/* aria-live is off on purpose: the separate status region below speaks
          once per state change instead of once per streamed token. */}
      <div
        ref={scrollRef}
        className="maya-thread__scroll"
        role="log"
        aria-live="off"
        aria-label="Conversation"
        tabIndex={0}
        onScroll={handleScroll}
      >
        <div className="maya-thread__inner">
          {messages.length === 0 && status === "idle" ? empty : null}
          {messages.map((message, index) => (
            <MessageItem
              key={message.key}
              message={message}
              onAskAgain={
                index === messages.length - 1 ? onAskAgain : undefined
              }
              onViewDeliverables={onViewDeliverables}
              onOpenTaskDetails={onOpenTaskDetails}
            />
          ))}
          {showPending ? (
            <article className="maya-msg maya-msg--assistant">
              <h2 className="maya-sr-only">MAYA</h2>
              <div className="maya-msg__head" aria-hidden="true">
                <span className="maya-msg__mark" />
                <span className="maya-msg__who">MAYA</span>
              </div>
              <PendingIndicator label={pendingLabel} />
            </article>
          ) : null}
        </div>
      </div>
      {showJump ? (
        <button type="button" className="maya-jump" onClick={jumpToLatest}>
          <ArrowDown aria-hidden="true" size={16} />
          Jump to latest
        </button>
      ) : null}
      <div
        role="status"
        aria-live="polite"
        aria-atomic="true"
        className="maya-sr-only"
      >
        {announcementFor(messages, status)}
      </div>
    </div>
  );
}
