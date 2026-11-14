"use client";

import Link from "next/link";
import { useEffect, useRef, useState, type FormEvent } from "react";

import { ApiError, NetworkError } from "../../lib/api-client";
import {
  registerAccount,
  requestPasswordReset,
  resendVerification,
  resetPassword,
  verifyEmail,
} from "../../lib/auth-client";
import { Button } from "../ui/Button";
import { Notice } from "../ui/Feedback";
import {
  AUTH_FORM_CLASS,
  AUTH_LINK_CLASS,
  AUTH_LINKS_CLASS,
  AuthField,
  AuthHeader,
  FormAlert,
  PASSWORD_MIN_LENGTH,
  PASSWORD_TOO_SHORT_MESSAGE,
  PasswordField,
  errorFieldFor,
  isRetryableAuthError,
  useFocusFirstError,
} from "./AuthParts";

type AccountMode = "register" | "verify" | "forgot" | "reset";
type FieldName =
  | "username"
  | "email"
  | "password"
  | "full_name"
  | "workspace_name"
  | "code"
  | "new_password";

interface FieldDefinition {
  name: FieldName;
  label: string;
  type?: string;
  required?: boolean;
  maxLength?: number;
  hint?: string;
  autoComplete?: string;
  inputMode?: "numeric" | "email";
}

const FIELD_SETS: Record<AccountMode, readonly FieldDefinition[]> = {
  register: [
    { name: "full_name", label: "Full name", required: true, maxLength: 1024 },
    {
      name: "username",
      label: "Username",
      required: true,
      maxLength: 64,
      autoComplete: "username",
      hint: "3 to 32 characters: letters, numbers, dot, dash or underscore.",
    },
    {
      name: "email",
      label: "Email",
      type: "email",
      required: true,
      maxLength: 320,
      autoComplete: "email",
    },
    {
      name: "password",
      label: "Password",
      type: "password",
      required: true,
      maxLength: 128,
      autoComplete: "new-password",
    },
    {
      name: "workspace_name",
      label: "Workspace name (optional)",
      maxLength: 1024,
    },
  ],
  verify: [
    {
      name: "email",
      label: "Email",
      type: "email",
      required: true,
      maxLength: 320,
      autoComplete: "email",
    },
    {
      name: "code",
      label: "6-digit code",
      required: true,
      maxLength: 6,
      autoComplete: "one-time-code",
      inputMode: "numeric",
    },
    {
      name: "password",
      label: "Registration password",
      type: "password",
      required: true,
      maxLength: 128,
      autoComplete: "current-password",
    },
  ],
  forgot: [
    {
      name: "email",
      label: "Email",
      type: "email",
      required: true,
      maxLength: 320,
      autoComplete: "email",
    },
  ],
  reset: [
    {
      name: "email",
      label: "Email",
      type: "email",
      required: true,
      maxLength: 320,
      autoComplete: "email",
    },
    {
      name: "code",
      label: "6-digit code",
      required: true,
      maxLength: 6,
      autoComplete: "one-time-code",
      inputMode: "numeric",
    },
    {
      name: "new_password",
      label: "New password",
      type: "password",
      required: true,
      maxLength: 128,
      autoComplete: "new-password",
    },
  ],
};

const TITLES: Record<AccountMode, string> = {
  register: "Create your account",
  verify: "Verify your email",
  forgot: "Reset your password",
  reset: "Choose a new password",
};

function errorText(error: unknown): string {
  if (error instanceof NetworkError) {
    return "MAYA could not be reached. Check the connection and try again.";
  }
  if (error instanceof ApiError) {
    switch (error.code) {
      case "invalid_or_expired_code":
        return "That code could not be verified. Check it or request a new one.";
      case "password_too_short":
        return "Choose a longer password and try again.";
      case "password_too_long":
        return "That password is longer than the service accepts.";
      case "password_matches_identifier":
        return "Choose a password that is different from your username and email.";
      case "rate_limited":
        return error.retryAfterSeconds === null
          ? "Too many requests. Wait a little, then try again."
          : `Too many requests. Try again in ${error.retryAfterSeconds} seconds.`;
      case "service_unavailable":
        return "This account service is temporarily unavailable. Try again later.";
      case "validation_error":
        return "Some details need attention. Check the fields and try again.";
      case "payload_too_large":
        return "This form is too large to submit. Shorten the details and try again.";
      case "invalid_credentials":
        return "The username or password is incorrect.";
      case "invalid_token":
      case "not_authenticated":
        return "Your sign-in is no longer valid. Sign in again to continue.";
      default:
        return "The request could not be completed. Try again.";
    }
  }
  return "The request could not be completed. Try again.";
}

