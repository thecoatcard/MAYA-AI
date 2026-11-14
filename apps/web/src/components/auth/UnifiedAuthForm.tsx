"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useRef, useState, type FormEvent } from "react";

import {
  ApiError,
  BackendUnreachableError,
  NetworkError,
} from "../../lib/api-client";
import {
  bootstrapOwner,
  completeSignup,
  getBootstrapStatus,
  safeRedirectPath,
  sendSignupOtp,
  signIn,
} from "../../lib/auth-client";
import { getAccessToken, setAccessToken } from "../../lib/auth-session";
import { Button } from "../ui/Button";
import { Notice } from "../ui/Feedback";
import {
  AUTH_FORM_CLASS,
  AUTH_LINKS_CLASS,
  AuthField,
  AuthHeader,
  FormAlert,
  PASSWORD_MAX_LENGTH,
  PASSWORD_MIN_LENGTH,
  PASSWORD_TOO_SHORT_MESSAGE,
  PasswordField,
  errorFieldFor,
  isRetryableAuthError,
  useFocusFirstError,
} from "./AuthParts";

export type AuthStep =
  "choice" | "password" | "signup_details" | "otp_verification" | "bootstrap";

type BackendState = "checking" | "ready" | "unreachable";

interface FieldError {
  readonly field: string;
  readonly message: string;
}

function isUnreachable(error: unknown): boolean {
  return (
    error instanceof BackendUnreachableError || error instanceof NetworkError
  );
}

function redirectTarget(): string {
  if (typeof window === "undefined") {
    return "/chat";
  }
  return safeRedirectPath(
    new URLSearchParams(window.location.search).get("redirect"),
    "/chat",
  );
}

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
        return "Authentication service is temporarily unavailable. Try again shortly.";
      default:
        return "The request could not be completed. Try again.";
    }
  }
  if (
    error instanceof NetworkError ||
    error instanceof BackendUnreachableError
  ) {
    return "MAYA could not be reached. Check the connection and try again.";
  }
  return "An unexpected error occurred. Try again.";
}

