import React from "react";

import { cx } from "./cx";

export interface ScrollAreaProps extends Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "aria-label"
> {
  /** Required: a scroll region needs a name, and it is focusable so the keyboard can scroll it. */
  readonly label: string;
  readonly axis?: "y" | "x" | "both";
}

/**
 * A region that scrolls inside its own box (min-h-0 min-w-0, contained overscroll). Place it in
 * a flex or grid cell with a bounded size. Focusable and named for keyboard and screen readers.
 */
export function ScrollArea({
  label,
  axis = "y",
  className,
  ...rest
}: ScrollAreaProps): React.JSX.Element {
  return (
    <div
      {...rest}
      role="region"
      aria-label={label}
      tabIndex={0}
      className={cx(
        "maya-scroll min-h-0 min-w-0 overscroll-contain",
        axis === "y" && "overflow-x-hidden overflow-y-auto",
        axis === "x" && "overflow-x-auto overflow-y-hidden",
        axis === "both" && "overflow-auto",
        className,
      )}
    />
  );
}

export interface CodeBlockShellProps extends Omit<
  React.HTMLAttributes<HTMLPreElement>,
  "aria-label"
> {
  readonly label: string;
  /**
   * Max height before the block scrolls vertically. Default 15rem. Pass null to set no inline
   * style at all (a caller that must stay free of style attributes sets the height in CSS).
   */
  readonly maxHeight?: string | null;
}

/**
 * Shell for code, payloads and logs. Long lines scroll horizontally inside the block (white-space
 * pre) and it never widens its parent. Syntax colouring is the caller's job (chat CodeBlock).
 */
export function CodeBlockShell({
  label,
  maxHeight = "15rem",
  className,
  style,
  ...rest
}: CodeBlockShellProps): React.JSX.Element {
  return (
    <pre
      {...rest}
      role="region"
      aria-label={label}
      tabIndex={0}
      style={maxHeight === null ? style : { maxHeight, ...style }}
      className={cx(
        "maya-code m-0 max-w-full min-w-0 overflow-auto overscroll-contain border border-maya-border bg-maya-code p-3 font-mono text-maya-mono whitespace-pre text-maya-text [tab-size:4]",
        className,
      )}
    />
  );
}

export interface TableScrollProps extends Omit<
  React.HTMLAttributes<HTMLDivElement>,
  "aria-label"
> {
  readonly label: string;
}

/** Wrap a table: it scrolls sideways inside the wrapper instead of widening the page. */
export function TableScroll({
  label,
  className,
  ...rest
}: TableScrollProps): React.JSX.Element {
  return (
    <div
      {...rest}
      role="region"
      aria-label={label}
      tabIndex={0}
      className={cx(
        "maya-table-scroll max-w-full min-w-0 overflow-x-auto overscroll-x-contain border border-maya-border",
        className,
      )}
    />
  );
}
