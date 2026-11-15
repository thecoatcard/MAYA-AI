import React from "react";

import { cx } from "./cx";

export interface PaneProps extends Omit<
  React.HTMLAttributes<HTMLElement>,
  "title"
> {
  /** Landmark or plain element. Default "section". */
  readonly as?: "section" | "div" | "aside" | "article" | "nav";
  /** Fixed header slot (never scrolls). */
  readonly header?: React.ReactNode;
  /** Fixed footer slot (never scrolls), for example a composer or the approval controls. */
  readonly footer?: React.ReactNode;
  /**
   * When set, the body becomes a labelled, keyboard-focusable scroll region (WCAG: a scrollable
   * area must be reachable without a mouse).
   */
  readonly bodyLabel?: string;
  readonly bodyClassName?: string;
  /** A free-standing card: rounded corners and a soft shadow instead of a full-bleed seam. */
  readonly floating?: boolean;
}

/**
 * A tiled pane: 1 px seam border, header / body / footer. The body is the ONLY scroll region
 * (min-h-0 min-w-0 overflow-auto), so long content scrolls inside the pane and never widens or
 * lengthens the page. The pane fills its grid or flex cell.
 */
export function Pane({
  as: Tag = "section",
  header,
  footer,
  bodyLabel,
  bodyClassName,
  floating = false,
  className,
  children,
  ...rest
}: PaneProps): React.JSX.Element {
  return (
    <Tag
      {...rest}
      className={cx(
        "maya-pane flex h-full min-h-0 min-w-0 flex-col border border-maya-border bg-maya-pane text-maya-text",
        floating && "maya-card",
        className,
      )}
    >
      {header !== undefined && header !== null && (
        <div className="maya-pane-header min-w-0 shrink-0 border-b border-maya-border px-4 py-2">
          {header}
        </div>
      )}
      <div
        className={cx(
          "min-h-0 min-w-0 flex-1 overflow-auto overscroll-contain",
          bodyClassName,
        )}
        {...(bodyLabel !== undefined
          ? { role: "region", "aria-label": bodyLabel, tabIndex: 0 }
          : {})}
      >
        {children}
      </div>
      {footer !== undefined && footer !== null && (
        <div className="maya-pane-footer min-w-0 shrink-0 border-t border-maya-border px-4 py-3">
          {footer}
        </div>
      )}
    </Tag>
  );
}
