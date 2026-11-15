"use client";

import Link from "next/link";
import React from "react";

import { Button } from "../components/ui/Button";

/**
 * Route error boundary. Plain words and a way back; the error message and stack are never shown
 * (they can hold paths or data). The digest is an opaque id the owner can quote when reporting.
 */
export default function ErrorBoundary({
  error,
  reset,
}: {
  error: Error & { digest?: string };
  reset: () => void;
}): React.JSX.Element {
  return (
    <main className="flex min-h-dvh min-w-0 items-center justify-center bg-maya-bg p-4 text-maya-text sm:p-6">
      <section
        role="alert"
        className="w-full max-w-md min-w-0 rounded-maya-lg border border-maya-danger bg-maya-pane p-4 sm:p-6"
      >
        <h1 className="m-0 text-maya-heading font-semibold">
          Something went wrong
        </h1>
        <p className="mt-2 mb-0 text-maya-small text-maya-secondary">
          This view stopped working. Your session and saved data are not
          affected. Try again, or go back to the conversation.
        </p>
        {error.digest !== undefined && error.digest !== "" && (
          <p className="mt-3 mb-0 text-maya-caption text-maya-muted [overflow-wrap:anywhere]">
            Reference: <span className="font-mono">{error.digest}</span>
          </p>
        )}
        <div className="mt-4 flex flex-wrap gap-3">
          <Button variant="primary" onClick={() => reset()}>
            Try again
          </Button>
          <Link
            href="/"
            className="maya-button motion-interactive inline-flex min-h-9 items-center justify-center rounded-maya-md border border-maya-border-strong bg-maya-raised px-4 text-maya-small font-medium text-maya-text hover:bg-maya-hover pointer-coarse:min-h-11"
          >
            Back to the conversation
          </Link>
        </div>
      </section>
    </main>
  );
}
