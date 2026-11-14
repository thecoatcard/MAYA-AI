"use client";

import React from "react";

import { cx } from "./cx";

export interface SwitchProps {
  readonly checked: boolean;
  readonly onCheckedChange: (next: boolean) => void;
  /** Accessible name when there is no visible label element. */
  readonly label?: string;
  /** Id of the element that names the switch (use instead of label). */
  readonly labelledBy?: string;
  readonly describedBy?: string;
  readonly disabled?: boolean;
  /** Also show the words On or Off beside the switch (state is never colour alone). */
  readonly showState?: boolean;
  readonly id?: string;
  readonly className?: string;
}

/**
 * iOS-style on/off switch (role="switch"): pill track and a thumb that springs across. Space and
 * Enter toggle it (it is a real button). State is exposed as aria-checked, the thumb position and
 * optionally the words On / Off. Hit area is 44 px high on coarse pointers. The motion is
 * transform only and is static under reduced motion (ui-surfaces.css).
 */
export function Switch({
  checked,
  onCheckedChange,
  label,
  labelledBy,
  describedBy,
  disabled = false,
  showState = false,
  id,
  className,
}: SwitchProps): React.JSX.Element {
  return (
    <button
      type="button"
      id={id}
      role="switch"
      aria-checked={checked}
      aria-label={labelledBy === undefined ? label : undefined}
      aria-labelledby={labelledBy}
      aria-describedby={describedBy}
      disabled={disabled}
      onClick={() => onCheckedChange(!checked)}
      className={cx(
        "maya-no-drag inline-flex min-h-8 shrink-0 items-center gap-2 rounded-maya-full px-0.5 pointer-coarse:min-h-11 disabled:cursor-not-allowed",
        className,
      )}
    >
      <span aria-hidden="true" className="maya-switch">
        <span className="maya-switch__thumb" />
      </span>
      {showState && (
        <span
          className="maya-count min-w-[2ch] text-maya-caption text-maya-secondary"
          data-testid="switch-state"
        >
          {checked ? "On" : "Off"}
        </span>
      )}
    </button>
  );
}
