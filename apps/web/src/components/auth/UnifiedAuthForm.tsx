"use client";

import { KeyRound, Layers3, MailCheck, UserPlus } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { ApiError, NetworkError } from "../../lib/api-client";
import { completeSignup, sendSignupOtp, signIn } from "../../lib/auth-client";
import { setAccessToken } from "../../lib/auth-session";
import {
  ThemeToggle,
  readStoredTheme,
  storeTheme,
  type ThemePreference,
} from "../chat/ThemeToggle";

import "../chat/chat.css";

export type AuthStep =
  "choice" | "password" | "signup_details" | "otp_verification";

function authErrorMessage(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.code) {
      case "invalid_credentials":
        return "The username or email and password could not be verified.";
      case "invalid_or_expired_code":
        return "That verification code is invalid or has expired. Request a new code or try again.";
      case "password_too_short":
        return "Choose a password of at least 12 characters.";
      case "password_too_long":
        return "That password is longer than the service accepts.";
      case "password_matches_identifier":
        return "Choose a password that does not match your username or email.";
      case "rate_limited":
        return error.retryAfterSeconds === null
          ? "Too many requests. Wait a moment, then try again."
          : `Too many requests. Try again in ${error.retryAfterSeconds} seconds.`;
      case "validation_error":
        return "Please check the entered details and ensure the format is valid.";
      case "service_unavailable":
        return "Authentication service is temporarily unavailable. Try again later.";
      default:
        return "The request could not be completed. Try again.";
    }
  }
  if (error instanceof NetworkError) {
    return "MAYA could not be reached. Check the connection and try again.";
  }
  return "An unexpected error occurred. Try again.";
}

