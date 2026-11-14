"use client";

import { Eye, EyeOff } from "lucide-react";
import React, { useEffect, useId, useState } from "react";

import {
  ApiError,
  BackendUnreachableError,
  NetworkError,
} from "../../lib/api-client";
import { Button } from "../ui/Button";
import { cx } from "../ui/cx";
import { ThemeToggle } from "../ui/ThemeToggle";

/** Password policy from docs/api/AUTH.md: 12 to 128 characters after normalisation. */
export const PASSWORD_MIN_LENGTH = 12;
export const PASSWORD_MAX_LENGTH = 128;
export const PASSWORD_HINT =
  "12 to 128 characters. It must not match your username or email.";
export const PASSWORD_TOO_SHORT_MESSAGE =
  "Choose a password of at least 12 characters.";

/** True for failures where trying the same request again can succeed. */
export function isRetryableAuthError(error: unknown): boolean {
  if (
    error instanceof NetworkError ||
    error instanceof BackendUnreachableError
  ) {
    return true;
  }
  return error instanceof ApiError && error.code === "service_unavailable";
}

/** Which form field a server error code belongs to, or null for a form-level error. */
export function errorFieldFor(
  error: unknown,
  passwordField: string,
): string | null {
  if (!(error instanceof ApiError)) return null;
  switch (error.code) {
    case "password_too_short":
    case "password_too_long":
    case "password_matches_identifier":
      return passwordField;
    case "invalid_or_expired_code":
      return "code";
    default:
      return null;
  }
}

/**
 * Moves focus to the first invalid field, or to the form alert when the error is not tied to a
 * field, every time `tick` changes after the first error. Never runs on first render.
 */
export function useFocusFirstError(
  formRef: React.RefObject<HTMLFormElement | null>,
  tick: number,
): void {
  useEffect(() => {
    if (tick === 0) return;
    const form = formRef.current;
    if (form === null) return;
    const target =
      form.querySelector<HTMLElement>('[aria-invalid="true"]') ??
      form.querySelector<HTMLElement>("[data-auth-alert]");
    target?.focus();
  }, [formRef, tick]);
}

/* ------------------------------------------------------------------- header */

export function AuthHeader({
  titleId,
  title,
  children,
}: {
  readonly titleId: string;
  readonly title: string;
  readonly children?: React.ReactNode;
}): React.JSX.Element {
  return (
    <header className="min-w-0">
      <div className="flex min-w-0 items-start justify-between gap-3">
        <h1
          id={titleId}
          className="m-0 min-w-0 text-maya-title font-semibold [overflow-wrap:anywhere]"
        >
          {title}
        </h1>
        <ThemeToggle />
      </div>
      {children !== undefined && (
        <div className="mt-2 grid gap-2 text-maya-small text-maya-secondary">
          {children}
        </div>
      )}
    </header>
  );
}

/* -------------------------------------------------------------------- field */

export interface AuthFieldProps {
  readonly label: string;
  readonly name: string;
  readonly value: string;
  readonly onChange: (value: string) => void;
  readonly type?: string;
  readonly autoComplete?: string;
  readonly inputMode?: "numeric" | "email" | "text";
  readonly maxLength?: number;
  readonly required?: boolean;
  readonly disabled?: boolean;
  readonly autoFocus?: boolean;
  readonly placeholder?: string;
  /** Helper text shown under the field until there is an error. */
  readonly hint?: string;
  /** Inline error for this field; announced and tied with aria-describedby. */
  readonly error?: string | null;
}

const INPUT_CLASS =
  "block w-full min-w-0 rounded-maya-md border bg-maya-raised px-3 py-2 min-h-10 pointer-coarse:min-h-11 text-maya-body text-maya-text placeholder:text-maya-muted disabled:cursor-not-allowed disabled:bg-maya-disabled-bg disabled:text-maya-disabled-text";

/** A labelled input with a hint and an inline error tied by aria-describedby. */
export function AuthField({
  label,
  name,
  value,
  onChange,
  type = "text",
  autoComplete,
  inputMode,
  maxLength,
  required = true,
  disabled = false,
  autoFocus = false,
  placeholder,
  hint,
  error = null,
}: AuthFieldProps): React.JSX.Element {
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const invalid = error !== null && error !== "";
  const describedBy =
    [invalid ? errorId : null, hint !== undefined ? hintId : null]
      .filter((part): part is string => part !== null)
      .join(" ") || undefined;
  return (
    <div className="grid min-w-0 gap-1.5">
      <label
        htmlFor={id}
        className="text-maya-small font-medium text-maya-text"
      >
        {label}
      </label>
      <input
        id={id}
        name={name}
        type={type}
        value={value}
        onChange={(event) => onChange(event.target.value)}
        autoComplete={autoComplete}
        autoCapitalize="none"
        autoCorrect="off"
        spellCheck={false}
        inputMode={inputMode}
        maxLength={maxLength}
        required={required}
        disabled={disabled}
        autoFocus={autoFocus}
        placeholder={placeholder}
        aria-invalid={invalid ? true : undefined}
        aria-describedby={describedBy}
        className={cx(
          INPUT_CLASS,
          invalid ? "border-maya-danger" : "border-maya-control-border",
        )}
      />
      {invalid && (
        <p
          id={errorId}
          role="alert"
          className="m-0 text-maya-caption text-maya-danger [overflow-wrap:anywhere]"
        >
          {error}
        </p>
      )}
      {hint !== undefined && (
        <p
          id={hintId}
          className="m-0 text-maya-caption text-maya-muted [overflow-wrap:anywhere]"
        >
          {hint}
        </p>
      )}
    </div>
  );
}

