"use client";

import { Check, CircleHelp, CornerDownLeft } from "lucide-react";
import React, { useEffect, useId, useRef, useState } from "react";

import {
  answerReadableText,
  isAnswered,
  questionLabel,
  type AskAnswerItem,
  type AskCardState,
  type AskQuestion,
  type ChatInputAnswer,
  type ChatInputRequest,
} from "../../lib/ask-user";

import "./AskUserCard.css";

export interface AskUserCardProps {
  readonly request: ChatInputRequest;
  /** "skipped" is local to this view: the user chose to type instead. */
  readonly state: AskCardState | "skipped";
  /** The answer, once answered: shown as a compact read-only summary. */
  readonly answer?: ChatInputAnswer | null;
  /** True while the answer is being sent: every control is disabled. */
  readonly submitting?: boolean;
  /** Send the answers (one item per question). Never an approval. */
  readonly onSubmit: (answers: readonly AskAnswerItem[]) => void;
  /** "Skip, I'll type instead": the caller usually moves focus to the composer. */
  readonly onSkip: () => void;
  /** Move focus into the card when it appears (default true). */
  readonly autoFocus?: boolean;
}

interface QuestionState {
  readonly selected: readonly number[];
  readonly text: string;
}

const MAX_TEXT = 500;

function isTextTarget(target: EventTarget | null): boolean {
  return (
    target instanceof HTMLTextAreaElement ||
    (target instanceof HTMLInputElement &&
      target.type !== "checkbox" &&
      target.type !== "radio")
  );
}

/**
 * MAYA's question to the user, inline in the thread (the `ask_user` tool). Numbered choices
 * (radio or checkbox), an optional text field, Submit and Skip. Keys: 1 to 9 pick, Up and Down
 * move, Enter submits, Esc skips. It is visually distinct from the approval card (question icon,
 * accent tint) and can never approve anything: it only sends the user's reply as a message.
 * All model text is rendered as plain text.
 */
