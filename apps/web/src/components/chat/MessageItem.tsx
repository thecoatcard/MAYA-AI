import {
  Info,
  Mic,
  OctagonX,
  TriangleAlert,
  Volume2,
  VolumeX,
} from "lucide-react";
import { memo, useCallback, useState, type ReactNode } from "react";

import type { ChatMessage } from "../../hooks/useChatStream";
import { speakVoiceText, stopVoiceSpeech } from "../../hooks/useLiveVoice";
import { LiveActionCard } from "../tasks/LiveActionCard";
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
  onViewDeliverables?: () => void;
  onOpenTaskDetails?: (taskId: string) => void;
}

function MessageItemImpl({
  message,
  onAskAgain,
  onViewDeliverables,
  onOpenTaskDetails,
}: MessageItemProps) {
  const [isSpeakingThis, setIsSpeakingThis] = useState<boolean>(false);

  const handleToggleSpeak = useCallback(() => {
    if (isSpeakingThis) {
      stopVoiceSpeech();
      setIsSpeakingThis(false);
    } else {
      setIsSpeakingThis(true);
      speakVoiceText(message.content, () => {
        setIsSpeakingThis(false);
      });
    }
  }, [isSpeakingThis, message.content]);

  if (message.role === "user") {
    return (
      <article className="maya-msg maya-msg--user" data-state={message.state}>
        <div className="flex items-center justify-between gap-2 mb-1">
          <h2 className="maya-sr-only">You said</h2>
          {message.modality === "voice" && (
            <span className="inline-flex items-center gap-1 text-[10px] font-mono text-teal-400 bg-teal-950/60 px-1.5 py-0.5 rounded border border-teal-500/30">
              <Mic className="w-3 h-3 text-teal-400" />
              Spoken Turn
            </span>
          )}
        </div>
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
      <div className="flex items-center justify-between gap-2 maya-msg__head">
        <div className="flex items-center gap-2" aria-hidden="true">
          <span className="maya-msg__mark" />
          <span className="maya-msg__who">MAYA</span>
          {message.modality === "voice" && (
            <span className="inline-flex items-center gap-1 text-[10px] font-mono text-blue-400 bg-blue-950/60 px-1.5 py-0.5 rounded border border-blue-500/30">
              Voice Mode
            </span>
          )}
        </div>

        {/* Listen to Maya Speech Button */}
        {message.content && !streaming && (
          <button
            type="button"
            onClick={handleToggleSpeak}
            className={`flex items-center gap-1 text-xs px-2 py-0.5 rounded border transition-colors ${
              isSpeakingThis
                ? "border-blue-500/50 bg-blue-500/10 text-blue-300 animate-pulse"
                : "border-zinc-800 bg-zinc-900/50 text-zinc-400 hover:text-zinc-200 hover:border-zinc-700"
            }`}
            title={isSpeakingThis ? "Stop speaking" : "Listen to Maya speak"}
            aria-label={
              isSpeakingThis ? "Stop speaking" : "Listen to Maya speak"
            }
          >
            {isSpeakingThis ? (
              <>
                <VolumeX className="w-3.5 h-3.5 text-blue-400" />
                <span className="text-[11px] font-mono">Stop</span>
              </>
            ) : (
              <>
                <Volume2 className="w-3.5 h-3.5" />
                <span className="text-[11px] font-mono">Speak</span>
              </>
            )}
          </button>
        )}
      </div>

      {streaming && message.content === "" ? (
        <PendingIndicator label="Thinking" />
      ) : (
        <MarkdownRenderer content={message.content} streaming={streaming} />
      )}

      {/* Live Tool Action DAG Card */}
      {message.toolAction && (
        <div className="mt-3">
          <LiveActionCard
            toolName={message.toolAction.toolName}
            taskId={message.toolAction.taskId}
            status={message.toolAction.status}
            intent={message.toolAction.intent}
            args={message.toolAction.args}
            result={message.toolAction.result}
            onViewDeliverables={onViewDeliverables}
            onOpenTaskDetails={onOpenTaskDetails}
          />
        </div>
      )}

      {assistantNote(message, onAskAgain)}
    </article>
  );
}

export const MessageItem = memo(MessageItemImpl);
