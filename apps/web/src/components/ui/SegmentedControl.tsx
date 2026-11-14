"use client";

import type { LucideIcon } from "lucide-react";
import React, { useId, useRef } from "react";

import { Icon } from "./Icon";
import { cx } from "./cx";

export interface SegmentOption<T extends string> {
  readonly value: T;
  readonly label: string;
  readonly icon?: LucideIcon;
  readonly disabled?: boolean;
}

interface BaseProps<T extends string> {
  readonly options: readonly SegmentOption<T>[];
  readonly value: T;
  readonly onChange: (value: T) => void;
  /** Accessible name of the group. */
  readonly label: string;
  readonly className?: string;
}

/** Index of the next enabled option from `from`, stepping `dir`, wrapping around. */
function step<T extends string>(
  options: readonly SegmentOption<T>[],
  from: number,
  dir: 1 | -1,
): number {
  const count = options.length;
  for (let n = 1; n <= count; n += 1) {
    const i = (((from + dir * n) % count) + count) % count;
    if (options[i]?.disabled !== true) return i;
  }
  return from;
}

function Segments<T extends string>({
  options,
  value,
  onChange,
  label,
  className,
  kind,
  panelPrefix,
}: BaseProps<T> & {
  readonly kind: "radio" | "tab";
  readonly panelPrefix?: string;
}): React.JSX.Element {
  const reactId = useId();
  const prefix = panelPrefix ?? reactId;
  const refs = useRef<Array<HTMLButtonElement | null>>([]);
  const index = Math.max(
    0,
    options.findIndex((o) => o.value === value),
  );

  const onKeyDown = (event: React.KeyboardEvent): void => {
    let next = index;
    if (event.key === "ArrowRight" || event.key === "ArrowDown") {
      next = step(options, index, 1);
    } else if (event.key === "ArrowLeft" || event.key === "ArrowUp") {
      next = step(options, index, -1);
    } else if (event.key === "Home") {
      next = step(options, -1, 1);
    } else if (event.key === "End") {
      next = step(options, options.length, -1);
    } else {
      return;
    }
    event.preventDefault();
    const target = options[next];
    if (target === undefined) return;
    onChange(target.value);
    refs.current[next]?.focus();
  };

  return (
    <div
      role={kind === "tab" ? "tablist" : "radiogroup"}
      aria-label={label}
      onKeyDown={onKeyDown}
      className={cx("maya-seg maya-no-drag", className)}
      style={
        {
          "--maya-seg-n": options.length,
          "--maya-seg-i": index,
        } as React.CSSProperties
      }
    >
      <span aria-hidden="true" className="maya-seg__thumb" />
      {options.map((option, i) => {
        const selected = option.value === value;
        return (
          <button
            key={option.value}
            ref={(node) => {
              refs.current[i] = node;
            }}
            type="button"
            role={kind === "tab" ? "tab" : "radio"}
            {...(kind === "tab"
              ? {
                  id: `${prefix}-tab-${option.value}`,
                  "aria-selected": selected,
                  "aria-controls": `${prefix}-panel-${option.value}`,
                }
              : { "aria-checked": selected })}
            tabIndex={selected ? 0 : -1}
            disabled={option.disabled}
            onClick={() => onChange(option.value)}
            className="maya-seg__item inline-flex min-h-7 items-center justify-center gap-1.5 px-3 text-maya-caption pointer-coarse:min-h-11 disabled:cursor-not-allowed disabled:text-maya-disabled-text"
          >
            {option.icon !== undefined && (
              <Icon icon={option.icon} size={14} className="shrink-0" />
            )}
            <span className="min-w-0 truncate">{option.label}</span>
          </button>
        );
      })}
    </div>
  );
}

/** iOS-style segmented control: one choice of a few, with a sliding thumb (transform only). */
export function SegmentedControl<T extends string>(
  props: BaseProps<T>,
): React.JSX.Element {
  return <Segments {...props} kind="radio" />;
}

export interface TabsProps<T extends string> extends BaseProps<T> {
  /** Prefix for the tab and panel ids: panel ids are `${idPrefix}-panel-${value}`. */
  readonly idPrefix: string;
}

/** Same look as SegmentedControl, with tab semantics. Render the panels with role="tabpanel". */
export function Tabs<T extends string>({
  idPrefix,
  ...props
}: TabsProps<T>): React.JSX.Element {
  return <Segments {...props} kind="tab" panelPrefix={idPrefix} />;
}
