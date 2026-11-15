"use client";

import Link from "next/link";
import { useRouter } from "next/navigation";
import React, { useEffect } from "react";

export interface LegacyRedirectProps {
  /** Same-origin destination path, for example "/artifacts". */
  readonly to: string;
}

/**
 * Client-side redirect for a retired path. It is static-export friendly (no
 * middleware, no next.config redirect) and keeps the query string, so old
 * bookmarks such as /integrations?x=1 land on /connections?x=1.
 */
export function LegacyRedirect({ to }: LegacyRedirectProps): React.JSX.Element {
  const router = useRouter();

  useEffect(() => {
    router.replace(`${to}${window.location.search}`);
  }, [router, to]);

  return (
    <main className="flex min-h-dvh items-center justify-center bg-maya-bg p-6 text-sm text-maya-muted">
      <p role="status">
        This page moved. <Link href={to}>Continue</Link>
      </p>
    </main>
  );
}