export function UnifiedAuthForm({
  initialIdentifier = "",
}: {
  initialIdentifier?: string;
}) {
  const router = useRouter();
  const [theme, setTheme] = useState<ThemePreference>("system");
  const [step, setStep] = useState<AuthStep>("choice");
  const [busy, setBusy] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);

  // Form field states
  const [identifier, setIdentifier] = useState(initialIdentifier);
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [code, setCode] = useState("");
  const [fullName, setFullName] = useState("");

  useEffect(() => {
    const stored = readStoredTheme();
    setTheme(stored);
    storeTheme(stored);
  }, []);

  useEffect(() => {
    if (cooldown <= 0) {
      return;
    }
    const timer = window.setTimeout(
      () => setCooldown((val) => Math.max(0, val - 1)),
      1000,
    );
    return () => window.clearTimeout(timer);
  }, [cooldown]);

  const changeTheme = (next: ThemePreference) => {
    setTheme(next);
    storeTheme(next);
  };

  const resetToChoice = () => {
    setStep("choice");
    setPassword("");
    setCode("");
    setErrorMessage(null);
    setNotice(null);
  };

  const handleRequestCode = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (
      busy ||
      email.trim() === "" ||
      username.trim() === "" ||
      fullName.trim() === ""
    ) {
      return;
    }
    setErrorMessage(null);
    setNotice(null);
    setBusy(true);

    try {
      const otpResult = await sendSignupOtp(email.trim());
      setCooldown(otpResult.retryAfterSeconds ?? 60);
      setNotice(
        "If a verification code can be issued for this address, it will arrive shortly. You can also try signing in.",
      );
      setStep("otp_verification");
    } catch (error) {
      setErrorMessage(authErrorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  // Sign-in is explicitly chosen; no identifier-existence lookup is made.
  const handlePasswordSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (busy || password === "") {
      return;
    }
    setErrorMessage(null);
    setBusy(true);

    try {
      const tokens = await signIn(identifier.trim(), password);
      setAccessToken(tokens.accessToken, tokens.expiresIn);
      setPassword("");
      router.replace("/chat");
    } catch (error) {
      setErrorMessage(authErrorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  // Complete the user-selected account creation flow after code verification.
  const handleOtpVerificationSubmit = async (
    event: FormEvent<HTMLFormElement>,
  ) => {
    event.preventDefault();
    if (busy || code.trim() === "" || password === "") {
      return;
    }
    setErrorMessage(null);
    setBusy(true);

    try {
      const tokens = await completeSignup({
        email: email.trim(),
        code: code.trim(),
        username: username.trim(),
        password,
        full_name: fullName.trim() || username.trim(),
      });
      setAccessToken(tokens.accessToken, tokens.expiresIn);
      setPassword("");
      setCode("");
      router.replace("/chat");
    } catch (error) {
      setErrorMessage(authErrorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  const handleResendCode = async () => {
    if (busy || cooldown > 0 || email.trim() === "") {
      return;
    }
    setErrorMessage(null);
    setNotice(null);
    setBusy(true);

    try {
      const result = await sendSignupOtp(email.trim());
      setCooldown(result.retryAfterSeconds ?? 60);
      setNotice(
        "If a new verification code is available for this address, it will arrive shortly.",
      );
    } catch (error) {
      setErrorMessage(authErrorMessage(error));
    } finally {
      setBusy(false);
    }
  };

  return (
    <div
      className="maya-chat"
      data-maya-theme={theme === "system" ? undefined : theme}
    >
      <header className="maya-chat__header">
        <div className="maya-chat__header-inner">
          <Link href="/" className="maya-brand">
            <Layers3 aria-hidden="true" size={18} />
            MAYA AI
          </Link>
          <span className="maya-chat__divider" aria-hidden="true" />
          <h1 className="maya-chat__title">
            {step === "password"
              ? "Sign in"
              : step === "otp_verification" || step === "signup_details"
                ? "Create account"
                : "Welcome"}
          </h1>
          <div className="maya-chat__tools">
            <ThemeToggle value={theme} onChange={changeTheme} />
          </div>
        </div>
      </header>
      <main className="maya-chat__main">
        <section className="maya-signin" aria-labelledby="maya-auth-title">
          <div className="maya-signedout__icon" aria-hidden="true">
            {step === "password" ? (
              <KeyRound size={20} />
            ) : step === "otp_verification" ? (
              <MailCheck size={20} />
            ) : (
              <UserPlus size={20} />
            )}
          </div>

          {step === "choice" && (
            <>
              <h2 id="maya-auth-title" className="maya-signedout__title">
                Welcome to MAYA
              </h2>
              <p className="maya-signedout__body">
                Choose how you would like to continue.
              </p>
              <div className="maya-signin__form">
                <button
                  className="maya-btn maya-btn--primary maya-signin__submit maya-ripple"
                  disabled={busy}
                  onClick={() => {
                    setErrorMessage(null);
                    setStep("password");
                  }}
                  type="button"
                >
                  Sign in
                </button>
                <button
                  className="maya-btn maya-btn--quiet maya-signin__submit"
                  disabled={busy}
                  onClick={() => {
                    setErrorMessage(null);
                    setNotice(null);
                    setStep("signup_details");
                  }}
                  type="button"
                >
                  Create account
                </button>
              </div>
            </>
          )}

          {step === "password" && (
            <>
              <h2 id="maya-auth-title" className="maya-signedout__title">
                Sign in
              </h2>
              <form
                className="maya-signin__form"
                onSubmit={handlePasswordSubmit}
              >
                <label className="maya-signin__field">
                  <span>Username or email</span>
                  <input
                    autoCapitalize="none"
                    autoComplete="username"
                    autoFocus
                    disabled={busy}
                    name="identifier"
                    placeholder="Username or email"
                    required
                    value={identifier}
                    onChange={(e) => setIdentifier(e.target.value)}
                  />
                </label>
                <label className="maya-signin__field">
                  <span>Password</span>
                  <input
                    autoComplete="current-password"
                    disabled={busy}
                    name="password"
                    required
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                </label>
                {errorMessage !== null ? (
                  <p className="maya-signin__error" role="alert">
                    {errorMessage}
                  </p>
                ) : null}
                <button
                  type="submit"
                  className="maya-btn maya-btn--primary maya-signin__submit maya-ripple"
                  disabled={busy || identifier.trim() === "" || password === ""}
                >
                  {busy ? "Signing in..." : "Sign in"}
                </button>
              </form>
              <nav className="maya-account__links" aria-label="Account options">
                <button
                  type="button"
                  className="maya-btn maya-btn--quiet"
                  onClick={resetToChoice}
                  disabled={busy}
                >
                  Back to account choices
                </button>
                <Link href="/forgot-password">Forgot password?</Link>
              </nav>
            </>
          )}

          {step === "signup_details" && (
            <>
              <h2 id="maya-auth-title" className="maya-signedout__title">
                Create an account
              </h2>
              <p className="maya-signedout__body">
                Enter your details to request an email verification code.
              </p>
              <form className="maya-signin__form" onSubmit={handleRequestCode}>
                <label className="maya-signin__field">
                  <span>Email address</span>
                  <input
                    autoCapitalize="none"
                    autoComplete="email"
                    autoFocus
                    disabled={busy}
                    name="email"
                    placeholder="you@example.com"
                    required
                    type="email"
                    value={email}
                    onChange={(e) => setEmail(e.target.value)}
                  />
                </label>
                <label className="maya-signin__field">
                  <span>Full name</span>
                  <input
                    autoComplete="name"
                    disabled={busy}
                    maxLength={1024}
                    name="full_name"
                    placeholder="Your name"
                    required
                    value={fullName}
                    onChange={(e) => setFullName(e.target.value)}
                  />
                </label>
                <label className="maya-signin__field">
                  <span>Username</span>
                  <input
                    autoCapitalize="none"
                    autoComplete="username"
                    disabled={busy}
                    maxLength={64}
                    name="username"
                    required
                    value={username}
                    onChange={(e) => setUsername(e.target.value)}
                  />
                </label>
                {errorMessage !== null ? (
                  <p className="maya-signin__error" role="alert">
                    {errorMessage}
                  </p>
                ) : null}
                <button
                  type="submit"
                  className="maya-btn maya-btn--primary maya-signin__submit maya-ripple"
                  disabled={
                    busy ||
                    email.trim() === "" ||
                    username.trim() === "" ||
                    fullName.trim() === ""
                  }
                >
                  {busy ? "Requesting code..." : "Continue"}
                </button>
              </form>
              <nav className="maya-account__links" aria-label="Account options">
                <button
                  type="button"
                  className="maya-btn maya-btn--quiet"
                  onClick={resetToChoice}
                  disabled={busy}
                >
                  Back to account choices
                </button>
              </nav>
            </>
          )}

          {step === "otp_verification" && (
            <>
              <h2 id="maya-auth-title" className="maya-signedout__title">
                Verify email and sign up
              </h2>
              <p className="maya-signedout__body">
                Enter the code if you received one for <strong>{email}</strong>,
                then finish creating your account.
              </p>
              {notice !== null ? (
                <p className="maya-account__notice" role="status">
                  {notice}
                </p>
              ) : null}
              <form
                className="maya-signin__form"
                onSubmit={handleOtpVerificationSubmit}
              >
                <label className="maya-signin__field">
                  <span>6-digit verification code</span>
                  <input
                    autoComplete="one-time-code"
                    autoFocus
                    disabled={busy}
                    inputMode="numeric"
                    maxLength={6}
                    name="code"
                    placeholder="123456"
                    required
                    value={code}
                    onChange={(e) => setCode(e.target.value)}
                  />
                </label>
                <label className="maya-signin__field">
                  <span>Password</span>
                  <input
                    autoComplete="new-password"
                    disabled={busy}
                    maxLength={128}
                    name="password"
                    placeholder="At least 12 characters"
                    required
                    type="password"
                    value={password}
                    onChange={(e) => setPassword(e.target.value)}
                  />
                </label>
                {errorMessage !== null ? (
                  <p className="maya-signin__error" role="alert">
                    {errorMessage}
                  </p>
                ) : null}
                <button
                  type="submit"
                  className="maya-btn maya-btn--primary maya-signin__submit maya-ripple"
                  disabled={busy || code.trim().length !== 6 || password === ""}
                >
                  {busy
                    ? "Completing registration..."
                    : "Complete registration"}
                </button>
              </form>
              <div className="maya-account__resend">
                <button
                  type="button"
                  className="maya-btn maya-btn--quiet"
                  onClick={() => void handleResendCode()}
                  disabled={busy || cooldown > 0}
                >
                  {cooldown > 0
                    ? `Resend code in ${cooldown} seconds`
                    : "Resend code"}
                </button>
              </div>
              <nav className="maya-account__links" aria-label="Account options">
                <button
                  type="button"
                  className="maya-btn maya-btn--quiet"
                  onClick={resetToChoice}
                  disabled={busy}
                >
                  Back to account choices
                </button>
              </nav>
            </>
          )}

          <Link href="/" className="maya-signedout__link">
            Back to overview
          </Link>
        </section>
      </main>
    </div>
  );
}
