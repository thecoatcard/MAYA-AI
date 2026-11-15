"use client";

import {
  ArrowUp,
  ClipboardPaste,
  Copy,
  Eraser,
  Paperclip,
  Scissors,
  Square,
  TextSelect,
  TriangleAlert,
  Type,
} from "lucide-react";
import {
  useEffect,
  useId,
  useLayoutEffect,
  useRef,
  type ChangeEvent,
  type ClipboardEvent,
  type KeyboardEvent,
  type ReactNode,
  type RefObject,
} from "react";

import { countCodePoints, validateChatContent } from "../../lib/api-client";
import type { AttachmentItem } from "../../hooks/useAttachments";
import { UPLOAD_ACCEPT } from "../../lib/files-client";
import { AttachmentChip } from "../files/FileParts";
import { copyText } from "../../lib/menu-actions";
import { useContextMenu } from "../ui/ContextMenu";

export interface ChatInputProps {
  value: string;
  onValueChange: (value: string) => void;
  /** Called with the text when the user sends. The parent clears `value`. */
  onSubmit: (text: string) => void;
  onStop: () => void;
  /** A reply is being written: the action button becomes Stop. */
  busy: boolean;
  disabled?: boolean;
  inputRef?: RefObject<HTMLTextAreaElement | null>;
  /** Rendered before the textarea: the mic orb button. */
  leading?: ReactNode;
  /** Rendered before the send button: secondary actions such as Run as task. */
  extraActions?: ReactNode;
  /** Replaces the keyboard hint line. */
  hint?: ReactNode;
  /** Files already chosen for this message. Passing `onAttachFiles` turns the attach control on. */
  attachments?: readonly AttachmentItem[];
  /** Called with files from the attach button or a paste. Files upload on select, not on send. */
  onAttachFiles?: (files: File[]) => void;
  onRemoveAttachment?: (key: string) => void;
  /** Why attaching is off right now (for example during a live voice session), or null. */
  attachDisabledReason?: string | null;
  /** A plain notice about the last attach (limits), or null. */
  attachNotice?: string | null;
  onDismissAttachNotice?: () => void;
}

