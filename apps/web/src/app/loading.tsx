import React from "react";

import { Skeleton } from "../components/ui/Feedback";

/** Root boot screen: shown while a route segment loads. Honest text, no invented progress. */
export default function Loading(): React.JSX.Element {
  return (
    <main
      aria-busy="true"
      className="flex min-h-dvh min-w-0 items-center justify-center bg-maya-bg p-4 text-maya-text sm:p-6"
    >
      <div className="grid w-full max-w-sm min-w-0 gap-3">
        <p role="status" className="m-0 text-maya-small text-maya-muted">
          Loading...
        </p>
        <Skeleton lines={3} />
      </div>
    </main>
  );
}
