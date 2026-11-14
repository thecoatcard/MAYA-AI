import {
  BookOpenText,
  Brain,
  Check,
  ClipboardCopy,
  Copy,
  CopyPlus,
  ExternalLink,
  FileDown,
  Hash,
  Info,
  Languages,
  Link2,
  Mail,
  Mic,
  MoreHorizontal,
  NotebookPen,
  OctagonX,
  Pencil,
  Quote,
  RefreshCw,
  Save,
  Search,
  Sparkles,
  ThumbsDown,
  ThumbsUp,
  TriangleAlert,
  Volume2,
  VolumeX,
} from "lucide-react";
import {
  memo,
  useCallback,
  useEffect,
  useRef,
  useState,
  type ReactNode,
} from "react";
import {
  copyText,
  openLink,
  quoteText,
  selectionPrompt,
  type SelectionAction,
} from "../../lib/menu-actions";
import { MenuButton, useContextMenu, type ContextMenuEntry } from "../ui";
import { useConversationRef } from "./ConversationContext";

import type { ChatMessage, ChatMessageState } from "../../hooks/useChatStream";
import { speakVoiceText, stopVoiceSpeech } from "../../hooks/useLiveVoice";
import { AttachmentChip } from "../files/FileParts";
import { MarkdownRenderer } from "./MarkdownRenderer";
import { AgentDraftCard } from "./AgentDraftCard";
import { ScheduleDraftCard } from "./ScheduleDraftCard";
import { MemorySavedChip } from "./MemorySavedChip";
import { FileCreatedCard } from "./FileCreatedCard";
import { ToolProgressChip } from "./ToolProgressChip";
import { ProgressSteps } from "./ProgressSteps";
import type { ProgressReplyState } from "../../lib/progress-steps";
import { AskUserCard, type AskUserCardProps } from "./AskUserCard";

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

/**
 * A quiet inline meta chip: icon plus text, never colour alone, and only as wide as its text.
 * Shown only for turns that really were spoken (modality voice, or a live voice turn).
 */
