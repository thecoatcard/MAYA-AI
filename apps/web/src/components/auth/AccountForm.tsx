"use client";

import Link from "next/link";
import { useEffect, useState, type FormEvent } from "react";

import { ApiError, NetworkError } from "../../lib/api-client";
import {
  registerAccount,
  requestPasswordReset,
  resendVerification,
  resetPassword,
  verifyEmail,
} from "../../lib/auth-client";
import {
  ThemeToggle,
  readStoredTheme,
  storeTheme,
  type ThemePreference,
} from "../chat/ThemeToggle";

import "../chat/chat.css";

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
      case "email_not_verified":
        return "Verify your email address before signing in.";
      case "invalid_token":
      case "not_authenticated":
        return "Your sign-in is no longer valid. Sign in again to continue.";
      default:
        return "The request could not be completed. Try again.";
    }
  }
  return "The request could not be completed. Try again.";
}

export function AccountForm({ mode }: { mode: AccountMode }) {
  const [theme, setTheme] = useState<ThemePreference>("system");
  const [fields, setFields] = useState<Partial<Record<FieldName, string>>>({});
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);

  useEffect(() => {
    const stored = readStoredTheme();
    setTheme(stored);
    storeTheme(stored);
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

  const changeTheme = (next: ThemePreference) => {
    setTheme(next);
    storeTheme(next);
  };

  const value = (name: FieldName) => fields[name] ?? "";
  const update = (name: FieldName, next: string) => {
    setFields((current) => ({ ...current, [name]: next }));
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy) {
      return;
    }
    setBusy(true);
    setError(null);
    setNotice(null);
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
      setError(errorText(reason));
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
    setNotice(null);
    try {
      const result = await resendVerification(value("email").trim());
      setCooldown(result.retryAfterSeconds ?? 0);
      setNotice(
        "If this email can be used, a verification message will arrive shortly.",
      );
    } catch (reason) {
      setError(errorText(reason));
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

  return (
    <div className="maya-chat">
      <header className="maya-chat__header">
        <div className="maya-chat__header-inner">
          <Link href="/" className="maya-brand">
            MAYA AI
          </Link>
          <span className="maya-chat__divider" aria-hidden="true" />
          <h1 className="maya-chat__title">{TITLES[mode]}</h1>
          <div className="maya-chat__tools">
            <ThemeToggle value={theme} onChange={changeTheme} />
          </div>
        </div>
      </header>
      <main className="maya-chat__main">
        <section className="maya-account" aria-labelledby="maya-account-title">
          <h2 id="maya-account-title" className="maya-signedout__title">
            {TITLES[mode]}
          </h2>
          {mode === "verify" ? (
            <p className="maya-account__intro">
              Enter the 6-digit code from your email and the password chosen
              during registration.
            </p>
          ) : null}
          <form className="maya-signin__form" onSubmit={handleSubmit}>
            {FIELD_SETS[mode].map((field) => (
              <label className="maya-signin__field" key={field.name}>
                <span>{field.label}</span>
                <input
                  autoComplete={field.autoComplete}
                  inputMode={field.inputMode}
                  maxLength={field.maxLength}
                  name={field.name}
                  onChange={(event) => update(field.name, event.target.value)}
                  required={field.required}
                  type={field.type ?? "text"}
                  value={value(field.name)}
                  disabled={busy}
                />
              </label>
            ))}
            {error === null ? null : (
              <p className="maya-signin__error" role="alert">
                {error}
              </p>
            )}
            {notice === null ? null : (
              <p className="maya-account__notice" role="status">
                {notice}
              </p>
            )}
            <button
              type="submit"
              className="maya-btn maya-btn--primary maya-signin__submit maya-ripple"
              disabled={busy}
            >
              {busy
                ? "Submitting..."
                : mode === "register"
                  ? "Create account"
                  : mode === "verify"
                    ? "Verify email"
                    : mode === "forgot"
                      ? "Send instructions"
                      : "Update password"}
            </button>
          </form>
          {mode === "verify" ? (
            <div className="maya-account__resend">
              <button
                type="button"
                className="maya-btn maya-btn--quiet"
                onClick={() => void handleResend()}
                disabled={busy || cooldown > 0 || value("email").trim() === ""}
              >
                {cooldown > 0
                  ? `Resend code in ${cooldown} seconds`
                  : "Resend code"}
              </button>
            </div>
          ) : null}
          <nav className="maya-account__links" aria-label="Account options">
            {mode === "register" ? (
              <>
                <Link
                  href={`/verify?email=${encodeURIComponent(value("email"))}`}
                >
                  Enter a verification code
                </Link>
                <Link href="/login">Sign in</Link>
              </>
            ) : mode === "verify" ? (
              <Link href="/login">Sign in</Link>
            ) : mode === "forgot" ? (
              <>
                <Link href="/reset-password">Enter a reset code</Link>
                <Link href="/login">Back to sign in</Link>
              </>
            ) : (
              <Link href="/login">Back to sign in</Link>
            )}
          </nav>
          <Link href="/" className="maya-signedout__link">
            Back to overview
          </Link>
        </section>
      </main>
    </div>
  );
}
