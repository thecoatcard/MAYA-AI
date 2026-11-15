"use client";

import { Check, Copy, Download, MoreHorizontal, WrapText } from "lucide-react";
import { memo, useEffect, useMemo, useRef, useState } from "react";

import { markBidiControls } from "../../lib/markdown";
import {
  downloadText,
  extensionForLanguage,
  copyText,
} from "../../lib/menu-actions";
import { MenuButton, useContextMenu } from "../ui/ContextMenu";
import { CodeBlockShell } from "../ui/Scroll";
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
  // Long lines scroll sideways inside the block by default; Wrap folds them instead.
  const [wrap, setWrap] = useState(false);
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

  const menu = useContextMenu({
    label: "Code block actions",
    getItems: () => [
      {
        id: "copy-code",
        label: "Copy code",
        icon: <Copy size={14} />,
        onSelect: () => void copyText(code),
      },
      {
        id: "save-code",
        label: "Save as file",
        icon: <Download size={14} />,
        onSelect: () =>
          downloadText(`code.${extensionForLanguage(language)}`, code),
      },
      {
        id: "wrap",
        label: "Wrap lines",
        icon: <WrapText size={14} />,
        checked: wrap,
        onSelect: () => setWrap((value) => !value),
      },
    ],
  });

  return (
    <figure
      className="maya-codeblock"
      aria-label={`${label} code`}
      {...menu.triggerProps}
    >
      <figcaption className="maya-codeblock__bar">
        <span className="maya-code__lang">{label}</span>
        <span className="maya-codeblock__tools">
          <button
            type="button"
            className="maya-code__copy"
            aria-pressed={wrap}
            onClick={() => setWrap((value) => !value)}
          >
            <WrapText aria-hidden="true" size={14} />
            <span>Wrap</span>
          </button>
          <button
            type="button"
            className="maya-code__copy"
            data-state={copyState}
            aria-label={BUTTON_NAME[copyState]}
            onClick={copy}
          >
            <span className="maya-code__icons" aria-hidden="true">
              <Copy
                className="maya-code__icon maya-code__icon--copy"
                size={14}
              />
              <Check
                className="maya-code__icon maya-code__icon--check"
                size={14}
              />
            </span>
            <span aria-hidden="true">{VISIBLE_LABEL[copyState]}</span>
          </button>
          <MenuButton label="More code actions" buttonProps={menu.buttonProps}>
            <MoreHorizontal size={14} aria-hidden="true" />
          </MenuButton>
        </span>
      </figcaption>
      {/* The shell is focusable (keyboard users can scroll long lines) and named,
          and it never widens its parent. */}
      <CodeBlockShell
        label={`Code listing, ${label}`}
        maxHeight={null}
        className="maya-code__pre"
        data-wrap={wrap ? "true" : "false"}
        dir="ltr"
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
      </CodeBlockShell>
      <span role="status" className="maya-sr-only">
        {ANNOUNCEMENT[copyState]}
      </span>
      {menu.menu}
    </figure>
  );
}

export const CodeBlock = memo(CodeBlockImpl);