export function AskUserCard({
  request,
  state,
  answer = null,
  submitting = false,
  onSubmit,
  onSkip,
  autoFocus = true,
}: AskUserCardProps): React.JSX.Element {
  const baseId = useId();
  const rootRef = useRef<HTMLElement>(null);
  const [values, setValues] = useState<readonly QuestionState[]>(() =>
    request.questions.map(() => ({ selected: [], text: "" })),
  );
  const [active, setActive] = useState(0);
  const [highlight, setHighlight] = useState<readonly number[]>(() =>
    request.questions.map(() => 0),
  );
  const waiting = state === "awaiting_input";

  // Focus the card when it appears, but never take focus from a text field the user is typing in.
  useEffect(() => {
    if (!waiting || !autoFocus) return;
    const current = document.activeElement;
    const root = rootRef.current;
    if (
      root !== null &&
      current !== null &&
      !root.contains(current) &&
      isTextTarget(current) &&
      (current as HTMLTextAreaElement | HTMLInputElement).value !== ""
    ) {
      return;
    }
    const first = root?.querySelector<HTMLElement>(
      '[role="radio"], [role="checkbox"], textarea',
    );
    first?.focus();
    // once per card
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [request.askId, waiting]);

  if (state === "answered") {
    const items = answer?.answers ?? [];
    return (
      <section
        className="maya-ask maya-ask--done"
        aria-label="Your answer to MAYA's question"
        data-testid="ask-user-card"
        data-state="answered"
      >
        <span className="maya-ask__icon" aria-hidden="true">
          <Check size={14} />
        </span>
        <ul className="maya-ask__summary">
          {request.questions.map((q, i) => {
            const a = items[i];
            const parts =
              a === undefined
                ? []
                : [...a.selected, ...(a.text !== "" ? [a.text] : [])];
            return (
              <li key={i} dir="auto">
                <span className="maya-ask__summary-q">{questionLabel(q)}</span>{" "}
                {parts.length > 0 ? parts.join(", ") : "Skipped"}
              </li>
            );
          })}
        </ul>
      </section>
    );
  }

  if (state === "dismissed" || state === "skipped") {
    return (
      <section
        className="maya-ask maya-ask--done"
        aria-label="Question skipped"
        data-testid="ask-user-card"
        data-state="dismissed"
      >
        <span className="maya-ask__icon" aria-hidden="true">
          <CircleHelp size={14} />
        </span>
        <p className="maya-ask__skipped" dir="auto">
          Skipped: {request.questions[0]?.question}
        </p>
      </section>
    );
  }

  const items: AskAnswerItem[] = request.questions.map((q, i) => ({
    selected: (values[i]?.selected ?? []).map((n) => q.options[n]?.label ?? ""),
    text: values[i]?.text.trim() ?? "",
  }));
  const ready = items.some(isAnswered);

  const patchValue = (qi: number, next: Partial<QuestionState>): void =>
    setValues((prev) => prev.map((v, i) => (i === qi ? { ...v, ...next } : v)));

  const pick = (qi: number, oi: number): void => {
    const q: AskQuestion | undefined = request.questions[qi];
    if (q === undefined || q.options[oi] === undefined) return;
    const current = values[qi]?.selected ?? [];
    if (q.multiSelect) {
      patchValue(qi, {
        selected: current.includes(oi)
          ? current.filter((n) => n !== oi)
          : [...current, oi].sort((a, b) => a - b),
      });
    } else {
      patchValue(qi, { selected: [oi] });
    }
    setHighlight((prev) => prev.map((h, i) => (i === qi ? oi : h)));
  };

  const focusOption = (qi: number, oi: number): void => {
    const el = rootRef.current?.querySelector<HTMLElement>(
      `[data-q="${qi}"][data-o="${oi}"]`,
    );
    el?.focus();
  };

  const submit = (): void => {
    if (submitting || !ready) return;
    onSubmit(items);
  };

  const onKeyDown = (event: React.KeyboardEvent<HTMLElement>): void => {
    if (submitting) return;
    const target = event.target as HTMLElement;
    const textField = isTextTarget(target);
    if (event.key === "Escape") {
      event.preventDefault();
      onSkip();
      return;
    }
    if (event.key === "Enter") {
      if (target.closest("[data-ask-action]") !== null) return; // the buttons act natively
      if (event.shiftKey && textField) return; // a new line
      event.preventDefault();
      if (ready) {
        submit();
        return;
      }
      // Nothing chosen yet: Enter takes the highlighted option of the active question.
      const q = request.questions[active];
      if (q !== undefined && q.options.length > 0) {
        const oi = highlight[active] ?? 0;
        pick(active, oi);
        if (request.questions.length === 1) {
          onSubmit([{ selected: [q.options[oi]?.label ?? ""], text: "" }]);
        }
      }
      return;
    }
    if (textField || event.ctrlKey || event.metaKey || event.altKey) return;
    if (/^[1-9]$/.test(event.key)) {
      const q = request.questions[active];
      const oi = Number(event.key) - 1;
      if (q !== undefined && oi < q.options.length) {
        event.preventDefault();
        pick(active, oi);
        focusOption(active, oi);
      }
      return;
    }
    if (event.key === "ArrowDown" || event.key === "ArrowUp") {
      const qi = Number(target.dataset.q);
      const oi = Number(target.dataset.o);
      const q = request.questions[qi];
      if (q === undefined || Number.isNaN(oi)) return;
      event.preventDefault();
      const count = q.options.length;
      const next = (oi + (event.key === "ArrowDown" ? 1 : count - 1)) % count;
      setHighlight((prev) => prev.map((h, i) => (i === qi ? next : h)));
      focusOption(qi, next);
    }
  };

  const titleId = `${baseId}-title`;
  return (
    <section
      ref={rootRef}
      className="maya-ask motion-none"
      role="group"
      aria-labelledby={titleId}
      aria-busy={submitting}
      data-testid="ask-user-card"
      data-state="awaiting_input"
      onKeyDown={onKeyDown}
    >
      <p role="status" className="maya-sr-only">
        MAYA has {request.questions.length === 1 ? "a question" : "questions"}:{" "}
        {request.questions[0]?.question}
      </p>
      <header className="maya-ask__head">
        <span className="maya-ask__icon" aria-hidden="true">
          <CircleHelp size={16} />
        </span>
        <h3 id={titleId} className="maya-ask__title">
          MAYA is asking
        </h3>
        <span className="maya-ask__note">
          Not an approval. Nothing runs from this.
        </span>
      </header>

      {request.questions.map((q, qi) => {
        const groupId = `${baseId}-q${qi}`;
        const selected = values[qi]?.selected ?? [];
        return (
          <fieldset
            key={qi}
            className="maya-ask__q"
            disabled={submitting}
            onFocus={() => setActive(qi)}
          >
            <legend className="maya-ask__legend">
              {q.header !== "" && (
                <span className="maya-ask__chip">{q.header}</span>
              )}
              <span id={groupId} className="maya-ask__text" dir="auto">
                {q.question}
              </span>
            </legend>
            {q.options.length > 0 && (
              <div
                role={q.multiSelect ? "group" : "radiogroup"}
                aria-labelledby={groupId}
                className="maya-ask__options"
              >
                {q.options.map((o, oi) => {
                  const on = selected.includes(oi);
                  return (
                    <button
                      key={oi}
                      type="button"
                      role={q.multiSelect ? "checkbox" : "radio"}
                      aria-checked={on}
                      aria-keyshortcuts={oi < 9 ? String(oi + 1) : undefined}
                      tabIndex={(highlight[qi] ?? 0) === oi ? 0 : -1}
                      data-q={qi}
                      data-o={oi}
                      data-selected={on}
                      data-highlight={(highlight[qi] ?? 0) === oi}
                      className="maya-ask__option"
                      onClick={() => pick(qi, oi)}
                    >
                      <span className="maya-ask__num" aria-hidden="true">
                        {oi + 1}
                      </span>
                      <span className="maya-ask__opt-body">
                        <span className="maya-ask__opt-label" dir="auto">
                          {o.label}
                        </span>
                        {o.description !== "" && (
                          <span className="maya-ask__opt-desc" dir="auto">
                            {o.description}
                          </span>
                        )}
                      </span>
                      {on && <Check size={14} aria-hidden="true" />}
                    </button>
                  );
                })}
              </div>
            )}
            {q.allowText && (
              <label className="maya-ask__field">
                <span className="maya-ask__field-label">
                  {q.options.length > 0
                    ? "Or tell MAYA what you want instead"
                    : "Your answer"}
                </span>
                <textarea
                  rows={1}
                  maxLength={MAX_TEXT}
                  value={values[qi]?.text ?? ""}
                  onChange={(e) => patchValue(qi, { text: e.target.value })}
                  className="maya-ask__input"
                  dir="auto"
                />
              </label>
            )}
          </fieldset>
        );
      })}

      <footer className="maya-ask__actions">
        <button
          type="button"
          data-ask-action="submit"
          className="maya-ask__btn maya-ask__btn--primary"
          disabled={!ready || submitting}
          onClick={submit}
        >
          Submit
          <CornerDownLeft size={14} aria-hidden="true" />
        </button>
        <button
          type="button"
          data-ask-action="skip"
          className="maya-ask__btn"
          disabled={submitting}
          onClick={onSkip}
        >
          Skip, I&apos;ll type instead
        </button>
        <span className="maya-ask__hint">
          1 to 9 pick, Enter submit, Esc skip
        </span>
      </footer>
    </section>
  );
}

/** The readable message text for an answer (re-exported for callers that send it). */
export { answerReadableText };
