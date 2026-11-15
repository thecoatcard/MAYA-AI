"use client";

import React, { useEffect, useState } from "react";

import { AuthView } from "../auth/AuthView";
import { Button } from "../ui/Button";
import { Notice, Skeleton } from "../ui/Feedback";
import { useWorkspace } from "./WorkspaceProvider";

/** After this long the start-up screen admits it is slow and offers Retry. */
export const SLOW_BOOT_MS = 8000;

/** Plain-language version of the machine reason from /readyz. The raw text is never shown. */
export function describeBackendReason(reason: string | undefined): string {
  if (reason === undefined) return "The backend did not answer.";
  const http = /^status_(\d{3})$/.exec(reason);
  if (http !== null) {
    return `The backend answered with HTTP ${http[1]}, so it is not ready to serve requests.`;
  }
  if (reason === "not_ready" || reason === "unavailable") {
    return "The backend is running but reports that it is not ready yet.";
  }
  return "The backend did not answer. It may be stopped or still starting.";
}

export type GateState =
  "booting" | "backend_unreachable" | "signed_out" | "ready";

/**
 * Decides what the window shows before the workspace is usable:
 *  - booting: backend check or session restore still running;
 *  - backend_unreachable: backend not reachable and nobody is signed in;
 *  - signed_out: the existing auth view, framed as part of the app;
 *  - ready: the children (the persistent shell and the routes).
 * A backend that goes away after sign-in does not eject the user; the status
 * bar reports it instead.
 */
export function useGateState(): GateState {
  const { auth, backend } = useWorkspace();
  if (backend.state === "checking") return "booting";
  if (backend.state === "unreachable" && !auth.isAuthenticated) {
    return "backend_unreachable";
  }
  if (auth.isLoading) return "booting";
  if (!auth.isAuthenticated) return "signed_out";
  return "ready";
}

function GateFrame({
  children,
  busy = false,
  label,
}: {
  readonly children: React.ReactNode;
  readonly busy?: boolean;
  readonly label: string;
}): React.JSX.Element {
  const [controls, setControls] = useState<"left" | "right" | null>(null);
  useEffect(() => {
    const maya = (
      window as unknown as { maya?: { isDesktop?: boolean; platform?: string } }
    ).maya;
    if (maya?.isDesktop === true) {
      setControls(maya.platform === "darwin" ? "left" : "right");
    }
  }, []);
  return (
    <div className="flex h-dvh min-w-0 flex-col bg-maya-bg text-maya-text">
      <header
        className="maya-drag maya-titlebar flex shrink-0 items-center border-b border-maya-border bg-maya-pane"
        style={{ minHeight: "var(--maya-titlebar-height)" }}
        data-desktop-controls={controls ?? undefined}
      >
        <span className="text-maya-small font-semibold tracking-tight">
          MAYA
        </span>
      </header>
      <main
        id="maya-main"
        aria-busy={busy}
        aria-label={label}
        className="flex min-h-0 min-w-0 flex-1 overflow-y-auto p-4 sm:p-6"
      >
        <div className="m-auto flex w-full min-w-0 justify-center">
          {children}
        </div>
      </main>
    </div>
  );
}

export function AppGate({
  children,
}: {
  readonly children: React.ReactNode;
}): React.JSX.Element {
  const state = useGateState();
  const { backend, sessionExpired } = useWorkspace();
  const [retrying, setRetrying] = useState<boolean>(false);
  const [slow, setSlow] = useState<boolean>(false);

  useEffect(() => {
    if (state !== "booting") {
      setSlow(false);
      return undefined;
    }
    const timer = setTimeout(() => setSlow(true), SLOW_BOOT_MS);
    return () => clearTimeout(timer);
  }, [state]);

  const retry = (): void => {
    setRetrying(true);
    void backend.recheck().finally(() => setRetrying(false));
  };

  if (state === "ready") {
    return <>{children}</>;
  }

  if (state === "booting") {
    return (
      <GateFrame busy label="Starting MAYA">
        <div
          className="grid w-full max-w-sm gap-3"
          data-testid="gate-booting-card"
        >
          <p
            role="status"
            data-testid="gate-booting"
            className="text-maya-small text-maya-muted"
          >
            {slow
              ? "Still starting. The backend is taking longer than usual to answer."
              : "Starting MAYA..."}
          </p>
          <Skeleton lines={3} />
          {slow && (
            <div>
              <Button variant="secondary" loading={retrying} onClick={retry}>
                Retry
              </Button>
            </div>
          )}
        </div>
      </GateFrame>
    );
  }

  if (state === "backend_unreachable") {
    return (
      <GateFrame label="Backend unreachable">
        <section
          data-testid="gate-unreachable"
          className="w-full max-w-md min-w-0 rounded-maya-lg border border-maya-border bg-maya-pane p-4 sm:p-6"
        >
          <h1 className="text-maya-heading font-semibold">
            Can&apos;t reach the MAYA backend
          </h1>
          <p className="mt-2 text-maya-small text-maya-secondary">
            MAYA needs its backend running to sign you in. If this is the first
            run, start the backend service, then try again; you will be asked to
            create the owner account the first time it is reachable.
          </p>
          <p
            className="mt-2 text-maya-caption text-maya-muted [overflow-wrap:anywhere]"
            data-testid="gate-unreachable-reason"
          >
            {describeBackendReason(backend.reason)}
          </p>
          <div className="mt-4">
            <Button variant="primary" loading={retrying} onClick={retry}>
              Retry
            </Button>
          </div>
        </section>
      </GateFrame>
    );
  }

  return (
    <GateFrame label="Sign in">
      <div
        data-testid="gate-signed-out"
        className="grid w-full max-w-md min-w-0 gap-3"
      >
        {sessionExpired && (
          <Notice tone="warning" title="Your session ended">
            You were signed out because your session expired or was revoked.
            Sign in again to continue. Anything you had not sent was not saved.
          </Notice>
        )}
        <div className="min-w-0 rounded-maya-lg border border-maya-border bg-maya-pane p-4 sm:p-6">
          <AuthView initialStep="password" />
        </div>
      </div>
    </GateFrame>
  );
}
