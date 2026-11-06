"use client";

import { Monitor, Moon, Sun } from "lucide-react";

export type ThemePreference = "system" | "light" | "dark";

/** Per-viewer convenience only. The access token is never stored. */
export const THEME_STORAGE_KEY = "maya.theme";

const OPTIONS = [
  { value: "system", label: "Follow system theme", Icon: Monitor },
  { value: "light", label: "Light theme", Icon: Sun },
  { value: "dark", label: "Dark theme", Icon: Moon },
] as const;

export function readStoredTheme(): ThemePreference {
  try {
    const stored = window.localStorage.getItem(THEME_STORAGE_KEY);
    return stored === "light" || stored === "dark" ? stored : "system";
  } catch {
    return "system";
  }
}

export function storeTheme(theme: ThemePreference): void {
  const app = document.querySelector<HTMLElement>(".maya-app");
  if (app !== null) {
    if (theme === "system") {
      delete app.dataset.mayaTheme;
    } else {
      app.dataset.mayaTheme = theme;
    }
  }
  try {
    if (theme === "system") {
      window.localStorage.removeItem(THEME_STORAGE_KEY);
    } else {
      window.localStorage.setItem(THEME_STORAGE_KEY, theme);
    }
  } catch {
    // Storage can be blocked (private windows, site settings). The choice then
    // simply lasts until the page is closed.
  }
}

export interface ThemeToggleProps {
  value: ThemePreference;
  onChange: (next: ThemePreference) => void;
}

export function ThemeToggle({ value, onChange }: ThemeToggleProps) {
  return (
    <div className="maya-theme" role="group" aria-label="Colour theme">
      {OPTIONS.map(({ value: option, label, Icon }) => (
        <button
          key={option}
          type="button"
          className="maya-theme__option"
          aria-pressed={value === option}
          aria-label={label}
          title={label}
          onClick={() => onChange(option)}
        >
          <Icon aria-hidden="true" size={16} />
        </button>
      ))}
    </div>
  );
}
