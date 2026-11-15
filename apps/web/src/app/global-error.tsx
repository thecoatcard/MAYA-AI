"use client";

import React from "react";

import "./globals.css";
import { DEFAULT_THEME, THEME_BOOTSTRAP_SCRIPT } from "../lib/theme";

/**
 * Last-resort boundary for errors the root layout itself cannot survive. It replaces the root
 * layout, so it renders its own html and body and applies the saved theme. It never shows the
 * error message or a stack, only an opaque reference.
 */
export default function GlobalError({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}): React.JSX.Element {
  return (
    <html lang="en" data-theme={DEFAULT_THEME} suppressHydrationWarning>
      <head>
        <script>{THEME_BOOTSTRAP_SCRIPT}</script>
      </head>
      <body>
        <main className="flex min-h-dvh min-w-0 items-center justify-center bg-maya-bg p-4 text-maya-text sm:p-6">
          <section
            role="alert"
            className="w-full max-w-md min-w-0 rounded-maya-lg border border-maya-danger bg-maya-pane p-4 sm:p-6"
          >
            <h1 className="m-0 text-maya-heading font-semibold">
              MAYA could not start this window
            </h1>
            <p className="mt-2 mb-0 text-maya-small text-maya-secondary">
              Something failed while loading. Your saved data is not affected.
              Try again, or close and reopen MAYA.
            </p>
            {error.digest !== undefined && error.digest !== "" && (
              <p className="mt-3 mb-0 text-maya-caption text-maya-muted [overflow-wrap:anywhere]">
                Reference: <span className="font-mono">{error.digest}</span>
              </p>
            )}
            <div className="mt-4">
              <button
                type="button"
                onClick={() => reset()}
                className="maya-button motion-interactive inline-flex min-h-9 items-center justify-center rounded-maya-md border border-maya-accent bg-maya-accent px-4 text-maya-small font-medium text-maya-accent-text hover:border-maya-accent-hover hover:bg-maya-accent-hover pointer-coarse:min-h-11"
              >
                Try again
              </button>
            </div>
          </section>
        </main>
      </body>
    </html>
  );
}