/** AuthField for passwords with a labelled Show/Hide control. Policy hint is opt-in. */
export function PasswordField(
  props: Omit<AuthFieldProps, "type"> & { readonly showPolicy?: boolean },
): React.JSX.Element {
  const { showPolicy = false, hint, ...rest } = props;
  const [shown, setShown] = useState(false);
  const id = useId();
  const hintId = `${id}-hint`;
  const errorId = `${id}-error`;
  const invalid = rest.error !== undefined && rest.error !== null;
  const effectiveHint = hint ?? (showPolicy ? PASSWORD_HINT : undefined);
  const describedBy =
    [invalid ? errorId : null, effectiveHint !== undefined ? hintId : null]
      .filter((part): part is string => part !== null)
      .join(" ") || undefined;
  return (
    <div className="grid min-w-0 gap-1.5">
      <label
        htmlFor={id}
        className="text-maya-small font-medium text-maya-text"
      >
        {rest.label}
      </label>
      <div className="flex min-w-0 items-stretch gap-2">
        <input
          id={id}
          name={rest.name}
          type={shown ? "text" : "password"}
          value={rest.value}
          onChange={(event) => rest.onChange(event.target.value)}
          autoComplete={rest.autoComplete}
          autoCapitalize="none"
          autoCorrect="off"
          spellCheck={false}
          maxLength={rest.maxLength}
          required={rest.required ?? true}
          disabled={rest.disabled}
          autoFocus={rest.autoFocus}
          aria-invalid={invalid ? true : undefined}
          aria-describedby={describedBy}
          className={cx(
            INPUT_CLASS,
            "flex-1",
            invalid ? "border-maya-danger" : "border-maya-control-border",
          )}
        />
        <Button
          variant="secondary"
          aria-label={shown ? "Hide password" : "Show password"}
          aria-pressed={shown}
          onClick={() => setShown((current) => !current)}
          leftIcon={
            shown ? (
              <EyeOff size={16} aria-hidden />
            ) : (
              <Eye size={16} aria-hidden />
            )
          }
          className="shrink-0"
        >
          {shown ? "Hide" : "Show"}
        </Button>
      </div>
      {invalid && (
        <p
          id={errorId}
          role="alert"
          className="m-0 text-maya-caption text-maya-danger [overflow-wrap:anywhere]"
        >
          {rest.error}
        </p>
      )}
      {effectiveHint !== undefined && (
        <p
          id={hintId}
          className="m-0 text-maya-caption text-maya-muted [overflow-wrap:anywhere]"
        >
          {effectiveHint}
        </p>
      )}
    </div>
  );
}

/* --------------------------------------------------------------- form alert */

/**
 * The form-level error (not tied to a field): one alert, focusable so a keyboard or screen
 * reader user lands on it, with Retry when trying again can help.
 */
export function FormAlert({
  message,
  onRetry,
  busy = false,
}: {
  readonly message: string;
  readonly onRetry?: () => void;
  readonly busy?: boolean;
}): React.JSX.Element {
  return (
    <div
      role="alert"
      data-auth-alert=""
      tabIndex={-1}
      className="grid min-w-0 gap-2 rounded-maya-md border border-maya-danger bg-maya-danger-soft p-3 text-maya-small text-maya-text [overflow-wrap:anywhere]"
    >
      <p className="m-0">{message}</p>
      {onRetry !== undefined && (
        <div>
          <Button
            size="sm"
            variant="secondary"
            disabled={busy}
            onClick={onRetry}
          >
            Retry
          </Button>
        </div>
      )}
    </div>
  );
}

/** Calm single card used by the stand-alone auth pages and the sign-in gate. */
export const AUTH_FORM_CLASS = "mt-5 grid min-w-0 gap-4";
export const AUTH_LINKS_CLASS =
  "mt-4 flex min-w-0 flex-wrap gap-x-5 gap-y-1 text-maya-small";
export const AUTH_LINK_CLASS =
  "inline-flex min-h-8 items-center rounded-maya-sm text-maya-link underline underline-offset-4 hover:text-maya-accent-hover pointer-coarse:min-h-11";
