import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
} from "@testing-library/react";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ThemeToggle } from "../components/ui/ThemeToggle";
import {
  THEME_BOOTSTRAP_SCRIPT,
  THEME_STORAGE_KEY,
  parseTheme,
  readStoredTheme,
  useTheme,
} from "./theme";

function reset(): void {
  delete document.documentElement.dataset.theme;
  window.localStorage.clear();
}

beforeEach(reset);
afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  reset();
});

describe("theme storage", () => {
  it("defaults to dark when nothing is stored", () => {
    expect(readStoredTheme()).toBe("dark");
  });

  it("respects a stored light choice", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    expect(readStoredTheme()).toBe("light");
  });

  it("treats the old 'system' value and any other junk as dark", () => {
    for (const value of ["system", "", "LIGHT", "blue", "null"]) {
      window.localStorage.setItem(THEME_STORAGE_KEY, value);
      expect(readStoredTheme(), value).toBe("dark");
    }
    expect(parseTheme(undefined)).toBe("dark");
  });

  it("falls back to dark when storage throws (blocked or corrupt)", () => {
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    expect(readStoredTheme()).toBe("dark");
  });

  it("has no prefers-color-scheme listener", () => {
    const spy = vi.fn((query: string) => ({
      matches: false,
      media: query,
      addEventListener: vi.fn(),
      removeEventListener: vi.fn(),
      addListener: vi.fn(),
      removeListener: vi.fn(),
      onchange: null,
      dispatchEvent: vi.fn(),
    }));
    vi.stubGlobal("matchMedia", spy);
    renderHook(() => useTheme());
    expect(spy).not.toHaveBeenCalled();
  });
});

describe("bootstrap script (runs before first paint)", () => {
  function run(): void {
    new Function(THEME_BOOTSTRAP_SCRIPT)();
  }

  it("sets dark with nothing stored", () => {
    run();
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("sets light when light is stored", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    run();
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("sets dark for a stored 'system' and never throws when storage is blocked", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "system");
    run();
    expect(document.documentElement.dataset.theme).toBe("dark");
    vi.spyOn(Storage.prototype, "getItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    delete document.documentElement.dataset.theme;
    expect(run).not.toThrow();
    expect(document.documentElement.dataset.theme).toBe("dark");
  });
});

describe("ThemeToggle", () => {
  it("starts dark with a label that names the action", () => {
    render(<ThemeToggle />);
    const button = screen.getByRole("button", {
      name: "Switch to light theme",
    });
    expect(button).toHaveAttribute("title", "Switch to light theme");
    expect(document.documentElement.dataset.theme).toBe("dark");
  });

  it("adopts a stored light choice when the bootstrap script did not run", () => {
    window.localStorage.setItem(THEME_STORAGE_KEY, "light");
    render(<ThemeToggle />);
    expect(
      screen.getByRole("button", { name: "Switch to dark theme" }),
    ).toBeInTheDocument();
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("flips on click, persists, and the label follows the state", () => {
    render(<ThemeToggle />);
    fireEvent.click(
      screen.getByRole("button", { name: "Switch to light theme" }),
    );
    expect(document.documentElement.dataset.theme).toBe("light");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("light");
    fireEvent.click(
      screen.getByRole("button", { name: "Switch to dark theme" }),
    );
    expect(document.documentElement.dataset.theme).toBe("dark");
    expect(window.localStorage.getItem(THEME_STORAGE_KEY)).toBe("dark");
  });

  it("is a native focusable button with no aria-pressed double signal", () => {
    render(<ThemeToggle />);
    const button = screen.getByRole("button", {
      name: "Switch to light theme",
    });
    expect(button.tagName).toBe("BUTTON");
    expect(button).toHaveAttribute("type", "button");
    expect(button).not.toHaveAttribute("aria-pressed");
    button.focus();
    expect(button).toHaveFocus();
    // A native button turns Enter and Space into a click in a browser; jsdom does not.
    fireEvent.click(button);
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("keeps two toggles in sync and still works when storage is blocked", () => {
    vi.spyOn(Storage.prototype, "setItem").mockImplementation(() => {
      throw new Error("blocked");
    });
    render(
      <>
        <ThemeToggle />
        <ThemeToggle />
      </>,
    );
    act(() => {
      fireEvent.click(
        screen.getAllByRole("button", { name: "Switch to light theme" })[0],
      );
    });
    expect(
      screen.getAllByRole("button", { name: "Switch to dark theme" }),
    ).toHaveLength(2);
    expect(document.documentElement.dataset.theme).toBe("light");
  });

  it("renders both icons in a fixed box so nothing shifts, only one visible", () => {
    const { container } = render(<ThemeToggle />);
    const icons = container.querySelectorAll("svg");
    expect(icons).toHaveLength(2);
    for (const icon of icons)
      expect(icon).toHaveAttribute("aria-hidden", "true");
    expect(container.querySelector("button")?.className).toContain("size-8");
    expect(icons[1].getAttribute("class")).toContain("opacity-0");
  });
});