export function UnifiedAuthForm({
  initialIdentifier = "",
  initialStep = "choice",
  embedded = false,
}: {
  initialIdentifier?: string;
  initialStep?: AuthStep;
  embedded?: boolean;
}) {
  let router: {
    replace: (url: string) => void;
    push: (url: string) => void;
  } | null = null;
  try {
    // eslint-disable-next-line react-hooks/rules-of-hooks
    router = useRouter();
  } catch {
    // Outside App Router context (e.g. unit tests)
  }
  const [step, setStep] = useState<AuthStep>(initialStep);
  const [busy, setBusy] = useState(false);
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [fieldError, setFieldError] = useState<FieldError | null>(null);
  const [retryable, setRetryable] = useState(false);
  const [errorTick, setErrorTick] = useState(0);
  const [notice, setNotice] = useState<string | null>(null);
  const [cooldown, setCooldown] = useState(0);
  const formRef = useRef<HTMLFormElement>(null);

  // Form field states
  const [identifier, setIdentifier] = useState(initialIdentifier);
  const [password, setPassword] = useState("");
  const [email, setEmail] = useState("");
  const [username, setUsername] = useState("");
  const [code, setCode] = useState("");
  const [fullName, setFullName] = useState("");
  const [backend, setBackend] = useState<BackendState>("checking");
  const [checkNonce, setCheckNonce] = useState(0);

  useFocusFirstError(formRef, errorTick);

  useEffect(() => {
    if (!embedded && getAccessToken() !== null) {
      router?.replace("/chat");
    }
  }, [router, embedded]);

  useEffect(() => {
    let cancelled = false;
    setBackend("checking");
    getBootstrapStatus()
      .then((status) => {
        if (cancelled) return;
        setBackend("ready");
        if (status.available) {
          setStep("bootstrap");
        }
      })
      .catch((error: unknown) => {
        if (cancelled) return;
        // Only a clear connectivity failure blocks the form; any other error
        // falls through to the normal sign-in flow.
        setBackend(isUnreachable(error) ? "unreachable" : "ready");
      });
    return () => {
      cancelled = true;
    };
  }, [checkNonce]);

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

  const clearErrors = () => {
    setErrorMessage(null);
    setFieldError(null);
    setRetryable(false);
  };

  const fail = (error: unknown) => {
    const message = authErrorMessage(error);
    const field = errorFieldFor(error, "password");
    if (field === null) {
      setErrorMessage(message);
      setFieldError(null);
    } else {
      setErrorMessage(null);
      setFieldError({ field, message });
    }
    setRetryable(isRetryableAuthError(error));
    setErrorTick((tick) => tick + 1);
  };

  const failField = (field: string, message: string) => {
    setErrorMessage(null);
    setFieldError({ field, message });
    setRetryable(false);
    setErrorTick((tick) => tick + 1);
  };

  const errorFor = (field: string): string | null =>
    fieldError !== null && fieldError.field === field
      ? fieldError.message
      : null;

  const goToStep = (next: AuthStep) => {
    clearErrors();
    setNotice(null);
    setStep(next);
  };

  const resetToChoice = () => {
    setStep("choice");
    setPassword("");
    setCode("");
    clearErrors();
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
    clearErrors();
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
      fail(error);
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
    clearErrors();
    setBusy(true);

    try {
      const tokens = await signIn(identifier.trim(), password);
      setAccessToken(tokens.accessToken, tokens.expiresIn);
      setPassword("");
      router?.replace(redirectTarget());
    } catch (error) {
      fail(error);
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
    clearErrors();
    if (password.length < PASSWORD_MIN_LENGTH) {
      failField("password", PASSWORD_TOO_SHORT_MESSAGE);
      return;
    }
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
      router?.replace("/chat");
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  };

  const handleBootstrapSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (
      busy ||
      fullName.trim() === "" ||
      email.trim() === "" ||
      password === ""
    ) {
      return;
    }
    clearErrors();
    if (password.length < PASSWORD_MIN_LENGTH) {
      failField("password", PASSWORD_TOO_SHORT_MESSAGE);
      return;
    }
    setBusy(true);
    try {
      const tokens = await bootstrapOwner({
        fullName: fullName.trim(),
        email: email.trim(),
        password,
      });
      setAccessToken(tokens.accessToken, tokens.expiresIn);
      setPassword("");
      router?.replace(redirectTarget());
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  };

  const handleResendCode = async () => {
    if (busy || cooldown > 0 || email.trim() === "") {
      return;
    }
    clearErrors();
    setNotice(null);
    setBusy(true);

    try {
      const result = await sendSignupOtp(email.trim());
      setCooldown(result.retryAfterSeconds ?? 60);
      setNotice(
        "If a new verification code is available for this address, it will arrive shortly.",
      );
    } catch (error) {
      fail(error);
    } finally {
      setBusy(false);
    }
  };

  /** Re-submits the current form after a connection failure. */
  const retrySubmit = () => formRef.current?.requestSubmit();

  const alert =
    errorMessage !== null ? (
      <FormAlert
        message={errorMessage}
        busy={busy}
        onRetry={retryable ? retrySubmit : undefined}
      />
    ) : null;

  const backLink = (
    <nav className={AUTH_LINKS_CLASS} aria-label="Account options">
      <Button variant="ghost" size="sm" onClick={resetToChoice} disabled={busy}>
        Back to account choices
      </Button>
    </nav>
  );

  const content = (
    <section
      aria-labelledby="maya-auth-title"
      className="mx-auto w-full max-w-md min-w-0"
    >
      {backend === "unreachable" && (
        <div role="alert">
          <AuthHeader
            titleId="maya-auth-title"
            title="Can't reach the MAYA backend"
          >
            <p className="m-0">
              The service did not respond. Check that it is running, then try
              again.
            </p>
          </AuthHeader>
          <div className="mt-5">
            <Button
              variant="primary"
              className="w-full"
              onClick={() => setCheckNonce((n) => n + 1)}
            >
              Retry
            </Button>
          </div>
        </div>
      )}

      {backend !== "unreachable" && step === "bootstrap" && (
        <>
          <AuthHeader
            titleId="maya-auth-title"
            title="Create your local owner account"
          >
            <p className="m-0">
              This MAYA install has no accounts yet. The account you create here
              owns this local workspace.
            </p>
            <p className="m-0">
              There is no email verification step: the account is created and
              you are signed in straight away.
            </p>
          </AuthHeader>
          <form
            ref={formRef}
            className={AUTH_FORM_CLASS}
            onSubmit={handleBootstrapSubmit}
          >
            <AuthField
              label="Full name"
              name="full_name"
              autoComplete="name"
              autoFocus
              disabled={busy}
              maxLength={1024}
              value={fullName}
              onChange={setFullName}
            />
            <AuthField
              label="Email address"
              name="email"
              type="email"
              autoComplete="email"
              inputMode="email"
              disabled={busy}
              value={email}
              onChange={setEmail}
            />
            <PasswordField
              label="Password"
              name="password"
              autoComplete="new-password"
              disabled={busy}
              maxLength={PASSWORD_MAX_LENGTH}
              showPolicy
              value={password}
              onChange={setPassword}
              error={errorFor("password")}
            />
            {alert}
            <Button
              type="submit"
              variant="primary"
              className="w-full"
              disabled={
                busy ||
                fullName.trim() === "" ||
                email.trim() === "" ||
                password === ""
              }
            >
              {busy ? "Creating account..." : "Create owner account"}
            </Button>
          </form>
        </>
      )}

      {backend !== "unreachable" && step === "choice" && (
        <>
          <AuthHeader titleId="maya-auth-title" title="Welcome to MAYA">
            <p className="m-0">Choose how you would like to continue.</p>
          </AuthHeader>
          <div className={AUTH_FORM_CLASS}>
            <Button
              variant="primary"
              className="w-full"
              disabled={busy}
              onClick={() => goToStep("password")}
            >
              Sign in
            </Button>
            <Button
              variant="secondary"
              className="w-full"
              disabled={busy}
              onClick={() => goToStep("signup_details")}
            >
              Create account
            </Button>
          </div>
        </>
      )}

      {backend !== "unreachable" && step === "password" && (
        <>
          <AuthHeader titleId="maya-auth-title" title="Sign in" />
          <form
            ref={formRef}
            className={AUTH_FORM_CLASS}
            onSubmit={handlePasswordSubmit}
          >
            <AuthField
              label="Username or email"
              name="identifier"
              autoComplete="username"
              autoFocus
              disabled={busy}
              value={identifier}
              onChange={setIdentifier}
            />
            <PasswordField
              label="Password"
              name="password"
              autoComplete="current-password"
              disabled={busy}
              value={password}
              onChange={setPassword}
            />
            {alert}
            <Button
              type="submit"
              variant="primary"
              className="w-full"
              disabled={busy || identifier.trim() === "" || password === ""}
            >
              {busy ? "Signing in..." : "Sign in"}
            </Button>
          </form>
          <nav className={AUTH_LINKS_CLASS} aria-label="Account options">
            <Button
              variant="ghost"
              size="sm"
              onClick={resetToChoice}
              disabled={busy}
            >
              Back to account choices
            </Button>
            <Link
              href="/forgot-password"
              className="inline-flex min-h-8 items-center rounded-maya-sm text-maya-link underline underline-offset-4 hover:text-maya-accent-hover pointer-coarse:min-h-11"
            >
              Forgot password?
            </Link>
          </nav>
        </>
      )}

      {backend !== "unreachable" && step === "signup_details" && (
        <>
          <AuthHeader titleId="maya-auth-title" title="Create an account">
            <p className="m-0">
              Enter your details to request an email verification code.
            </p>
          </AuthHeader>
          <form
            ref={formRef}
            className={AUTH_FORM_CLASS}
            onSubmit={handleRequestCode}
          >
            <AuthField
              label="Email address"
              name="email"
              type="email"
              autoComplete="email"
              inputMode="email"
              autoFocus
              disabled={busy}
              placeholder="you@example.com"
              value={email}
              onChange={setEmail}
            />
            <AuthField
              label="Full name"
              name="full_name"
              autoComplete="name"
              disabled={busy}
              maxLength={1024}
              value={fullName}
              onChange={setFullName}
            />
            <AuthField
              label="Username"
              name="username"
              autoComplete="username"
              disabled={busy}
              maxLength={64}
              hint="3 to 32 characters: letters, numbers, dot, dash or underscore."
              value={username}
              onChange={setUsername}
            />
            {alert}
            <Button
              type="submit"
              variant="primary"
              className="w-full"
              disabled={
                busy ||
                email.trim() === "" ||
                username.trim() === "" ||
                fullName.trim() === ""
              }
            >
              {busy ? "Requesting code..." : "Continue"}
            </Button>
          </form>
          {backLink}
        </>
      )}

      {backend !== "unreachable" && step === "otp_verification" && (
        <>
          <AuthHeader
            titleId="maya-auth-title"
            title="Verify email and sign up"
          >
            <p className="m-0 [overflow-wrap:anywhere]">
              Enter the code if you received one for <strong>{email}</strong>,
              then finish creating your account.
            </p>
          </AuthHeader>
          {notice !== null ? (
            <div className="mt-4">
              <Notice tone="info">{notice}</Notice>
            </div>
          ) : null}
          <form
            ref={formRef}
            className={AUTH_FORM_CLASS}
            onSubmit={handleOtpVerificationSubmit}
          >
            <AuthField
              label="6-digit verification code"
              name="code"
              autoComplete="one-time-code"
              autoFocus
              disabled={busy}
              inputMode="numeric"
              maxLength={6}
              placeholder="123456"
              value={code}
              onChange={setCode}
              error={errorFor("code")}
            />
            <PasswordField
              label="Password"
              name="password"
              autoComplete="new-password"
              disabled={busy}
              maxLength={PASSWORD_MAX_LENGTH}
              showPolicy
              value={password}
              onChange={setPassword}
              error={errorFor("password")}
            />
            {alert}
            <Button
              type="submit"
              variant="primary"
              className="w-full"
              disabled={busy || code.trim().length !== 6 || password === ""}
            >
              {busy ? "Completing registration..." : "Complete registration"}
            </Button>
          </form>
          <div className="mt-3">
            <Button
              variant="ghost"
              size="sm"
              onClick={() => void handleResendCode()}
              disabled={busy || cooldown > 0}
            >
              {cooldown > 0
                ? `Resend code in ${cooldown} seconds`
                : "Resend code"}
            </Button>
          </div>
          {backLink}
        </>
      )}
    </section>
  );

  return <div className="w-full min-w-0">{content}</div>;
}
