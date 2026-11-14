import React from "react";

import { cx } from "./cx";

/** A keyboard key, for shortcut hints: <Kbd>Ctrl</Kbd> + <Kbd>K</Kbd>. */
export function Kbd({
  className,
  ...rest
}: React.HTMLAttributes<HTMLElement>): React.JSX.Element {
  return (
    <kbd
      {...rest}
      className={cx(
        "maya-kbd inline-block min-w-6 rounded-maya-xs border border-maya-border-strong bg-maya-raised px-1.5 py-0.5 tabular-nums shadow-maya-1 text-center font-mono text-maya-label leading-4 text-maya-secondary",
        className,
      )}
    />
  );
}
