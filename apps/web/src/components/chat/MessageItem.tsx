import { Info, OctagonX, TriangleAlert } from "lucide-react";
import { memo, type ReactNode } from "react";

import type { ChatMessage } from "../../hooks/useChatStream";
import { MarkdownRenderer } from "./MarkdownRenderer";

/** Three dots plus a text label, so the state never depends on motion. */
export function PendingIndicator({ label }: { label: string }) {
  return (
    <div className="maya-pending">
      <span className="maya-dots" aria-hidden="true">
        <i />
        <i />
        <i />
      </span>
      <span className="maya-pending__label">{label}</span>
    </div>
  );
}

function Note({
  tone,
  icon,
  action = null,
  children,
}: {
  tone: "neutral" | "error" | "warning";
  icon: ReactNode;
  /** An inline control that continues the sentence, such as "Ask again". */
  action?: ReactNode;
  children: ReactNode;
}) {
  return (
    <p className={`maya-msg__note maya-msg__note--${tone}`}>
      {icon}
      <span>
        {children}
        {action}
      </span>
    </p>
  );
}

function assistantNote(
  message: ChatMessage,
  onAskAgain: (() => void) | undefined,
): ReactNode {
  const hasText = message.content !== "";
  switch (message.state) {
    case "interrupted":
      return (
        <Note
          tone="error"
          icon={<TriangleAlert aria-hidden="true" size={14} />}
          action={
            onAskAgain !== undefined ? (
              <button
                type="button"
                className="maya-msg__action"
                onClick={onAskAgain}
              >
                Ask again
              </button>
            ) : null
          }
        >
          {hasText
            ? "Reply interrupted. The text above is what arrived."
            : "Reply interrupted before any text arrived."}
        </Note>
      );
    case "stopped":
      return (
        <Note tone="neutral" icon={<OctagonX aria-hidden="true" size={14} />}>
          {hasText
            ? "Stopped. The text above is what arrived."
            : "Stopped before a reply started."}
        </Note>
      );
    case "complete":
      return message.finishReason === "max_tokens" ? (
        <Note tone="neutral" icon={<Info aria-hidden="true" size={14} />}>
          The reply reached the length limit and was cut off.
        </Note>
      ) : null;
    default:
      return null;
  }
}

function userNote(message: ChatMessage): ReactNode {
  switch (message.state) {
    case "failed":
      return (
        <Note
          tone="error"
          icon={<TriangleAlert aria-hidden="true" size={14} />}
        >
          Not sent
        </Note>
      );
    case "unconfirmed":
      // The request went out but no answer arrived, so the server may have it.
      return (
        <Note
          tone="warning"
          icon={<TriangleAlert aria-hidden="true" size={14} />}
        >
          Not confirmed. The server may have received this message.
        </Note>
      );
    default:
      return null;
  }
}

export interface MessageItemProps {
  message: ChatMessage;
  /** Offered inside an interrupted reply's note; omitted when not available. */
  onAskAgain?: () => void;
}

function MessageItemImpl({ message, onAskAgain }: MessageItemProps) {
  if (message.role === "user") {
    return (
      <article className="maya-msg maya-msg--user" data-state={message.state}>
        <h2 className="maya-sr-only">You said</h2>
        <p className="maya-msg__text">{message.content}</p>
        {userNote(message)}
      </article>
    );
  }

  const streaming = message.state === "streaming";
  return (
    <article
      className="maya-msg maya-msg--assistant"
      data-state={message.state}
      aria-busy={streaming}
    >
      <h2 className="maya-sr-only">MAYA said</h2>
      <div className="maya-msg__head" aria-hidden="true">
        <span className="maya-msg__mark" />
        <span className="maya-msg__who">MAYA</span>
      </div>
      {streaming && message.content === "" ? (
        <PendingIndicator label="Thinking" />
      ) : (
        <MarkdownRenderer content={message.content} streaming={streaming} />
      )}
      {assistantNote(message, onAskAgain)}
    </article>
  );
}

export const MessageItem = memo(MessageItemImpl);
