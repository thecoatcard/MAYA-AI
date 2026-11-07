"use client";

import { ArrowUp, Mic, Square, TriangleAlert } from "lucide-react";
import {
  useId,
  useLayoutEffect,
  useRef,
  type ChangeEvent,
  type KeyboardEvent,
  type RefObject,
} from "react";

import {
  CHAT_MAX_MESSAGE_CHARS,
  countCodePoints,
  validateChatContent,
} from "../../lib/api-client";

export interface ChatInputProps {
  value: string;
  onValueChange: (value: string) => void;
  /** Called with the text when the user sends. The parent clears `value`. */
  onSubmit: (text: string) => void;
  onStop: () => void;
  /** A reply is being written: the action button becomes Stop. */
  busy: boolean;
  disabled?: boolean;
  /** Provisional input limit; the server is the authority. */
  maxChars?: number;
  inputRef?: RefObject<HTMLTextAreaElement | null>;
  /** Optional real-time voice microphone controls */
  isListening?: boolean;
  onToggleVoice?: () => void;
  voiceLabel?: string;
}

/** Counter turns into a warning at this share of the limit. */
const NEAR_LIMIT_RATIO = 0.9;

export function ChatInput({
  value,
  onValueChange,
  onSubmit,
  onStop,
  busy,
  disabled = false,
  maxChars = CHAT_MAX_MESSAGE_CHARS,
  inputRef,
  isListening = false,
  onToggleVoice,
  voiceLabel,
}: ChatInputProps) {
  const localRef = useRef<HTMLTextAreaElement>(null);
  const ref = inputRef ?? localRef;
  const composingRef = useRef(false);
  const metaId = useId();

  const count = countCodePoints(value);
  const problem = validateChatContent(value, maxChars);
  const over = problem === "too_long";
  const near = !over && count >= maxChars * NEAR_LIMIT_RATIO;
  const canSend = problem === null && !disabled;

  // Grow with the text; CSS caps the height and scrolls beyond it.
  useLayoutEffect(() => {
    const element = ref.current;
    if (element === null) {
      return;
    }
    element.style.height = "auto";
    element.style.height = `${element.scrollHeight}px`;
  }, [value, ref]);

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

  const handleKeyDown = (event: KeyboardEvent<HTMLTextAreaElement>) => {
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

  // Spoken only when the category changes, not on every keystroke.
  let limitStatus = "";
  if (over) {
    limitStatus = "The message is over the length limit.";
  } else if (problem === "invalid_characters") {
    limitStatus = "The message contains characters that cannot be sent.";
  } else if (near) {
    limitStatus = "The message is close to the length limit.";
  }

  return (
    <div className="maya-composer" data-busy={busy ? "true" : "false"}>
      <div className="maya-composer__box">
        <textarea
          ref={ref}
          className="maya-composer__field"
          name="message"
          rows={1}
          value={value}
          disabled={disabled}
          placeholder="Write a message"
          aria-label="Message"
          aria-describedby={metaId}
          aria-invalid={over || problem === "invalid_characters"}
          enterKeyHint="send"
          autoComplete="off"
          onChange={handleChange}
          onKeyDown={handleKeyDown}
          onCompositionStart={() => {
            composingRef.current = true;
          }}
          onCompositionEnd={() => {
            composingRef.current = false;
          }}
        />
        {onToggleVoice && (
          <button
            type="button"
            className={`p-2 rounded-lg border transition-all flex items-center justify-center mr-1 ${
              isListening
                ? "border-teal-500/50 bg-teal-500/20 text-teal-300 animate-pulse"
                : "border-zinc-800 bg-zinc-900/60 text-zinc-400 hover:text-zinc-200 hover:border-zinc-700"
            }`}
            aria-label={
              isListening ? "Stop voice listening" : "Start voice listening"
            }
            title={
              isListening
                ? "Stop voice listening"
                : voiceLabel || "Speak with Maya"
            }
            onClick={onToggleVoice}
          >
            <Mic
              className={`w-4 h-4 ${isListening ? "text-teal-400" : "text-zinc-400"}`}
            />
          </button>
        )}
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
          {isListening ? (
            <span className="text-teal-400 font-semibold inline-flex items-center gap-1.5 animate-pulse">
              <span className="w-1.5 h-1.5 rounded-full bg-teal-400" />
              Maya is listening to your microphone... speak or type below
            </span>
          ) : (
            "Enter to send. Shift+Enter for a new line."
          )}
        </p>
        <p
          className="maya-composer__count"
          data-level={over ? "over" : near ? "near" : "ok"}
        >
          {over || near || problem === "invalid_characters" ? (
            <TriangleAlert aria-hidden="true" size={13} />
          ) : null}
          {near ? <span>Close to the limit.</span> : null}
          {over ? <span>Over by {count - maxChars}.</span> : null}
          {problem === "invalid_characters" ? (
            <span>Contains characters that cannot be sent.</span>
          ) : null}
          <span>
            {count} / {maxChars}
          </span>
        </p>
      </div>
      <div role="status" aria-live="polite" className="maya-sr-only">
        {limitStatus}
      </div>
    </div>
  );
}
