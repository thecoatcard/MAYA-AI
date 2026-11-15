import Link from "next/link";
import React from "react";

export default function NotFound(): React.JSX.Element {
  return (
    <main className="flex min-h-dvh min-w-0 items-center justify-center bg-maya-bg p-4 text-maya-text sm:p-6">
      <section className="w-full max-w-md min-w-0 rounded-maya-lg border border-maya-border bg-maya-pane p-4 sm:p-6">
        <p className="m-0 font-mono text-maya-caption text-maya-muted">404</p>
        <h1 className="mt-1 mb-0 text-maya-heading font-semibold">
          This page does not exist
        </h1>
        <p className="mt-2 mb-0 text-maya-small text-maya-secondary">
          The address may be mistyped or the page may have moved.
        </p>
        <Link
          href="/"
          className="maya-button motion-interactive mt-4 inline-flex min-h-9 items-center justify-center rounded-maya-md border border-maya-accent bg-maya-accent px-4 text-maya-small font-medium text-maya-accent-text hover:border-maya-accent-hover hover:bg-maya-accent-hover pointer-coarse:min-h-11"
        >
          Back to the conversation
        </Link>
      </section>
    </main>
  );
}
