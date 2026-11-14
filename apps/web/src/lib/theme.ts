"use client";

import { useCallback, useEffect, useSyncExternalStore } from "react";

import { syncDesktopTheme } from "./desktop-bridge";

/**
 * Theme logic (UI design system, docs/architecture/UI_DESIGN_SYSTEM.md).
 *
 * Exactly two themes, dark and light. The choice is stored per device in localStorage and
 * defaults to dark when nothing valid is stored. There is no "system" mode and no
 * prefers-color-scheme listener: an old stored "system" (or any other value) reads as dark.
 *
 * Contract: the theme is the `data-theme` attribute on <html> ("dark" or "light"). The inline
 * bootstrap script (THEME_BOOTSTRAP_SCRIPT, mounted by app/layout.tsx) sets it before first
 * paint; ThemeToggle and useTheme keep it, the storage and the toggles in sync.
 */

export type Theme = "dark" | "light";

/** Per-viewer convenience only. The access token is never stored. */
export const THEME_STORAGE_KEY = "maya.theme";

export const DEFAULT_THEME: Theme = "dark";

/** Fired on window after a change so every toggle on the page re-reads the theme. */
const THEME_EVENT = "maya:theme-change";

/** Anything other than "light" (including the old "system" value) is dark. */
export function parseTheme(value: unknown): Theme {
  return value === "light" ? "light" : DEFAULT_THEME;
}

export function readStoredTheme(): Theme {
  try {
    return parseTheme(window.localStorage.getItem(THEME_STORAGE_KEY));
  } catch {
    return DEFAULT_THEME;
  }
}

/** Write the attribute on <html>. Safe to call repeatedly. */
export function applyTheme(theme: Theme): void {
  document.documentElement.dataset.theme = theme;
  syncDesktopTheme(theme);
}

/** Persist the choice. Blocked storage only means the choice lasts until the page closes. */
export function storeTheme(theme: Theme): void {
  try {
    window.localStorage.setItem(THEME_STORAGE_KEY, theme);
  } catch {
    // Private windows or blocked site data: nothing to do.
  }
}

/** The theme currently applied to the document (falls back to the default). */
export function currentTheme(): Theme {
  return parseTheme(document.documentElement.dataset.theme);
}

/**
 * Inline script for <head>: reads the stored choice and sets data-theme before the first paint.
 * It is plain ES5 so it runs everywhere, and it never throws. Keep it in sync with parseTheme.
 */
export const THEME_BOOTSTRAP_SCRIPT = `(function(){var t="dark";try{if(localStorage.getItem(${JSON.stringify(
  THEME_STORAGE_KEY,
)})==="light"){t="light"}}catch(e){}document.documentElement.setAttribute("data-theme",t)})();`;

function subscribe(onChange: () => void): () => void {
  const onStorage = (event: StorageEvent): void => {
    if (event.key === THEME_STORAGE_KEY || event.key === null) {
      applyTheme(readStoredTheme());
      onChange();
    }
  };
  window.addEventListener(THEME_EVENT, onChange);
  window.addEventListener("storage", onStorage);
  return () => {
    window.removeEventListener(THEME_EVENT, onChange);
    window.removeEventListener("storage", onStorage);
  };
}

function getServerSnapshot(): Theme {
  return DEFAULT_THEME;
}

export interface UseTheme {
  readonly theme: Theme;
  readonly setTheme: (next: Theme) => void;
  readonly toggleTheme: () => void;
}

/** Current theme plus setters. Every component using it updates together. */
export function useTheme(): UseTheme {
  const theme = useSyncExternalStore(
    subscribe,
    currentTheme,
    getServerSnapshot,
  );

  // Without the bootstrap script (tests, a page outside the root layout) adopt the stored choice.
  useEffect(() => {
    // The bootstrap script sets the attribute before paint; the desktop strip is told here.
    syncDesktopTheme(currentTheme());
    if (document.documentElement.dataset.theme === undefined) {
      applyTheme(readStoredTheme());
      window.dispatchEvent(new Event(THEME_EVENT));
    }
  }, []);

  const setTheme = useCallback((next: Theme): void => {
    applyTheme(next);
    storeTheme(next);
    window.dispatchEvent(new Event(THEME_EVENT));
  }, []);

  const toggleTheme = useCallback((): void => {
    setTheme(currentTheme() === "dark" ? "light" : "dark");
  }, [setTheme]);

  return { theme, setTheme, toggleTheme };
}
