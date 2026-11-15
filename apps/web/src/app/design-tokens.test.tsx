import { existsSync, readFileSync, statSync } from "node:fs";
import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { evaluate, parseThemes } from "../../scripts/design-tokens.mjs";

const css = readFileSync(join(__dirname, "globals.css"), "utf8");
const root = join(__dirname, "..", "..");

describe("design tokens: contrast (WCAG 2.2 AA, computed from globals.css)", () => {
  const rows = evaluate(css);

  it("checks a real set of pairs in both themes", () => {
    const dark = rows.filter((r) => r.theme === "dark").length;
    expect(dark).toBeGreaterThan(60);
    expect(rows.filter((r) => r.theme === "light").length).toBe(dark);
  });

  it("passes every text pair at 4.5:1 and every UI pair at 3:1 in dark and light", () => {
    const failed = rows
      .filter((r) => !r.pass)
      .map((r) => `${r.theme}: ${r.label} ${r.ratio.toFixed(2)} < ${r.min}`);
    expect(failed).toEqual([]);
  });

  it("makes dark the default and light a full override", () => {
    const { dark, light } = parseThemes(css);
    expect(dark.bg).not.toBe(light.bg);
    expect(dark.text).not.toBe(light.text);
    expect(dark.accent).not.toBe(light.accent);
  });
});

describe("design tokens: structure", () => {
  it("defines every token group the shells need", () => {
    for (const token of [
      "--maya-bg",
      "--maya-pane",
      "--maya-raised",
      "--maya-overlay",
      "--maya-text",
      "--maya-text-secondary",
      "--maya-text-muted",
      "--maya-accent-text",
      "--maya-border",
      "--maya-border-strong",
      "--maya-focus",
      "--maya-accent",
      "--maya-accent-hover",
      "--maya-accent-active",
      "--maya-accent-soft",
      "--maya-success-soft",
      "--maya-warning-soft",
      "--maya-danger-soft",
      "--maya-info-soft",
      "--maya-selection",
      "--maya-scroll-thumb",
      "--maya-z-base",
      "--maya-z-sticky",
      "--maya-z-nav",
      "--maya-z-dropdown",
      "--maya-z-modal",
      "--maya-z-toast",
      "--maya-motion-scale",
      "--maya-dur-instant",
      "--maya-dur-fast",
      "--maya-dur-base",
      "--maya-dur-slow",
      "--maya-nav-width",
      "--maya-sidebar-width",
      "--maya-header-height",
      "--maya-radius-sm",
      "--maya-shadow-1",
      "--maya-text-body",
      "--maya-text-caption",
      "--maya-space-4",
    ]) {
      expect(css, token).toContain(`${token}:`);
    }
  });

  it("keeps the legacy token names alive as aliases", () => {
    for (const token of [
      "--maya-surface",
      "--maya-control-border",
      "--maya-glass-surface",
      "--maya-code-bg",
    ]) {
      expect(css, token).toContain(`${token}:`);
    }
  });

  it("collapses motion under reduced motion and offers a motion-none guard", () => {
    expect(css).toMatch(
      /prefers-reduced-motion: reduce\)\s*\{[^}]*--maya-motion-scale:\s*0/s,
    );
    expect(css).toContain(".motion-none");
  });

  it("supports forced colours", () => {
    expect(css).toContain("forced-colors: active");
    expect(css).toContain("Highlight");
  });

  it("has two themes only and no system or prefers-color-scheme logic", () => {
    expect(css).not.toContain("prefers-color-scheme");
    expect(css).not.toContain("data-maya-theme");
  });

  it("bans blur, gradient buttons and glow in the token file", () => {
    expect(css).not.toMatch(/backdrop-filter\s*:/);
    expect(css).not.toMatch(/[^-]blur\(\s*[1-9]/);
    expect(css).not.toMatch(/box-shadow:\s*0 0 \d/);
  });

  it("makes chrome text at least 13 px and mono labels at least 12 px", () => {
    const rem = (name: string): number => {
      const m = new RegExp(`--maya-${name}:\\s*([\\d.]+)rem`).exec(css);
      return m === null ? 0 : Number(m[1]) * 16;
    };
    expect(rem("text-label")).toBeGreaterThanOrEqual(12);
    expect(rem("text-caption")).toBeGreaterThanOrEqual(13);
    expect(rem("text-small")).toBeGreaterThanOrEqual(13);
    expect(rem("text-mono")).toBeGreaterThanOrEqual(12);
    expect(rem("text-body")).toBe(16);
  });
});

describe("design tokens: self-hosted fonts", () => {
  const files = [
    "ibm-plex-sans-latin-400-normal.woff2",
    "ibm-plex-sans-latin-500-normal.woff2",
    "ibm-plex-sans-latin-600-normal.woff2",
    "ibm-plex-mono-latin-400-normal.woff2",
    "ibm-plex-mono-latin-500-normal.woff2",
    "ibm-plex-sans-devanagari-devanagari-400-normal.woff2",
    "ibm-plex-sans-devanagari-devanagari-500-normal.woff2",
    "ibm-plex-sans-devanagari-devanagari-600-normal.woff2",
  ];

  it("ships every font file declared in @font-face, with licences", () => {
    for (const file of files) {
      const path = join(root, "public", "fonts", "ibm-plex", file);
      expect(existsSync(path), file).toBe(true);
      expect(statSync(path).size).toBeGreaterThan(5000);
      expect(css).toContain(`/fonts/ibm-plex/${file}`);
    }
    for (const licence of ["sans", "mono", "sans-devanagari"]) {
      expect(
        existsSync(
          join(
            root,
            "public",
            "fonts",
            "ibm-plex",
            `LICENSE-ibm-plex-${licence}.txt`,
          ),
        ),
      ).toBe(true);
    }
  });

  it("uses font-display swap, unicode-range and a system fallback, and loads nothing external", () => {
    expect(css.match(/font-display:\s*swap/g)?.length).toBe(files.length);
    expect(css.match(/unicode-range:/g)?.length).toBe(files.length);
    expect(css).toMatch(/--maya-font-sans:[^;]*system-ui[^;]*sans-serif/s);
    expect(css).toMatch(/--maya-font-mono:[^;]*monospace/s);
    expect(css).not.toMatch(/https?:\/\//);
  });
});