export function ChatInput({
  value,
  onValueChange,
  onSubmit,
  onStop,
  busy,
  disabled = false,
  inputRef,
  leading,
  extraActions,
  hint,
  attachments = [],
  onAttachFiles,
  onRemoveAttachment,
  attachDisabledReason = null,
  attachNotice = null,
  onDismissAttachNotice,
}: ChatInputProps) {
  const fileInputRef = useRef<HTMLInputElement>(null);
  const uploading = attachments.some((a) => a.status === "uploading");
  const attachOff = attachDisabledReason !== null;
  const localRef = useRef<HTMLTextAreaElement>(null);
  const ref = inputRef ?? localRef;
  const composingRef = useRef(false);
  const metaId = useId();

  const count = countCodePoints(value);
  const problem = validateChatContent(value);
  const canSend = problem === null && !disabled && !uploading;

  // Grow with the text; CSS caps the height and scrolls beyond it.
  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null) {
      return;
    }
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight}px`;
  }, [value, ref]);

  // The height also depends on the width (text wraps differently): measure again when the field
  // becomes wider or narrower, for example when the pane is resized or the row reflows. Without
  // this a field measured while narrow keeps a tall, empty box.
  useEffect(() => {
    const element = ref.current;
    if (element === null || typeof ResizeObserver === "undefined") {
      return undefined;
    }
    let lastWidth = element.clientWidth;
    const observer = new ResizeObserver(() => {
      if (element.clientWidth === lastWidth) return;
      lastWidth = element.clientWidth;
      element.style.height = "auto";
      element.style.height = `${element.scrollHeight}px`;
    });
    observer.observe(element);
    return () => observer.disconnect();
  }, [ref]);

  const submit = () => {
    if (!canSend || busy) {
      return;
    }
    onSubmit(value);
    ref.current?.focus();
  };

  const stop = () => {
    onStop();
    ref.current?.focus();
  };

  // Text menu. The composer opts in to our menu because it also offers Attach and Clear draft;
  // every other text input keeps the browser's own menu.
  const insertAtCursor = (text: string): void => {
    const el = ref.current;
    if (el === null) return;
    const start = el.selectionStart ?? value.length;
    const end = el.selectionEnd ?? value.length;
    onValueChange(value.slice(0, start) + text + value.slice(end));
    const caret = start + text.length;
    window.setTimeout(() => {
      el.focus();
      el.setSelectionRange(caret, caret);
    }, 0);
  };
  const selectedText = (): string => {
    const el = ref.current;
    if (el === null) return "";
    return value.slice(el.selectionStart ?? 0, el.selectionEnd ?? 0);
  };
  const textMenu = useContextMenu({
    label: "Message actions",
    allowEditable: true,
    getItems: () => {
      const picked = selectedText();
      const readable =
        typeof navigator !== "undefined" &&
        typeof navigator.clipboard?.readText === "function";
      return [
        {
          id: "cut",
          label: "Cut",
          icon: <Scissors size={14} />,
          shortcut: "Ctrl+X",
          disabled: picked === "" || disabled,
          disabledReason: "Select some text first.",
          onSelect: () => {
            void copyText(picked);
            insertAtCursor("");
          },
        },
        {
          id: "copy",
          label: "Copy",
          icon: <Copy size={14} />,
          shortcut: "Ctrl+C",
          disabled: picked === "",
          disabledReason: "Select some text first.",
          onSelect: () => void copyText(picked),
        },
        {
          id: "paste",
          label: "Paste",
          icon: <ClipboardPaste size={14} />,
          shortcut: "Ctrl+V",
          disabled: !readable || disabled,
          disabledReason: "This browser does not allow reading the clipboard.",
          onSelect: () =>
            void navigator.clipboard
              .readText()
              .then(insertAtCursor)
              .catch(() => undefined),
        },
        {
          id: "paste-plain",
          label: "Paste as plain text",
          icon: <Type size={14} />,
          disabled: !readable || disabled,
          disabledReason: "This browser does not allow reading the clipboard.",
          onSelect: () =>
            void navigator.clipboard
              .readText()
              .then((t) =>
                insertAtCursor(
                  t
                    .replace(/\r\n?/g, "\n")
                    .replace(
                      /[\u0000-\u0008\u000b\u000c\u000e-\u001f\u200b-\u200f\u202a-\u202e]/g,
                      "",
                    ),
                ),
              )
              .catch(() => undefined),
        },
        {
          id: "select-all",
          label: "Select all",
          icon: <TextSelect size={14} />,
          shortcut: "Ctrl+A",
          disabled: value === "",
          onSelect: () => {
            ref.current?.focus();
            ref.current?.select();
          },
        },
        { kind: "separator" },
        ...(onAttachFiles !== undefined
          ? [
              {
                id: "attach",
                label: "Attach file",
                icon: <Paperclip size={14} />,
                disabled: attachOff,
                disabledReason: attachDisabledReason ?? undefined,
                onSelect: () => fileInputRef.current?.click(),
              },
            ]
          : []),
        {
          id: "clear",
          label: "Clear draft",
          icon: <Eraser size={14} />,
          disabled: value === "",
          disabledReason: "There is no draft.",
          onSelect: () => {
            onValueChange("");
            ref.current?.focus();
          },
        },
      ];
    },
  });

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
    textMenu.triggerProps.onKeyDown(event);
    if (event.defaultPrevented) return;
    if (event.key !== "Enter" || event.shiftKey) {
      return;
    }
    // Enter that confirms an IME candidate must not send.
    if (
      event.nativeEvent.isComposing ||
      composingRef.current ||
      event.keyCode === 229
    ) {
      return;
    }
    event.preventDefault();
    submit();
  };

  const handleChange = (event: ChangeEvent<HTMLTextAreaElement>) => {
    onValueChange(event.target.value);
  };

  const handlePaste = (event: ClipboardEvent<HTMLTextAreaElement>) => {
    if (onAttachFiles === undefined || attachOff) return;
    const files = Array.from(event.clipboardData?.files ?? []);
    // Text that happens to come with a picture (a copied table, say) still pastes as text.
    if (
      files.length === 0 ||
      event.clipboardData.getData("text/plain") !== ""
    ) {
      return;
    }
    event.preventDefault();
    onAttachFiles(files);
  };

  const handlePicked = (event: ChangeEvent<HTMLInputElement>) => {
    const files = Array.from(event.target.files ?? []);
    event.target.value = ""; // choosing the same file again must fire a change
    if (files.length > 0) onAttachFiles?.(files);
    ref.current?.focus();
  };

  // Spoken only when the category changes, not on every keystroke.
  const limitStatus =
    problem === "invalid_characters"
      ? "The message contains characters that cannot be sent."
      : "";

  return (
    <div className="maya-composer" data-busy={busy ? "true" : "false"}>
      {attachments.length > 0 && (
        <ul className="maya-composer__files" aria-label="Attached files">
          {attachments.map((item) => (
            <li key={item.key} className="maya-composer__file">
              <AttachmentChip
                name={item.name}
                kind={item.kind}
                size={item.size}
                status={item.status}
                progress={item.progress}
                error={item.error}
                onRemove={() => onRemoveAttachment?.(item.key)}
              />
            </li>
          ))}
        </ul>
      )}
      {attachNotice !== null && (
        <p className="maya-composer__attach-notice" role="status">
          <TriangleAlert aria-hidden="true" size={13} />
          <span>{attachNotice}</span>
          {onDismissAttachNotice !== undefined && (
            <button
              type="button"
              className="maya-msg__action"
              onClick={onDismissAttachNotice}
            >
              Dismiss
            </button>
          )}
        </p>
      )}
      <div className="maya-composer__box">
        {leading !== undefined && leading !== null ? (
          <div className="maya-composer__lead">{leading}</div>
        ) : null}
        {onAttachFiles !== undefined && (
          <div className="maya-composer__lead">
            <input
              ref={fileInputRef}
              type="file"
              multiple
              accept={UPLOAD_ACCEPT}
              className="maya-sr-only"
              tabIndex={-1}
              aria-hidden="true"
              data-testid="attach-input"
              onChange={handlePicked}
            />
            <button
              type="button"
              className="maya-composer__mic motion-interactive"
              aria-label="Attach files"
              aria-disabled={attachOff}
              title={attachDisabledReason ?? "Attach files"}
              data-testid="attach-button"
              onClick={() => {
                if (!attachOff) fileInputRef.current?.click();
              }}
            >
              <Paperclip aria-hidden="true" size={18} />
            </button>
          </div>
        )}
        <textarea
          ref={ref}
          className="maya-composer__field"
          dir="auto"
          name="message"
          rows={1}
          value={value}
          disabled={disabled}
          placeholder="Write a message"
          aria-label="Message"
          aria-describedby={metaId}
          aria-invalid={problem === "invalid_characters"}
          enterKeyHint="send"
          autoComplete="off"
          onChange={handleChange}
          onPaste={handlePaste}
          onKeyDown={handleKeyDown}
          onContextMenu={textMenu.triggerProps.onContextMenu}
          onCompositionStart={() => {
            composingRef.current = true;
          }}
          onCompositionEnd={() => {
            composingRef.current = false;
          }}
        />
        {extraActions !== undefined && extraActions !== null ? (
          <div className="maya-composer__extra">{extraActions}</div>
        ) : null}
        <button
          type="button"
          className="maya-composer__action"
          data-mode={busy ? "stop" : "send"}
          aria-label={busy ? "Stop generating" : "Send message"}
          disabled={busy ? false : !canSend}
          onClick={busy ? stop : submit}
        >
          <ArrowUp
            className="maya-composer__icon maya-composer__icon--send"
            aria-hidden="true"
            size={18}
          />
          <Square
            className="maya-composer__icon maya-composer__icon--stop"
            aria-hidden="true"
            size={14}
            fill="currentColor"
          />
        </button>
      </div>
      <div className="maya-composer__meta" id={metaId}>
        <p className="maya-composer__hint">
          {uploading
            ? "Waiting for the files to finish uploading."
            : (hint ?? "Enter to send. Shift+Enter for a new line.")}
        </p>
        <p className="maya-composer__count" data-level="ok">
          {problem === "invalid_characters" ? (
            <TriangleAlert aria-hidden="true" size={13} />
          ) : null}
          {problem === "invalid_characters" ? (
            <span>Contains characters that cannot be sent.</span>
          ) : null}
          {count > 0 ? (
            <span>
              {count} {count === 1 ? "character" : "characters"}
            </span>
          ) : null}
        </p>
      </div>
      <div role="status" aria-live="polite" className="maya-sr-only">
        {limitStatus}
      </div>
      {textMenu.menu}
    </div>
  );
}
