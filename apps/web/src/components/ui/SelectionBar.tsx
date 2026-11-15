"use client";

import { Trash2 } from "lucide-react";
import React from "react";

import { Button } from "./Button";

export interface SelectionBarProps {
  readonly count: number;
  /** Plural noun for the label of the region, for example "tasks". */
  readonly noun: string;
  readonly onDelete: () => void;
  readonly onClear: () => void;
  readonly disabled?: boolean;
}

/** Sticky bar shown while rows are selected: "3 selected", Delete and Clear selection. */
export function SelectionBar({
  count,
  noun,
  onDelete,
  onClear,
  disabled = false,
}: SelectionBarProps): React.JSX.Element | null {
  if (count === 0) return null;
  return (
    <div
      role="region"
      aria-label={`Selected ${noun}`}
      data-testid="selection-bar"
      className="sticky bottom-0 z-10 flex min-w-0 flex-wrap items-center justify-between gap-2 border border-maya-border-strong bg-maya-overlay px-3 py-2 shadow-maya-2"
    >
      <span
        className="text-maya-small font-medium text-maya-text"
        aria-live="polite"
      >
        {count} selected
      </span>
      <span className="flex flex-wrap items-center gap-2">
        <Button variant="ghost" size="sm" onClick={onClear} disabled={disabled}>
          Clear selection
        </Button>
        <Button
          variant="danger"
          size="sm"
          onClick={onDelete}
          disabled={disabled}
          leftIcon={<Trash2 size={14} />}
        >
          Delete
        </Button>
      </span>
    </div>
  );
}
