import React from "react";

import { cx } from "./cx";

export interface ProgressProps {
  /** 0 to max. Leave out for "working, amount unknown" (needs a label that says so). */
  readonly value?: number;
  readonly max?: number;
  /** Accessible name, also shown when showLabel is set. */
  readonly label: string;
  readonly showLabel?: boolean;
  readonly className?: string;
}

/**
 * Progress bar. The fill is full width and scaled with transform, so it never changes layout. It
 * only shows what really happened: pass the real value, or omit it for the indeterminate bar.
 */
export function Progress({
  value,
  max = 100,
  label,
  showLabel = false,
  className,
}: ProgressProps): React.JSX.Element {
  const known = value !== undefined;
  const ratio = known ? Math.min(1, Math.max(0, value / max)) : 0;
  return (
    <div className={cx("grid min-w-0 gap-1", className)}>
      {showLabel && (
        <div className="flex items-baseline justify-between gap-2 text-maya-caption text-maya-secondary">
          <span className="min-w-0 truncate">{label}</span>
          {known && (
            <span className="maya-count">{Math.round(ratio * 100)}%</span>
          )}
        </div>
      )}
      <div
        role="progressbar"
        aria-label={label}
        aria-valuemin={known ? 0 : undefined}
        aria-valuemax={known ? max : undefined}
        aria-valuenow={known ? value : undefined}
        className="maya-progress h-1.5 w-full overflow-hidden rounded-maya-full bg-maya-raised"
      >
        <div
          className={cx(
            "h-full w-full rounded-maya-full bg-maya-accent",
            known ? "motion-progress" : "motion-progress-indeterminate",
          )}
          style={
            known
              ? ({ "--maya-progress": ratio } as React.CSSProperties)
              : undefined
          }
        />
      </div>
    </div>
  );
}
