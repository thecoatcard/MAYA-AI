"use client";

import { KeyRound, Layers3 } from "lucide-react";
import Link from "next/link";
import { useRouter } from "next/navigation";
import { useEffect, useState, type FormEvent } from "react";

import { ApiError, NetworkError } from "../../lib/api-client";
import { signIn } from "../../lib/auth-client";
import { setAccessToken } from "../../lib/auth-session";
import {
  ThemeToggle,
  readStoredTheme,
  storeTheme,
  type ThemePreference,
} from "../chat/ThemeToggle";

import "../chat/chat.css";

function signInMessage(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.code === "invalid_credentials") {
      return "The username or password is incorrect.";
    }
    if (error.code === "email_not_verified") {
      return "Verify your email address before signing in.";
    }
    if (error.code === "rate_limited") {
      return "Too many sign-in attempts. Wait a moment, then try again.";
    }
  }
  if (error instanceof NetworkError) {
    return "MAYA could not be reached. Check the connection and try again.";
  }
  return "Sign-in could not be completed. Try again.";
}

export function SignInForm() {
  const router = useRouter();
  const [theme, setTheme] = useState<ThemePreference>("system");
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [errorMessage, setErrorMessage] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    const stored = readStoredTheme();
    setTheme(stored);
    storeTheme(stored);
  }, []);

  const changeTheme = (next: ThemePreference) => {
    setTheme(next);
    storeTheme(next);
  };

  const handleSubmit = async (event: FormEvent<HTMLFormElement>) => {
    event.preventDefault();
    if (submitting) {
      return;
    }
    setErrorMessage(null);
    setSubmitting(true);
    try {
      const tokens = await signIn(username.trim(), password);
      setAccessToken(tokens.accessToken, tokens.expiresIn);
      setPassword("");
      router.replace("/chat");
    } catch (error) {
      setErrorMessage(signInMessage(error));
    } finally {
      setSubmitting(false);
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
          <h1 className="maya-chat__title">Sign in</h1>
          <div className="maya-chat__tools">
            <ThemeToggle value={theme} onChange={changeTheme} />
          </div>
        </div>
      </header>
      <main className="maya-chat__main">
        <section className="maya-signin" aria-labelledby="maya-signin-title">
          <div className="maya-signedout__icon" aria-hidden="true">
            <KeyRound size={20} />
          </div>
          <h2 id="maya-signin-title" className="maya-signedout__title">
            Welcome back
          </h2>
          <p className="maya-signedout__body">
            Sign in with your MAYA account to continue to chat.
          </p>
          <form className="maya-signin__form" onSubmit={handleSubmit}>
            <label className="maya-signin__field">
              <span>Username</span>
              <input
                autoComplete="username"
                autoCapitalize="none"
                name="username"
                required
                value={username}
                onChange={(event) => setUsername(event.target.value)}
                disabled={submitting}
              />
            </label>
            <label className="maya-signin__field">
              <span>Password</span>
              <input
                autoComplete="current-password"
                name="password"
                type="password"
                required
                value={password}
                onChange={(event) => setPassword(event.target.value)}
                disabled={submitting}
              />
            </label>
            {errorMessage !== null ? (
              <p className="maya-signin__error" role="alert">
                {errorMessage}
              </p>
            ) : null}
            <button
              type="submit"
              className="maya-btn maya-btn--primary maya-signin__submit"
              disabled={submitting || username.trim() === "" || password === ""}
            >
              {submitting ? "Signing in..." : "Sign in"}
            </button>
          </form>
          <nav className="maya-account__links" aria-label="Account options">
            <Link href="/register">Create account</Link>
            <Link href="/verify">Verify email</Link>
            <Link href="/forgot-password">Forgot password?</Link>
          </nav>
          <Link href="/" className="maya-signedout__link">
            Back to overview
          </Link>
        </section>
      </main>
    </div>
  );
}
