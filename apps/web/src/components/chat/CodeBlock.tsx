"use client";

import { Check, Copy } from "lucide-react";
import { memo, useEffect, useMemo, useRef, useState } from "react";

import { markBidiControls } from "../../lib/markdown";
import { highlight } from "./highlight";

export interface CodeBlockProps {
  code: string;
  /** Language label from the fence info string, if any. */
  language?: string | null;
}

type CopyState = "idle" | "copied" | "failed";

const COPIED_FEEDBACK_MS = 2000;
const FAILED_FEEDBACK_MS = 4000;

const VISIBLE_LABEL: Record<CopyState, string> = {
  idle: "Copy",
  copied: "Copied",
  failed: "Copy failed",
};

// The accessible name always contains the visible text (WCAG 2.5.3).
const BUTTON_NAME: Record<CopyState, string> = {
  idle: "Copy code",
  copied: "Code copied",
  failed: "Copy failed",
};

const ANNOUNCEMENT: Record<CopyState, string> = {
  idle: "",
  copied: "Code copied to clipboard.",
  failed: "Could not copy. Select the code and copy it manually.",
};

function CodeBlockImpl({ code: rawCode, language = null }: CodeBlockProps) {
  const [copyState, setCopyState] = useState<CopyState>("idle");
  const timer = useRef<ReturnType<typeof setTimeout> | null>(null);
  const mounted = useRef(true);

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
      if (timer.current !== null) {
        clearTimeout(timer.current);
        timer.current = null;
      }
    };
  }, []);

  // Bidirectional controls are shown as <U+202E> style markers instead of being
  // obeyed, so code cannot read differently from what it does. The Copy button
  // copies this same marked text.
  const code = useMemo(() => markBidiControls(rawCode), [rawCode]);
  const tokens = useMemo(() => highlight(code, language), [code, language]);
  const label = language !== null && language !== "" ? language : "text";

  const copy = async () => {
    let next: CopyState = "failed";
    try {
      if (typeof navigator !== "undefined" && navigator.clipboard) {
        await navigator.clipboard.writeText(code);
        next = "copied";
      }
    } catch {
      next = "failed";
    }
    // The clipboard call can finish after the block was removed from the page.
    if (!mounted.current) {
      return;
    }
    setCopyState(next);
    if (timer.current !== null) {
      clearTimeout(timer.current);
    }
    timer.current = setTimeout(
      () => setCopyState("idle"),
      next === "copied" ? COPIED_FEEDBACK_MS : FAILED_FEEDBACK_MS,
    );
  };

  return (
    <figure className="maya-code" aria-label={`${label} code`}>
      <figcaption className="maya-code__bar">
        <span className="maya-code__lang">{label}</span>
        <button
          type="button"
          className="maya-code__copy"
          data-state={copyState}
          aria-label={BUTTON_NAME[copyState]}
          onClick={copy}
        >
          <span className="maya-code__icons" aria-hidden="true">
            <Copy className="maya-code__icon maya-code__icon--copy" size={14} />
            <Check
              className="maya-code__icon maya-code__icon--check"
              size={14}
            />
          </span>
          <span aria-hidden="true">{VISIBLE_LABEL[copyState]}</span>
        </button>
      </figcaption>
      {/* Focusable so keyboard users can scroll long lines; a named group so the
          stop announces what it is. */}
      <pre
        className="maya-code__pre"
        tabIndex={0}
        role="group"
        aria-label={`Code listing, ${label}`}
      >
        <code>
          {tokens.map((token, index) =>
            token.type === "plain" ? (
              token.text
            ) : (
              <span key={index} className={`maya-tok maya-tok--${token.type}`}>
                {token.text}
              </span>
            ),
          )}
        </code>
      </pre>
      <span role="status" className="maya-sr-only">
        {ANNOUNCEMENT[copyState]}
      </span>
    </figure>
  );
}

export const CodeBlock = memo(CodeBlockImpl);