export function AccountForm({
  mode,
}: {
  mode: AccountMode;
  /** Accepted for the page call sites; the card is the same embedded or not. */
  embedded?: boolean;
}) {
  const [fields, setFields] = useState<Partial<Record<FieldName, string>>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);
  const [fieldError, setFieldError] = useState<{
    field: string;
    message: string;
  } | null>(null);
  const [retryable, setRetryable] = useState(false);
  const [errorTick, setErrorTick] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);

  useFocusFirstError(formRef, errorTick);

  const passwordFieldName = mode === "reset" ? "new_password" : "password";

  const failWith = (reason: unknown) => {
    const message = errorText(reason);
    const field = errorFieldFor(reason, passwordFieldName);
    if (field === null) {
      setError(message);
      setFieldError(null);
    } else {
      setError(null);
      setFieldError({ field, message });
    }
    setRetryable(isRetryableAuthError(reason));
    setErrorTick((tick) => tick + 1);
  };

  useEffect(() => {
    const email = new URLSearchParams(window.location.search).get("email");
    if (email !== null) {
      setFields((current) => ({ ...current, email }));
    }
  }, []);

  useEffect(() => {
    if (cooldown === 0) {
      return;
    }
    const timer = window.setTimeout(
      () => setCooldown((value) => Math.max(0, value - 1)),
      1000,
    );
    return () => window.clearTimeout(timer);
  }, [cooldown]);

  const value = (name: FieldName) => fields[name] ?? "";
  const update = (name: FieldName, next: string) => {
    setFields((current) => ({ ...current, [name]: next }));
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) {
      return;
    }
    setError(null);
    setFieldError(null);
    setRetryable(false);
    setNotice(null);
    if (
      (mode === "register" || mode === "reset") &&
      value(passwordFieldName).length < PASSWORD_MIN_LENGTH
    ) {
      setFieldError({
        field: passwordFieldName,
        message: PASSWORD_TOO_SHORT_MESSAGE,
      });
      setErrorTick((tick) => tick + 1);
      return;
    }
    setBusy(true);
    try {
      const email = value("email").trim();
      if (mode === "register") {
        await registerAccount({
          username: value("username").trim(),
          email,
          password: value("password"),
          full_name: value("full_name").trim(),
          ...(value("workspace_name").trim() === ""
            ? {}
            : { workspace_name: value("workspace_name").trim() }),
        });
        setNotice(
          "If these details can be used, a verification message will arrive shortly.",
        );
      } else if (mode === "verify") {
        await verifyEmail({
          email,
          code: value("code").trim(),
          password: value("password"),
        });
        setNotice("Email verification is complete. You can now sign in.");
      } else if (mode === "forgot") {
        await requestPasswordReset(email);
        setNotice(
          "If this email can be used, password reset instructions will arrive shortly.",
        );
      } else {
        await resetPassword({
          email,
          code: value("code").trim(),
          new_password: value("new_password"),
        });
        setNotice("Your password has been updated. You can now sign in.");
      }
    } catch (reason) {
      failWith(reason);
      if (
        reason instanceof ApiError &&
        reason.code === "rate_limited" &&
        reason.retryAfterSeconds !== null
      ) {
        setCooldown(reason.retryAfterSeconds);
      }
    } finally {
      setBusy(false);
    }
  };

  const handleResend = async () => {
    if (busy || cooldown > 0 || value("email").trim() === "") {
      return;
    }
    setBusy(true);
    setError(null);
    setFieldError(null);
    setRetryable(false);
    setNotice(null);
    try {
      const result = await resendVerification(value("email").trim());
      setCooldown(result.retryAfterSeconds ?? 0);
      setNotice(
        "If this email can be used, a verification message will arrive shortly.",
      );
    } catch (reason) {
      failWith(reason);
      if (
        reason instanceof ApiError &&
        reason.code === "rate_limited" &&
        reason.retryAfterSeconds !== null
      ) {
        setCooldown(reason.retryAfterSeconds);
      }
    } finally {
      setBusy(false);
    }
  };

  const retrySubmit = () => formRef.current?.requestSubmit();

  const submitLabel =
    mode === "register"
      ? "Create account"
      : mode === "verify"
        ? "Verify email"
        : mode === "forgot"
          ? "Send instructions"
          : "Update password";

  return (
    <section
      aria-labelledby="maya-account-title"
      className="mx-auto w-full max-w-md min-w-0"
    >
      <AuthHeader titleId="maya-account-title" title={TITLES[mode]}>
        {mode === "verify" ? (
          <p className="m-0">
            Enter the 6-digit code from your email and the password chosen
            during registration.
          </p>
        ) : mode === "register" ? (
          <p className="m-0">
            A verification code is sent to your email address. Use it on the
            verify page to finish.
          </p>
        ) : mode === "forgot" ? (
          <p className="m-0">
            Enter your email address. If it can be used, you will get a reset
            code.
          </p>
        ) : (
          <p className="m-0">
            Enter your email address, the 6-digit code you received and a new
            password.
          </p>
        )}
      </AuthHeader>
      <form ref={formRef} className={AUTH_FORM_CLASS} onSubmit={handleSubmit}>
        {FIELD_SETS[mode].map((field, index) => {
          const fieldMessage =
            fieldError !== null && fieldError.field === field.name
              ? fieldError.message
              : null;
          if (field.type === "password") {
            return (
              <PasswordField
                key={field.name}
                label={field.label}
                name={field.name}
                autoComplete={field.autoComplete}
                maxLength={field.maxLength}
                required={field.required === true}
                disabled={busy}
                showPolicy={mode === "register" || mode === "reset"}
                value={value(field.name)}
                onChange={(next) => update(field.name, next)}
                error={fieldMessage}
              />
            );
          }
          return (
            <AuthField
              key={field.name}
              label={field.label}
              name={field.name}
              type={field.type ?? "text"}
              autoComplete={field.autoComplete}
              inputMode={field.inputMode}
              maxLength={field.maxLength}
              required={field.required === true}
              hint={field.hint}
              autoFocus={index === 0}
              disabled={busy}
              value={value(field.name)}
              onChange={(next) => update(field.name, next)}
              error={fieldMessage}
            />
          );
        })}
        {error === null ? null : (
          <FormAlert
            message={error}
            busy={busy}
            onRetry={retryable ? retrySubmit : undefined}
          />
        )}
        {notice === null ? null : <Notice tone="success">{notice}</Notice>}
        <Button
          type="submit"
          variant="primary"
          className="w-full"
          disabled={busy}
        >
          {busy ? "Submitting..." : submitLabel}
        </Button>
      </form>
      {mode === "verify" ? (
        <div className="mt-3">
          <Button
            variant="ghost"
            size="sm"
            onClick={() => void handleResend()}
            disabled={busy || cooldown > 0 || value("email").trim() === ""}
          >
            {cooldown > 0
              ? `Resend code in ${cooldown} seconds`
              : "Resend code"}
          </Button>
        </div>
      ) : null}
      <nav className={AUTH_LINKS_CLASS} aria-label="Account options">
        {mode === "register" ? (
          <>
            <Link
              className={AUTH_LINK_CLASS}
              href={`/verify?email=${encodeURIComponent(value("email"))}`}
            >
              Enter a verification code
            </Link>
            <Link className={AUTH_LINK_CLASS} href="/login">
              Sign in
            </Link>
          </>
        ) : mode === "verify" ? (
          <Link className={AUTH_LINK_CLASS} href="/login">
            Sign in
          </Link>
        ) : mode === "forgot" ? (
          <>
            <Link className={AUTH_LINK_CLASS} href="/reset-password">
              Enter a reset code
            </Link>
            <Link className={AUTH_LINK_CLASS} href="/login">
              Back to sign in
            </Link>
          </>
        ) : (
          <Link className={AUTH_LINK_CLASS} href="/login">
            Back to sign in
          </Link>
        )}
      </nav>
    </section>
  );
}