export function SpokenMarker() {
  return (
    <span className="maya-chip" data-testid="spoken-marker">
      <Mic aria-hidden="true" size={12} />
      Spoken
    </span>
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

/** Maps the reply state to the step list's summary state. */
function progressReplyState(state: ChatMessageState): ProgressReplyState {
  switch (state) {
    case "complete":
      return "complete";
    case "interrupted":
      return "interrupted";
    case "stopped":
      return "stopped";
    default:
      return "live";
  }
}

function formatMessageTime(timestamp?: number): string | null {
  if (
    typeof timestamp !== "number" ||
    Number.isNaN(timestamp) ||
    timestamp <= 0
  ) {
    return null;
  }
  try {
    const date = new Date(timestamp);
    return new Intl.DateTimeFormat([], {
      hour: "numeric",
      minute: "2-digit",
    }).format(date);
  } catch {
    return null;
  }
}

/** The selected text when it lies inside `root`, else "". */
export function readSelectionIn(root: HTMLElement | null): string {
  if (root === null || typeof window === "undefined") return "";
  const selection = window.getSelection();
  if (selection === null || selection.isCollapsed || selection.rangeCount === 0)
    return "";
  const range = selection.getRangeAt(0);
  if (!root.contains(range.commonAncestorContainer)) return "";
  return selection.toString().trim();
}

export interface MessageItemProps {
  message: ChatMessage;
  /** Offered inside an interrupted reply's note; omitted when not available. */
  onAskAgain?: () => void;
  /** The message was spoken (modality voice). */
  spoken?: boolean;
  /** How to show this reply's question card (ask_user); omitted: the card is not shown. */
  ask?: Omit<AskUserCardProps, "request">;
}

function MessageItemImpl({
  message,
  onAskAgain,
  spoken = false,
  ask,
}: MessageItemProps) {
  const [isSpeakingThis, setIsSpeakingThis] = useState<boolean>(false);
  const [copied, setCopied] = useState<boolean>(false);
  const copyTimeoutRef = useRef<ReturnType<typeof setTimeout> | null>(null);
  const articleRef = useRef<HTMLElement>(null);
  const runtimeRef = useConversationRef();
  const [feedback, setFeedback] = useState<"up" | "down" | null>(
    message.feedback ?? null,
  );

  useEffect(() => {
    return () => {
      if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current);
    };
  }, []);

  const handleCopy = useCallback((textToCopy: string) => {
    void copyText(textToCopy);
    setCopied(true);
    if (copyTimeoutRef.current) clearTimeout(copyTimeoutRef.current);
    copyTimeoutRef.current = setTimeout(() => {
      setCopied(false);
    }, 1500);
  }, []);

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

  const rateReply = (value: "up" | "down"): void => {
    const rt = runtimeRef.current;
    if (rt?.conversationActions === undefined || message.id === null) return;
    const before = feedback;
    const next = before === value ? null : value;
    setFeedback(next); // optimistic; rolled back when the server refuses
    void rt.conversationActions.feedback(message.id, next).then((ok) => {
      if (!ok) setFeedback(before);
    });
  };

  const getItems = ({
    target,
  }: {
    readonly target: Element | null;
  }): readonly ContextMenuEntry[] | null => {
    const rt = runtimeRef.current;
    const voiceBlocked = rt?.voiceLive === true;
    const sendReason = voiceBlocked
      ? "Not available during a live voice session."
      : message.id === null
        ? "Available once the reply is saved."
        : message.state !== "complete"
          ? "Available once the reply is complete."
          : undefined;
    const send = (text: string): (() => void) => {
      return () => {
        if (message.id !== null)
          void rt?.sendText(text, { sourceMessageId: message.id });
      };
    };

    // A link or a [n] citation.
    const anchor = target?.closest("a[href]") ?? null;
    if (anchor !== null) {
      const href = anchor.getAttribute("href") ?? "";
      const cite = anchor.closest(".maya-md__cite") !== null;
      return [
        {
          id: "open-link",
          label: cite ? "Open source" : "Open link",
          icon: <Link2 size={14} />,
          onSelect: () => openLink(href),
        },
        {
          id: "copy-link",
          label: "Copy link",
          icon: <ExternalLink size={14} />,
          onSelect: () => void copyText(href),
        },
      ];
    }

    // Selected text inside this message (not when opened from the "..." button).
    const fromButton = target?.closest("button") != null;
    const sel = fromButton ? "" : readSelectionIn(articleRef.current);
    if (sel !== "") {
      const ask = (action: SelectionAction) => (): void => {
        void rt?.sendText(selectionPrompt(action, sel));
      };
      const canSend = rt !== null && !voiceBlocked;
      const reason = canSend ? undefined : "Not available right now.";
      return [
        {
          id: "sel-copy",
          label: "Copy",
          icon: <Copy size={14} />,
          onSelect: () => void copyText(sel),
        },
        {
          id: "sel-quote",
          label: "Quote in reply",
          icon: <Quote size={14} />,
          disabled: rt?.prefillComposer === undefined,
          disabledReason: "No message box here.",
          onSelect: () =>
            rt?.prefillComposer?.(quoteText(sel), { append: true }),
        },
        { kind: "separator" },
        {
          id: "sel-explain",
          label: "Explain",
          icon: <Sparkles size={14} />,
          disabled: !canSend,
          disabledReason: reason,
          onSelect: ask("explain"),
        },
        {
          id: "sel-translate",
          label: "Translate",
          icon: <Languages size={14} />,
          disabled: !canSend,
          disabledReason: reason,
          children: [
            { id: "sel-hi", label: "Hindi", onSelect: ask("translate-hi") },
            { id: "sel-en", label: "English", onSelect: ask("translate-en") },
          ],
        },
        {
          id: "sel-summarize",
          label: "Summarize",
          icon: <BookOpenText size={14} />,
          disabled: !canSend,
          disabledReason: reason,
          onSelect: ask("summarize"),
        },
        {
          id: "sel-search",
          label: "Search the web",
          icon: <Search size={14} />,
          disabled: !canSend,
          disabledReason: reason,
          onSelect: ask("search"),
        },
        {
          id: "sel-remember",
          label: "Remember",
          icon: <NotebookPen size={14} />,
          disabled: !canSend,
          disabledReason: reason,
          onSelect: ask("remember"),
        },
        { kind: "separator" },
        {
          id: "sel-speak",
          label: "Speak",
          icon: <Volume2 size={14} />,
          onSelect: () => speakVoiceText(sel, () => undefined),
        },
      ];
    }

    if (message.role === "user") {
      return [
        {
          id: "copy",
          label: "Copy",
          icon: <Copy size={14} />,
          onSelect: () => void copyText(message.content),
        },
        {
          id: "edit",
          label: "Edit and resend",
          icon: <Pencil size={14} />,
          disabled: rt?.prefillComposer === undefined,
          disabledReason: "No message box here.",
          onSelect: () => rt?.prefillComposer?.(message.content),
        },
        {
          id: "quote",
          label: "Quote in reply",
          icon: <Quote size={14} />,
          disabled: rt?.prefillComposer === undefined,
          disabledReason: "No message box here.",
          onSelect: () =>
            rt?.prefillComposer?.(quoteText(message.content), { append: true }),
        },
      ];
    }

    const actions = rt?.conversationActions;
    const hidden = actions?.unsupported ?? new Set<string>();
    const canAct = rt !== null && sendReason === undefined;
    const rendered =
      articleRef.current?.querySelector(".maya-md")?.textContent ??
      message.content;
    const items: ContextMenuEntry[] = [
      {
        id: "copy",
        label: "Copy",
        icon: <Copy size={14} />,
        onSelect: () => void copyText(rendered),
      },
      {
        id: "copy-md",
        label: "Copy as Markdown",
        icon: <ClipboardCopy size={14} />,
        onSelect: () => void copyText(message.content),
      },
    ];
    if (message.content !== "" && message.state !== "streaming") {
      items.push({
        id: "speak",
        label: isSpeakingThis ? "Stop speaking" : "Speak",
        icon: isSpeakingThis ? <VolumeX size={14} /> : <Volume2 size={14} />,
        onSelect: handleToggleSpeak,
      });
    }
    if (onAskAgain !== undefined) {
      items.push({
        id: "again",
        label: "Ask again",
        icon: <RefreshCw size={14} />,
        onSelect: onAskAgain,
      });
    }
    if (rt !== null) {
      const flag = { disabled: !canAct, disabledReason: sendReason };
      items.push(
        { kind: "separator" },
        {
          id: "file",
          label: "Make a file",
          icon: <FileDown size={14} />,
          ...flag,
          children: [
            { id: "pdf", label: "PDF", onSelect: send("Make this a PDF.") },
            {
              id: "docx",
              label: "Word",
              onSelect: send("Make this a Word document."),
            },
            {
              id: "pptx",
              label: "Slides",
              onSelect: send("Make this a slide deck."),
            },
            {
              id: "xlsx",
              label: "Sheet",
              onSelect: send("Make this a spreadsheet."),
            },
          ],
        },
        {
          id: "notion",
          label: "Save to Notion as it is",
          icon: <Save size={14} />,
          ...flag,
          onSelect: send("Save this to Notion as it is."),
        },
        {
          id: "remember",
          label: "Remember this",
          icon: <NotebookPen size={14} />,
          ...flag,
          onSelect: send("Remember this."),
        },
        {
          id: "email",
          label: "Send by email",
          icon: <Mail size={14} />,
          ...flag,
          onSelect: send("Email this."),
        },
      );
    }
    if (
      actions !== undefined &&
      !hidden.has("feedback") &&
      message.id !== null
    ) {
      items.push(
        { kind: "separator" },
        {
          id: "good",
          label: "Good answer",
          icon: <ThumbsUp size={14} />,
          checked: feedback === "up",
          onSelect: () => rateReply("up"),
        },
        {
          id: "bad",
          label: "Bad answer",
          icon: <ThumbsDown size={14} />,
          checked: feedback === "down",
          onSelect: () => rateReply("down"),
        },
      );
    }
    const tail: ContextMenuEntry[] = [];
    const sid = rt?.chat.sessionId ?? null;
    if (
      actions !== undefined &&
      !hidden.has("duplicate") &&
      message.id !== null &&
      sid !== null
    ) {
      const mid = message.id;
      tail.push({
        id: "branch",
        label: "Duplicate from here",
        icon: <CopyPlus size={14} />,
        onSelect: () => void actions.duplicate(sid, mid),
      });
    }
    if (message.id !== null) {
      const mid = message.id;
      tail.push({
        id: "copy-id",
        label: "Copy message ID",
        icon: <Hash size={14} />,
        onSelect: () => void copyText(mid),
      });
    }
    if (tail.length > 0) items.push({ kind: "separator" }, ...tail);
    return items;
  };

  const menu = useContextMenu({
    getItems,
    label: message.role === "user" ? "Your message actions" : "Reply actions",
  });
  const moreButton = (
    <MenuButton
      label={
        message.role === "user" ? "More message actions" : "More reply actions"
      }
      buttonProps={menu.buttonProps}
    >
      <MoreHorizontal size={14} aria-hidden="true" />
    </MenuButton>
  );

  if (message.role === "user") {
    const timeStr = formatMessageTime(
      (message as { createdAt?: number }).createdAt,
    );
    return (
      <article
        ref={articleRef}
        {...menu.triggerProps}
        className="maya-msg maya-msg--user maya-reveal motion-message"
        data-state={message.state}
      >
        <span className="maya-sr-only">You said</span>
        {(spoken ||
          message.modality === "voice" ||
          message.mode === "deep_research") && (
          <div className="maya-msg__meta">
            {(spoken || message.modality === "voice") && <SpokenMarker />}
            {message.mode === "deep_research" && (
              <span className="maya-chip" data-testid="deep-research-badge">
                <Brain aria-hidden="true" size={12} />
                Deep research
              </span>
            )}
          </div>
        )}
        <p className="maya-msg__text" dir="auto">
          {message.content}
        </p>
        {(message.attachments ?? []).length > 0 && (
          <ul className="maya-msg__files" aria-label="Attached files">
            {(message.attachments ?? []).map((file) => (
              <li key={file.id} className="maya-composer__file">
                <AttachmentChip
                  name={file.name}
                  kind={file.kind}
                  status="sent"
                  downloadId={file.id}
                />
              </li>
            ))}
          </ul>
        )}
        {userNote(message)}
        <div className="maya-msg__tools" data-reveal="true">
          {timeStr !== null && (
            <time
              className="maya-msg__time"
              dateTime={new Date(
                (message as { createdAt?: number }).createdAt!,
              ).toISOString()}
            >
              {timeStr}
            </time>
          )}
          <div
            className="maya-msg__actions"
            role="toolbar"
            aria-label="Message actions"
          >
            <button
              type="button"
              className="maya-msg__pill"
              onClick={() => handleCopy(message.content)}
              title={copied ? "Copied" : "Copy"}
              aria-label={copied ? "Copied" : "Copy message"}
            >
              {copied ? (
                <Check size={13} aria-hidden="true" />
              ) : (
                <Copy size={13} aria-hidden="true" />
              )}
            </button>
            {runtimeRef.current?.prefillComposer !== undefined && (
              <button
                type="button"
                className="maya-msg__pill"
                onClick={() =>
                  runtimeRef.current?.prefillComposer?.(
                    quoteText(message.content),
                    { append: true },
                  )
                }
                title="Quote in reply"
                aria-label="Quote in reply"
              >
                <Quote size={13} aria-hidden="true" />
              </button>
            )}
            {moreButton}
          </div>
        </div>
        {menu.menu}
      </article>
    );
  }

  const streaming = message.state === "streaming";
  const actions = runtimeRef.current?.conversationActions;
  const hidden = actions?.unsupported ?? new Set<string>();
  const renderedContent =
    articleRef.current?.querySelector(".maya-md")?.textContent ??
    message.content;

  return (
    <article
      ref={articleRef}
      {...menu.triggerProps}
      className="maya-msg maya-msg--assistant maya-reveal motion-message"
      data-state={message.state}
      aria-busy={streaming}
    >
      <span className="maya-sr-only">MAYA said</span>
      <div className="maya-msg__head">
        <span className="maya-msg__mark" aria-hidden="true" />
        <span className="maya-msg__who" aria-hidden="true">
          MAYA
        </span>
        {(spoken || message.modality === "voice") && <SpokenMarker />}
        {feedback !== null && (
          <span
            className="maya-chip"
            data-testid="feedback-state"
            role="img"
            aria-label={
              feedback === "up"
                ? "Marked as a good answer"
                : "Marked as a bad answer"
            }
          >
            {feedback === "up" ? (
              <ThumbsUp size={12} aria-hidden="true" />
            ) : (
              <ThumbsDown size={12} aria-hidden="true" />
            )}
          </span>
        )}

        {/* Listen to Maya Speech Button */}
        {message.content && !streaming && (
          <button
            type="button"
            onClick={handleToggleSpeak}
            className="maya-msg__speak"
            title={isSpeakingThis ? "Stop speaking" : "Listen to Maya speak"}
            aria-label={
              isSpeakingThis ? "Stop speaking" : "Listen to Maya speak"
            }
          >
            {isSpeakingThis ? (
              <>
                <VolumeX size={14} aria-hidden="true" />
                <span>Stop</span>
              </>
            ) : (
              <>
                <Volume2 size={14} aria-hidden="true" />
                <span>Speak</span>
              </>
            )}
          </button>
        )}
      </div>

      {streaming && message.content === "" ? (
        // Once a step exists the work log is the live indicator (its Thinking row and status
        // line), so the separate three-dot placeholder would only duplicate it.
        (message.progress ?? []).length > 0 ? null : (
          <PendingIndicator label="Thinking" />
        )
      ) : message.inputRequest !== undefined &&
        message.content.trim() ===
          message.inputRequest.questions[0]?.question.trim() ? null : ( // the card shows it
        <MarkdownRenderer content={message.content} streaming={streaming} />
      )}

      {(message.progress ?? []).length > 0 && (
        <ProgressSteps
          steps={message.progress ?? []}
          replyState={progressReplyState(message.state)}
          usage={message.usage}
        />
      )}
      {message.inputRequest !== undefined && ask !== undefined && (
        <AskUserCard request={message.inputRequest} {...ask} />
      )}
      {(message.scheduleDraftIds ?? []).map((draftId) => (
        <ScheduleDraftCard key={draftId} draftId={draftId} />
      ))}
      {assistantNote(message, onAskAgain)}
      <ToolProgressChip tools={message.runningTools ?? []} />
      {(message.files ?? []).length > 0 && (
        <ul
          className="m-0 flex min-w-0 max-w-full list-none flex-col gap-1.5 p-0"
          aria-label="Files made in this reply"
        >
          {(message.files ?? []).map((file) => (
            <FileCreatedCard key={file.fileId} file={file} />
          ))}
        </ul>
      )}
      {(message.agentDrafts ?? []).map((draft) => (
        <AgentDraftCard key={draft.name} draft={draft} />
      ))}
      {(message.memoriesSaved ?? []).map((memory) => (
        <MemorySavedChip key={memory.memoryId} memory={memory} />
      ))}

      <div className="maya-msg__footer" data-reveal="true">
        <div
          className="maya-msg__actions"
          role="toolbar"
          aria-label="Reply actions"
        >
          {!streaming && message.content !== "" && (
            <>
              <button
                type="button"
                className="maya-msg__pill"
                onClick={() => handleCopy(renderedContent)}
                title={copied ? "Copied" : "Copy reply"}
                aria-label={copied ? "Copied" : "Copy reply"}
              >
                {copied ? (
                  <>
                    <Check size={13} aria-hidden="true" />
                    <span>Copied</span>
                  </>
                ) : (
                  <>
                    <Copy size={13} aria-hidden="true" />
                    <span>Copy</span>
                  </>
                )}
              </button>
              {runtimeRef.current?.prefillComposer !== undefined && (
                <button
                  type="button"
                  className="maya-msg__pill"
                  onClick={() =>
                    runtimeRef.current?.prefillComposer?.(
                      quoteText(renderedContent),
                      { append: true },
                    )
                  }
                  title="Quote in reply"
                  aria-label="Quote in reply"
                >
                  <Quote size={13} aria-hidden="true" />
                  <span>Quote</span>
                </button>
              )}
              {onAskAgain !== undefined && (
                <button
                  type="button"
                  className="maya-msg__pill"
                  onClick={onAskAgain}
                  title="Ask again"
                  aria-label="Ask again"
                >
                  <RefreshCw size={13} aria-hidden="true" />
                  <span>Retry</span>
                </button>
              )}
              {actions !== undefined &&
                !hidden.has("feedback") &&
                message.id !== null && (
                  <>
                    <button
                      type="button"
                      className="maya-msg__pill"
                      onClick={() => rateReply("up")}
                      aria-pressed={feedback === "up"}
                      data-active={feedback === "up"}
                      title={
                        feedback === "up" ? "Remove rating" : "Good answer"
                      }
                      aria-label={
                        feedback === "up"
                          ? "Remove good answer rating"
                          : "Good answer"
                      }
                    >
                      <ThumbsUp size={13} aria-hidden="true" />
                    </button>
                    <button
                      type="button"
                      className="maya-msg__pill"
                      onClick={() => rateReply("down")}
                      aria-pressed={feedback === "down"}
                      data-active={feedback === "down"}
                      title={
                        feedback === "down" ? "Remove rating" : "Bad answer"
                      }
                      aria-label={
                        feedback === "down"
                          ? "Remove bad answer rating"
                          : "Bad answer"
                      }
                    >
                      <ThumbsDown size={13} aria-hidden="true" />
                    </button>
                  </>
                )}
            </>
          )}
          {moreButton}
        </div>
      </div>

      {menu.menu}
    </article>
  );
}

export const MessageItem = memo(MessageItemImpl);
