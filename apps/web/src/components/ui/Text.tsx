import React from "react";

import { cx } from "./cx";

export interface TruncateProps extends Omit<
  React.HTMLAttributes<HTMLElement>,
  "title"
> {
  readonly as?: "span" | "div" | "p" | "h1" | "h2" | "h3" | "h4";
  /** 1 (default) is a single line with an ellipsis. 2 or more clamps to that many lines. */
  readonly lines?: number;
  /** Full text for the tooltip. Defaults to the children when they are a plain string. */
  readonly title?: string;
}

/**
 * Cuts long text with an ellipsis and keeps the full text in the native title (tooltip) so
 * nothing is lost. Needs a parent that can shrink (flex or grid child): min-w-0 is built in.
 */
export function Truncate({
  as: Tag = "span",
  lines = 1,
  title,
  className,
  style,
  children,
  ...rest
}: TruncateProps): React.JSX.Element {
  const full = title ?? (typeof children === "string" ? children : undefined);
  const clamped = lines > 1;
  return (
    <Tag
      {...rest}
      title={full}
      className={cx(
        "maya-truncate block max-w-full min-w-0",
        clamped ? "overflow-hidden [overflow-wrap:anywhere]" : "truncate",
        className,
      )}
      style={
        clamped
          ? {
              display: "-webkit-box",
              WebkitBoxOrient: "vertical",
              WebkitLineClamp: lines,
              ...style,
            }
          : style
      }
    >
      {children}
    </Tag>
  );
}

export interface WrapTextProps extends React.HTMLAttributes<HTMLElement> {
  readonly as?: "span" | "div" | "p" | "code";
}

/**
 * For strings with no break opportunities: URLs, hashes, file paths, ids. They wrap anywhere
 * instead of pushing their container wider. Spaces and newlines in the text are kept.
 */
export function WrapText({
  as: Tag = "span",
  className,
  ...rest
}: WrapTextProps): React.JSX.Element {
  return (
    <Tag
      {...rest}
      className={cx(
        "maya-wrap max-w-full min-w-0 whitespace-pre-wrap [overflow-wrap:anywhere]",
        className,
      )}
    />
  );
}
