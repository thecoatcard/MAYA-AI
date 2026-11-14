"use client";

import { AlertCircle, X } from "lucide-react";
import React, { forwardRef, useId } from "react";

export interface GlassInputProps extends React.InputHTMLAttributes<HTMLInputElement> {
  readonly label?: string;
  readonly helperText?: string;
  readonly error?: string | boolean;
  readonly leftIcon?: React.ReactNode;
  readonly rightIcon?: React.ReactNode;
  readonly clearable?: boolean;
  readonly onClear?: () => void;
  readonly containerClassName?: string;
}

export interface GlassTextareaProps extends React.TextareaHTMLAttributes<HTMLTextAreaElement> {
  readonly label?: string;
  readonly helperText?: string;
  readonly error?: string | boolean;
  readonly containerClassName?: string;
}

/**
 * GlassInput: token-backed input with icon adornments, error state, clear
 * button and accessible labelling. No blur or glow.
 */
export const GlassInput = forwardRef<HTMLInputElement, GlassInputProps>(
  function GlassInput(
    {
      id: providedId,
      label,
      helperText,
      error,
      leftIcon,
      rightIcon,
      clearable = false,
      onClear,
      disabled = false,
      className = "",
      containerClassName = "",
      value,
      ...props
    },
    ref,
  ) {
    const generatedId = useId();
    const id = providedId ?? generatedId;
    const errorId = `${id}-error`;
    const helperId = `${id}-helper`;

    const hasError = Boolean(error);
    const errorMessage = typeof error === "string" ? error : undefined;
    const showClear = clearable && Boolean(value) && !disabled;

    const baseInputStyles =
      "w-full rounded-maya-md bg-maya-pane text-maya-text placeholder:text-maya-muted border motion-interactive focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-maya-focus disabled:bg-maya-disabled-bg disabled:text-maya-disabled-text disabled:cursor-not-allowed";

    const stateStyles = hasError
      ? "border-maya-danger text-maya-text"
      : "border-maya-control-border hover:border-maya-border-strong focus:border-maya-focus";

    const paddingStyles = `${leftIcon ? "pl-10" : "pl-3.5"} ${
      rightIcon || showClear ? "pr-10" : "pr-3.5"
    } py-2 text-maya-body`;

    return (
      <div className={`flex flex-col gap-1.5 ${containerClassName}`.trim()}>
        {label && (
          <label
            htmlFor={id}
            className="text-maya-caption font-medium text-maya-text flex items-center justify-between"
          >
            <span>{label}</span>
            {props.required && (
              <span className="text-maya-accent text-maya-caption">*</span>
            )}
          </label>
        )}

        <div className="relative flex items-center">
          {leftIcon && (
            <div className="absolute left-3 flex items-center pointer-events-none text-maya-muted">
              {leftIcon}
            </div>
          )}

          <input
            ref={ref}
            id={id}
            disabled={disabled}
            value={value}
            aria-invalid={hasError}
            aria-describedby={
              hasError ? errorId : helperText ? helperId : undefined
            }
            className={`${baseInputStyles} ${stateStyles} ${paddingStyles} ${className}`.trim()}
            {...props}
          />

          {showClear && (
            <button
              type="button"
              onClick={onClear}
              className="absolute right-1 grid size-8 place-items-center rounded-maya-sm pointer-coarse:size-11 text-maya-muted hover:text-maya-text hover:bg-maya-hover transition-colors"
              aria-label="Clear input"
            >
              <X className="size-3.5" />
            </button>
          )}

          {!showClear && rightIcon && (
            <div className="absolute right-3 flex items-center text-maya-muted">
              {rightIcon}
            </div>
          )}
        </div>

        {errorMessage && (
          <p
            id={errorId}
            role="alert"
            className="text-maya-caption text-maya-danger flex items-center gap-1.5 mt-0.5"
          >
            <AlertCircle className="size-3.5 shrink-0" aria-hidden="true" />
            <span>{errorMessage}</span>
          </p>
        )}

        {!hasError && helperText && (
          <p id={helperId} className="text-maya-caption text-maya-muted mt-0.5">
            {helperText}
          </p>
        )}
      </div>
    );
  },
);

/**
 * GlassTextarea: token-backed multiline field with error state and helper text.
 */
export const GlassTextarea = forwardRef<
  HTMLTextAreaElement,
  GlassTextareaProps
>(function GlassTextarea(
  {
    id: providedId,
    label,
    helperText,
    error,
    disabled = false,
    className = "",
    containerClassName = "",
    rows = 4,
    ...props
  },
  ref,
) {
  const generatedId = useId();
  const id = providedId ?? generatedId;
  const errorId = `${id}-error`;
  const helperId = `${id}-helper`;

  const hasError = Boolean(error);
  const errorMessage = typeof error === "string" ? error : undefined;

  const baseTextareaStyles =
    "w-full rounded-maya-md bg-maya-pane text-maya-text placeholder:text-maya-muted border motion-interactive focus-visible:outline-2 focus-visible:outline-offset-2 focus-visible:outline-maya-focus disabled:bg-maya-disabled-bg disabled:text-maya-disabled-text disabled:cursor-not-allowed px-3.5 py-2 text-maya-body resize-y";

  const stateStyles = hasError
    ? "border-maya-danger text-maya-text"
    : "border-maya-control-border hover:border-maya-border-strong focus:border-maya-focus";

  return (
    <div className={`flex flex-col gap-1.5 ${containerClassName}`.trim()}>
      {label && (
        <label
          htmlFor={id}
          className="text-maya-caption font-medium text-maya-text flex items-center justify-between"
        >
          <span>{label}</span>
          {props.required && (
            <span className="text-maya-accent text-maya-caption">*</span>
          )}
        </label>
      )}

      <textarea
        ref={ref}
        id={id}
        rows={rows}
        disabled={disabled}
        aria-invalid={hasError}
        aria-describedby={
          hasError ? errorId : helperText ? helperId : undefined
        }
        className={`${baseTextareaStyles} ${stateStyles} ${className}`.trim()}
        {...props}
      />

      {errorMessage && (
        <p
          id={errorId}
          role="alert"
          className="text-maya-caption text-maya-danger flex items-center gap-1.5 mt-0.5"
        >
          <AlertCircle className="size-3.5 shrink-0" aria-hidden="true" />
          <span>{errorMessage}</span>
        </p>
      )}

      {!hasError && helperText && (
        <p id={helperId} className="text-maya-caption text-maya-muted mt-0.5">
          {helperText}
        </p>
      )}
    </div>
  );
});
