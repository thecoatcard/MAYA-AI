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
 * GlassInput - Translucent frosted input field with icon adornments,
 * focused teal glow, error states, clear button, and accessible labeling.
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
      "w-full rounded-xl bg-zinc-900/50 hover:bg-zinc-900/70 focus:bg-zinc-900/90 text-zinc-100 placeholder:text-zinc-500 backdrop-blur-md border shadow-[inset_0_1px_2px_rgba(0,0,0,0.3)] transition-all duration-150 focus-visible:outline-none focus:ring-2 disabled:opacity-50 disabled:cursor-not-allowed";

    const stateStyles = hasError
      ? "border-rose-500/60 focus:border-rose-500 focus:ring-rose-500/30 text-rose-100 placeholder:text-rose-400/50"
      : "border-white/10 focus:border-teal-500/60 focus:ring-teal-500/30";

    const paddingStyles = `${leftIcon ? "pl-10" : "pl-3.5"} ${
      rightIcon || showClear ? "pr-10" : "pr-3.5"
    } py-2.5 text-sm`;

    return (
      <div className={`flex flex-col gap-1.5 ${containerClassName}`.trim()}>
        {label && (
          <label
            htmlFor={id}
            className="text-xs font-medium text-zinc-300 flex items-center justify-between"
          >
            <span>{label}</span>
            {props.required && <span className="text-teal-400 text-xs">*</span>}
          </label>
        )}

        <div className="relative flex items-center">
          {leftIcon && (
            <div className="absolute left-3 flex items-center pointer-events-none text-zinc-400">
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
              className="absolute right-3 p-1 rounded-md text-zinc-400 hover:text-zinc-200 hover:bg-white/10 transition-colors"
              aria-label="Clear input"
            >
              <X className="w-3.5 h-3.5" />
            </button>
          )}

          {!showClear && rightIcon && (
            <div className="absolute right-3 flex items-center text-zinc-400">
              {rightIcon}
            </div>
          )}
        </div>

        {errorMessage && (
          <p
            id={errorId}
            role="alert"
            className="text-xs text-rose-400 flex items-center gap-1.5 mt-0.5"
          >
            <AlertCircle className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
            <span>{errorMessage}</span>
          </p>
        )}

        {!hasError && helperText && (
          <p id={helperId} className="text-xs text-zinc-400 mt-0.5">
            {helperText}
          </p>
        )}
      </div>
    );
  },
);

/**
 * GlassTextarea - Translucent frosted multiline textarea with focus ring,
 * error state styling, and helper label.
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
    "w-full rounded-xl bg-zinc-900/50 hover:bg-zinc-900/70 focus:bg-zinc-900/90 text-zinc-100 placeholder:text-zinc-500 backdrop-blur-md border shadow-[inset_0_1px_2px_rgba(0,0,0,0.3)] transition-all duration-150 focus-visible:outline-none focus:ring-2 disabled:opacity-50 disabled:cursor-not-allowed px-3.5 py-2.5 text-sm resize-y";

  const stateStyles = hasError
    ? "border-rose-500/60 focus:border-rose-500 focus:ring-rose-500/30 text-rose-100 placeholder:text-rose-400/50"
    : "border-white/10 focus:border-teal-500/60 focus:ring-teal-500/30";

  return (
    <div className={`flex flex-col gap-1.5 ${containerClassName}`.trim()}>
      {label && (
        <label
          htmlFor={id}
          className="text-xs font-medium text-zinc-300 flex items-center justify-between"
        >
          <span>{label}</span>
          {props.required && <span className="text-teal-400 text-xs">*</span>}
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
          className="text-xs text-rose-400 flex items-center gap-1.5 mt-0.5"
        >
          <AlertCircle className="w-3.5 h-3.5 shrink-0" aria-hidden="true" />
          <span>{errorMessage}</span>
        </p>
      )}

      {!hasError && helperText && (
        <p id={helperId} className="text-xs text-zinc-400 mt-0.5">
          {helperText}
        </p>
      )}
    </div>
  );
});
