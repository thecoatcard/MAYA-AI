"use client";

import { Moon, Sun } from "lucide-react";
import React from "react";

import { useTheme } from "../../lib/theme";

export interface ThemeToggleProps {
  readonly className?: string;
}

/**
 * The one control that switches dark and light. A single icon button: a sun while dark (press
 * for light), a moon while light (press for dark). The label states the action and changes with
 * the state, so it is not also an aria-pressed toggle (that would read twice). Both icons stay
 * mounted in a fixed box, so nothing shifts; they cross-fade and turn a quarter under 200 ms,
 * and under reduced motion the swap is instant (the duration tokens collapse to 0).
 */
export function ThemeToggle({
  className = "",
}: ThemeToggleProps): React.JSX.Element {
  const { theme, toggleTheme } = useTheme();
  const dark = theme === "dark";
  const label = dark ? "Switch to light theme" : "Switch to dark theme";

  return (
    <button
      type="button"
      aria-label={label}
      title={label}
      data-theme-toggle=""
      onClick={toggleTheme}
      className={`maya-button motion-interactive relative inline-grid size-8 shrink-0 place-items-center rounded-maya-md border border-maya-border-strong bg-transparent text-maya-text hover:bg-maya-hover active:bg-maya-raised pointer-coarse:size-11 ${className}`.trim()}
    >
      <Sun
        aria-hidden="true"
        size={16}
        className={`motion-swap absolute ${dark ? "opacity-100 rotate-0" : "opacity-0 rotate-90"}`}
      />
      <Moon
        aria-hidden="true"
        size={16}
        className={`motion-swap absolute ${dark ? "opacity-0 -rotate-90" : "opacity-100 rotate-0"}`}
      />
    </button>
  );
}
