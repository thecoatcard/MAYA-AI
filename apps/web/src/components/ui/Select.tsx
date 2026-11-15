"use client";

import { ChevronDown } from "lucide-react";
import React, { forwardRef, useId } from "react";

import { Icon } from "./Icon";
import { cx } from "./cx";

export interface SelectProps extends React.SelectHTMLAttributes<HTMLSelectElement> {
  readonly label?: string;
  readonly helperText?: string;
  readonly error?: string;
  readonly containerClassName?: string;
}

/**
 * Select built on the native element (keyboard, screen readers and the OS picker for free), with
 * a soft rounded field, a chevron icon and the same focus, error and disabled states as Input.
 */
export const Select = forwardRef<HTMLSelectElement, SelectProps>(
  function Select(
    {
      id: providedId,
      label,
      helperText,
      error,
      className,
      containerClassName,
      disabled,
      children,
      ...rest
    },
    ref,
  ) {
    const generated = useId();
    const id = providedId ?? generated;
    const noteId = `${id}-note`;
    const note = error ?? helperText;
    return (
      <div className={cx("flex min-w-0 flex-col gap-1.5", containerClassName)}>
        {label !== undefined && (
          <label
            htmlFor={id}
            className="text-maya-caption font-medium text-maya-text"
          >
            {label}
          </label>
        )}
        <div className="relative min-w-0">
          <select
            {...rest}
            ref={ref}
            id={id}
            disabled={disabled}
            aria-invalid={error !== undefined || undefined}
            aria-describedby={note !== undefined ? noteId : undefined}
            className={cx(
              "motion-interactive min-h-9 w-full appearance-none rounded-maya-md border bg-maya-pane py-2 pr-9 pl-3.5 text-maya-small text-maya-text pointer-coarse:min-h-11 disabled:cursor-not-allowed disabled:bg-maya-disabled-bg disabled:text-maya-disabled-text",
              error !== undefined
                ? "border-maya-danger"
                : "border-maya-control-border hover:border-maya-border-strong focus:border-maya-focus",
              className,
            )}
          >
            {children}
          </select>
          <Icon
            icon={ChevronDown}
            size={16}
            className="pointer-events-none absolute top-1/2 right-3 -translate-y-1/2 text-maya-muted"
          />
        </div>
        {note !== undefined && (
          <p
            id={noteId}
            className={cx(
              "m-0 text-maya-caption",
              error !== undefined ? "text-maya-danger" : "text-maya-muted",
            )}
          >
            {note}
          </p>
        )}
      </div>
    );
  },
);
