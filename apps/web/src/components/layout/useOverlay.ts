"use client";

import { useEffect, useRef, useState } from "react";

const FOCUSABLE =
  'a[href], button:not([disabled]), input:not([disabled]):not([type="hidden"]), select:not([disabled]), textarea:not([disabled]), [tabindex]:not([tabindex="-1"])';

function focusables(container: HTMLElement): HTMLElement[] {
  return Array.from(container.querySelectorAll<HTMLElement>(FOCUSABLE)).filter(
    (el) =>
      !el.hasAttribute("hidden") && el.getAttribute("aria-hidden") !== "true",
  );
}

/**
 * Behaviour shared by the shell's drawers and slide-overs: while `open`, focus moves into the
 * container, Tab and Shift+Tab stay inside it, Escape calls `onClose`, and when it closes focus
 * returns to the element that had it before (normally the button that opened the overlay).
 */
export function useOverlay(
  open: boolean,
  onClose: () => void,
  containerRef: React.RefObject<HTMLElement | null>,
): void {
  const onCloseRef = useRef(onClose);
  useEffect(() => {
    onCloseRef.current = onClose;
  });

  useEffect(() => {
    if (!open) return undefined;
    const container = containerRef.current;
    if (container === null) return undefined;
    const previous =
      document.activeElement instanceof HTMLElement
        ? document.activeElement
        : null;

    // Do not steal focus if a child already focused something
    if (
      !container.contains(document.activeElement) ||
      document.activeElement === container
    ) {
      const first = focusables(container)[0];
      if (first !== undefined) first.focus();
      else {
        container.tabIndex = -1;
        container.focus();
      }
    }

    const onKeyDown = (event: KeyboardEvent): void => {
      if (event.key === "Escape") {
        event.stopPropagation();
        onCloseRef.current();
        return;
      }
      if (event.key !== "Tab") return;
      const items = focusables(container);
      if (items.length === 0) {
        event.preventDefault();
        return;
      }
      const head = items[0];
      const tail = items[items.length - 1];
      const active = document.activeElement;
      if (!container.contains(active)) {
        event.preventDefault();
        head.focus();
      } else if (event.shiftKey && (active === head || active === container)) {
        event.preventDefault();
        tail.focus();
      } else if (!event.shiftKey && active === tail) {
        event.preventDefault();
        head.focus();
      }
    };
    document.addEventListener("keydown", onKeyDown, true);
    return () => {
      document.removeEventListener("keydown", onKeyDown, true);
      if (previous !== null && previous.isConnected) previous.focus();
    };
  }, [open, containerRef]);
}

export type ShellMode = "full" | "compact" | "narrow" | "mobile";

/** >=1280 full; 1024-1279 compact (same layout as full, kept for the width tests); 760-1023 narrow (icon rail); <760 mobile (drawer). */
const QUERIES = {
  full: "(min-width: 1280px)",
  compact: "(min-width: 1024px)",
  narrow: "(min-width: 760px)",
} as const;

function readMode(): ShellMode {
  if (
    typeof window === "undefined" ||
    typeof window.matchMedia !== "function"
  ) {
    return "full";
  }
  if (window.matchMedia(QUERIES.full).matches) return "full";
  if (window.matchMedia(QUERIES.compact).matches) return "compact";
  if (window.matchMedia(QUERIES.narrow).matches) return "narrow";
  return "mobile";
}

/** The shell's layout mode, kept in step with the window width (and with zoom, which changes it). */
export function useShellMode(): ShellMode {
  const [mode, setMode] = useState<ShellMode>("full");
  useEffect(() => {
    if (typeof window.matchMedia !== "function") return undefined;
    const lists = Object.values(QUERIES).map((q) => window.matchMedia(q));
    const update = (): void => setMode(readMode());
    update();
    for (const list of lists) list.addEventListener("change", update);
    return () => {
      for (const list of lists) list.removeEventListener("change", update);
    };
  }, []);
  return mode;
}

/** Sets <html data-paused> while the document is hidden so CSS loops (orb, shimmer) stop. */
export function usePauseWhenHidden(): void {
  useEffect(() => {
    const root = document.documentElement;
    const sync = (): void => {
      if (document.hidden) root.setAttribute("data-paused", "");
      else root.removeAttribute("data-paused");
    };
    sync();
    document.addEventListener("visibilitychange", sync);
    return () => {
      document.removeEventListener("visibilitychange", sync);
      root.removeAttribute("data-paused");
    };
  }, []);
}
