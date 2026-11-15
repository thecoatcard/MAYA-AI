/**
 * The small bridge the Electron preload exposes as `window.maya` (apps/desktop/src/preload.cjs).
 * In a plain browser there is no bridge and these helpers return null.
 */

import { useEffect, useState } from "react";

export interface DesktopBridge {
  readonly isDesktop: true;
  /** Native folder dialog. Resolves the absolute path, or null when the person cancels. */
  readonly pickFolder: () => Promise<string | null>;
  /** Show a path inside a folder chosen with the dialog in the file manager. */
  readonly revealPath: (path: string) => Promise<boolean>;
  /** Download a stored artifact to a MAYA temp folder and open it with the PC's default app. */
  readonly openArtifact: (request: OpenArtifactRequest) => Promise<unknown>;
  /**
   * Create (true) or remove (false) the Windows Scheduled Task that wakes the PC to run schedules.
   * Preload API name: `window.maya.registerScheduleWake(enabled: boolean)`, resolving
   * `{ ok: boolean, reason?: string }`.
   */
  readonly registerScheduleWake?: (enabled: boolean) => Promise<unknown>;
}

/** What the renderer may ask the desktop shell to open: an artifact id, never a path. */
export interface OpenArtifactRequest {
  readonly artifactId: string;
  readonly fileName: string;
  readonly token: string;
}

/** Result of an open request. `reason` is a short machine code, shown to people as plain words. */
export interface OpenArtifactResult {
  readonly ok: boolean;
  readonly reason?:
    | "untrusted"
    | "bad-request"
    | "blocked-type"
    | "download-failed"
    | "open-failed";
}

type MayaWindow = {
  maya?: {
    isDesktop?: boolean;
    pickFolder?: unknown;
    revealPath?: unknown;
    openArtifact?: unknown;
    registerScheduleWake?: unknown;
    setTheme?: unknown;
  };
};

/** Tell the desktop shell the theme so its window-control strip matches. No-op in a browser. */
export function syncDesktopTheme(theme: "dark" | "light"): void {
  if (typeof window === "undefined") return;
  const maya = (window as unknown as MayaWindow).maya;
  if (maya?.isDesktop !== true || typeof maya.setTheme !== "function") return;
  try {
    void Promise.resolve(
      (maya.setTheme as (t: string) => unknown)(theme),
    ).catch(() => undefined);
  } catch {
    /* the strip keeps its old colour */
  }
}

/** The folder picker, or null when this page is not running inside the desktop app. */
export function getFolderPicker(): (() => Promise<string | null>) | null {
  if (typeof window === "undefined") return null;
  const maya = (window as unknown as MayaWindow).maya;
  if (maya?.isDesktop !== true || typeof maya.pickFolder !== "function") {
    return null;
  }
  const pick = maya.pickFolder as () => Promise<unknown>;
  return async () => {
    const result = await pick();
    return typeof result === "string" && result !== "" ? result : null;
  };
}

/** "Open folder", or null when there is no desktop bridge (a browser cannot open local folders). */
export function getPathRevealer(): ((path: string) => Promise<boolean>) | null {
  if (typeof window === "undefined") return null;
  const maya = (window as unknown as MayaWindow).maya;
  if (maya?.isDesktop !== true || typeof maya.revealPath !== "function") {
    return null;
  }
  const reveal = maya.revealPath as (path: string) => Promise<unknown>;
  return async (path) => (await reveal(path)) === true;
}

/**
 * "Open in the default app", or null when there is no desktop bridge (a browser cannot open local
 * files). It never resolves to a thrown error: any failure is `{ ok: false }`.
 */
export function getArtifactOpener():
  ((request: OpenArtifactRequest) => Promise<OpenArtifactResult>) | null {
  if (typeof window === "undefined") return null;
  const maya = (window as unknown as MayaWindow).maya;
  if (maya?.isDesktop !== true || typeof maya.openArtifact !== "function") {
    return null;
  }
  const open = maya.openArtifact as (
    r: OpenArtifactRequest,
  ) => Promise<unknown>;
  return async (request) => {
    try {
      const result = (await open(request)) as OpenArtifactResult | null;
      return result !== null && typeof result === "object" && result.ok === true
        ? { ok: true }
        : { ok: false, reason: result?.reason ?? "open-failed" };
    } catch {
      return { ok: false, reason: "open-failed" };
    }
  };
}

/** Result of asking the desktop shell to register or remove the wake task. */
export interface ScheduleWakeResult {
  readonly ok: boolean;
  /** A short reason, shown to people as plain words. */
  readonly reason?: string;
}

/** True only inside the desktop app whose preload offers `registerScheduleWake`. */
export function canRegisterScheduleWake(): boolean {
  if (typeof window === "undefined") return false;
  const maya = (window as unknown as MayaWindow).maya;
  return (
    maya?.isDesktop === true && typeof maya.registerScheduleWake === "function"
  );
}

/**
 * Ask the desktop app to create (true) or remove (false) the wake task. Never throws: a browser,
 * a missing bridge or any failure is `{ ok: false, reason }`.
 */
export async function registerScheduleWake(
  enabled: boolean,
): Promise<ScheduleWakeResult> {
  if (!canRegisterScheduleWake()) return { ok: false, reason: "not-desktop" };
  const maya = (window as unknown as MayaWindow).maya!;
  try {
    const result = (await (
      maya.registerScheduleWake as (e: boolean) => Promise<unknown>
    )(enabled)) as { ok?: unknown; reason?: unknown } | null;
    if (result !== null && typeof result === "object" && result.ok === true) {
      return { ok: true };
    }
    return {
      ok: false,
      reason: typeof result?.reason === "string" ? result.reason : "failed",
    };
  } catch {
    return { ok: false, reason: "failed" };
  }
}

export interface DesktopBridgeState {
  readonly isDesktop: boolean;
  readonly pickFolder: (() => Promise<string | null>) | null;
  readonly revealPath: ((path: string) => Promise<boolean>) | null;
}

/**
 * React hook exposing desktop bridge capabilities (e.g. native folder picker).
 */
export function useDesktopBridge(): DesktopBridgeState {
  const [bridge, setBridge] = useState<DesktopBridgeState>(() => {
    const picker = getFolderPicker();
    const revealer = getPathRevealer();
    return {
      isDesktop: picker !== null,
      pickFolder: picker,
      revealPath: revealer,
    };
  });

  useEffect(() => {
    const picker = getFolderPicker();
    const revealer = getPathRevealer();
    setBridge({
      isDesktop: picker !== null,
      pickFolder: picker,
      revealPath: revealer,
    });
  }, []);

  return bridge;
}
