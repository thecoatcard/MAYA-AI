import React from "react";

import { cx } from "./cx";

export type Gap = 0 | 1 | 2 | 3 | 4 | 5 | 6 | 8;

/** Gap values map to the spacing scale (--maya-space-N, 4 px base). */
const GAP: Record<Gap, string> = {
  0: "gap-0",
  1: "gap-1",
  2: "gap-2",
  3: "gap-3",
  4: "gap-4",
  5: "gap-5",
  6: "gap-6",
  8: "gap-8",
};

export interface FlexProps extends React.HTMLAttributes<HTMLElement> {
  readonly as?: "div" | "section" | "ul" | "ol" | "nav" | "header" | "footer";
  readonly gap?: Gap;
  readonly align?: "start" | "center" | "end" | "stretch" | "baseline";
  readonly justify?: "start" | "center" | "end" | "between";
  /** Allow children to wrap onto several lines (a Row only wraps when asked). */
  readonly wrap?: boolean;
}

const ALIGN = {
  start: "items-start",
  center: "items-center",
  end: "items-end",
  stretch: "items-stretch",
  baseline: "items-baseline",
} as const;

const JUSTIFY = {
  start: "justify-start",
  center: "justify-center",
  end: "justify-end",
  between: "justify-between",
} as const;

function flex(
  direction: "flex-col" | "flex-row",
  {
    as: Tag = "div",
    gap = 2,
    align,
    justify,
    wrap = false,
    className,
    ...rest
  }: FlexProps,
): React.JSX.Element {
  return (
    <Tag
      {...rest}
      // min-w-0 is the default: a flex child may shrink below its content instead of widening
      // the parent, which is how long strings and URLs otherwise break layouts.
      className={cx(
        "flex min-w-0",
        direction,
        GAP[gap],
        align !== undefined && ALIGN[align],
        justify !== undefined && JUSTIFY[justify],
        wrap && "flex-wrap",
        className,
      )}
    />
  );
}

/** Vertical flex container with a token gap and min-w-0. */
export function Stack(props: FlexProps): React.JSX.Element {
  return flex("flex-col", props);
}

/** Horizontal flex container with a token gap and min-w-0. Give text children min-w-0 too. */
export function Row(props: FlexProps): React.JSX.Element {
  return flex("flex-row", { align: "center", ...props });
}
