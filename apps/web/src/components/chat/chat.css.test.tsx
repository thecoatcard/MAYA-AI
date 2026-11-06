import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

/**
 * Static checks on chat.css. jsdom does no layout or painting, so these tests
 * read the stylesheet as text to hold the UI/UX standards that can be checked
 * without a browser: tokens only, both themes, contrast of the real token pairs,
 * compositor-only motion, a reduced-motion equivalent, and no external requests.
 * They do not replace looking at the page in a browser.
 */

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(resolve(here, "chat.css"), "utf8");

/** Text between the braces of the block whose header starts at `at`. */
function blockAt(at: number): { text: string; start: number; end: number } {
  const open = css.indexOf("{", at);
  let depth = 0;
  let close = open;
  for (; close < css.length; close += 1) {
    if (css[close] === "{") {
      depth += 1;
    } else if (css[close] === "}") {
      depth -= 1;
      if (depth === 0) {
        break;
      }
    }
  }
  return { text: css.slice(open + 1, close), start: at, end: close + 1 };
}

/** Text between the braces of the first block that starts at `marker`. */
function block(marker: string): { text: string; start: number; end: number } {
  const at = css.indexOf(marker);
  if (at < 0) {
    throw new Error(`missing block: ${marker}`);
  }
  return blockAt(at);
}

/** Every block that starts with `header` (for example each hover media query). */
function blocks(header: string): string[] {
  const found: string[] = [];
  let from = css.indexOf(header);
  while (from >= 0) {
    found.push(blockAt(from).text);
    from = css.indexOf(header, from + header.length);
  }
  return found;
}

function tokens(text: string): Record<string, string> {
  const out: Record<string, string> = {};
  for (const match of text.matchAll(/--maya-([a-z0-9-]+):\s*([^;]+);/g)) {
    out[match[1]] = match[2].trim();
  }
  return out;
}

const lightBlock = block(".maya-app {");
const darkMediaBlock = block("@media (prefers-color-scheme: dark)");
const darkAttrBlock = block('.maya-app[data-maya-theme="dark"]');
const light = tokens(lightBlock.text);
const darkMedia = tokens(darkMediaBlock.text);
const darkAttr = tokens(darkAttrBlock.text);
const dark = { ...light, ...darkAttr };

type Rgba = [number, number, number, number];

function parseColour(value: string): Rgba {
  const hex = value.match(/^#([0-9a-f]{6})$/i);
  if (hex) {
    const n = Number.parseInt(hex[1], 16);
    return [(n >> 16) & 255, (n >> 8) & 255, n & 255, 1];
  }
  const rgb = value.match(
    /^rgb\(\s*(\d+)\s+(\d+)\s+(\d+)\s*(?:\/\s*([\d.]+))?\)$/,
  );
  if (rgb) {
    return [
      Number(rgb[1]),
      Number(rgb[2]),
      Number(rgb[3]),
      rgb[4] === undefined ? 1 : Number(rgb[4]),
    ];
  }
  throw new Error(`cannot parse colour: ${value}`);
}

function over(foreground: Rgba, background: Rgba): Rgba {
  const [r, g, b, a] = foreground;
  return [
    r * a + background[0] * (1 - a),
    g * a + background[1] * (1 - a),
    b * a + background[2] * (1 - a),
    1,
  ];
}

function luminance([r, g, b]: Rgba): number {
  const channel = (c: number) => {
    const s = c / 255;
    return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
  };
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

function contrast(a: Rgba, b: Rgba): number {
  const [x, y] = [luminance(a), luminance(b)];
  return (Math.max(x, y) + 0.05) / (Math.min(x, y) + 0.05);
}

describe("tokens", () => {
  const colourKeys = Object.keys(light).filter(
    (key) => /^#|^rgb\(/.test(light[key]) && !key.startsWith("shadow"),
  );

  it("defines the same colour roles in the dark theme as in the light theme", () => {
    expect(colourKeys.length).toBeGreaterThan(20);
    expect(
      Object.keys(darkAttr)
        .filter((key) => colourKeys.includes(key))
        .sort(),
    ).toEqual([...colourKeys].sort());
    for (const key of colourKeys) {
      expect(
        darkAttr[key],
        `dark theme is missing --maya-${key}`,
      ).toBeDefined();
    }
  });

  it("defines the dark theme identically for the system setting and the manual switch", () => {
    expect(darkMedia).toEqual(darkAttr);
  });

  it("only changes the light theme when the user has not chosen light", () => {
    expect(darkMediaBlock.text).toContain(
      '.maya-app:not([data-maya-theme="light"])',
    );
  });

  it("uses colour literals only inside the token definitions", () => {
    const outside =
      css.slice(0, lightBlock.start) +
      css.slice(lightBlock.end, darkMediaBlock.start) +
      css.slice(darkMediaBlock.end, darkAttrBlock.start) +
      css.slice(darkAttrBlock.end);
    const withoutComments = outside.replace(/\/\*[\s\S]*?\*\//g, "");
    const literals =
      withoutComments.match(/#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/g) ?? [];
    expect(literals).toEqual([]);
  });

  it("does not use pure black or pure white as a background token", () => {
    for (const theme of [light, dark]) {
      for (const key of ["bg", "surface", "raised", "code-bg"]) {
        expect(theme[key].toLowerCase()).not.toBe("#000000");
        expect(theme[key].toLowerCase()).not.toBe("#ffffff");
      }
    }
  });
});

describe("contrast of the real token pairs (WCAG 2.2 AA)", () => {
  for (const [name, theme] of [
    ["light", light],
    ["dark", dark],
  ] as const) {
    const c = (key: string) => parseColour(theme[key]);
    const dangerSoftOnSurface = over(c("danger-soft"), c("surface"));
    const pairs: Array<[string, Rgba, Rgba, number]> = [
      ["text on bg", c("text"), c("bg"), 4.5],
      ["text on surface", c("text"), c("surface"), 4.5],
      ["text on raised", c("text"), c("raised"), 4.5],
      [
        "text on glass over darkest light-theme surface",
        c("text"),
        over(c("glass-surface"), c("bg")),
        4.5,
      ],
      [
        "text on glass over lightest light-theme surface",
        c("text"),
        over(c("glass-surface"), c("raised")),
        4.5,
      ],
      [
        "text on raised glass over darkest light-theme surface",
        c("text"),
        over(c("glass-raised"), c("bg")),
        4.5,
      ],
      [
        "text on raised glass over lightest light-theme surface",
        c("text"),
        over(c("glass-raised"), c("raised")),
        4.5,
      ],
      ["text on code", c("text"), c("code-bg"), 4.5],
      ["text on hover", c("text"), c("hover"), 4.5],
      ["text on error notice", c("text"), dangerSoftOnSurface, 4.5],
      ["muted on bg", c("text-muted"), c("bg"), 4.5],
      ["muted on surface", c("text-muted"), c("surface"), 4.5],
      ["muted on raised", c("text-muted"), c("raised"), 4.5],
      ["muted on code", c("text-muted"), c("code-bg"), 4.5],
      ["muted on hover", c("text-muted"), c("hover"), 4.5],
      ["accent text on accent", c("accent-text"), c("accent"), 4.5],
      ["accent text on accent hover", c("accent-text"), c("accent-hover"), 4.5],
      ["accent hover mark on bg", c("accent-hover"), c("bg"), 3],
      ["warning text on raised", c("warning"), c("raised"), 4.5],
      ["muted text on error notice", c("text-muted"), dangerSoftOnSurface, 4.5],
      ["link on bg", c("link"), c("bg"), 4.5],
      ["link on surface", c("link"), c("surface"), 4.5],
      ["danger text on bg", c("danger"), c("bg"), 4.5],
      ["danger text on surface", c("danger"), c("surface"), 4.5],
      ["danger text on raised", c("danger"), c("raised"), 4.5],
      ["warning text on bg", c("warning"), c("bg"), 4.5],
      ["warning text on surface", c("warning"), c("surface"), 4.5],
      ["focus indicator on bg", c("focus"), c("bg"), 3],
      ["focus indicator on surface", c("focus"), c("surface"), 3],
      ["input border on surface", c("control-border"), c("surface"), 3],
      ["input border on bg", c("control-border"), c("bg"), 3],
      ["accent mark on bg", c("accent"), c("bg"), 3],
      ["accent mark on surface", c("accent"), c("surface"), 3],
      ["error border on notice", c("danger"), dangerSoftOnSurface, 3],
      ["keyword on code", c("tok-keyword"), c("code-bg"), 4.5],
      ["string on code", c("tok-string"), c("code-bg"), 4.5],
      ["number on code", c("tok-number"), c("code-bg"), 4.5],
      ["comment on code", c("tok-comment"), c("code-bg"), 4.5],
      ["name on code", c("tok-name"), c("code-bg"), 4.5],
      ["type on code", c("tok-type"), c("code-bg"), 4.5],
    ];
    it.each(pairs)(`${name}: %s`, (_label, foreground, background, minimum) => {
      expect(contrast(foreground, background)).toBeGreaterThanOrEqual(minimum);
    });
  }
});

describe("motion", () => {
  it("animates only transform and opacity in keyframes", () => {
    const keyframes = Array.from(css.matchAll(/@keyframes\s+[\w-]+\s*\{/g));
    expect(keyframes.length).toBeGreaterThan(0);
    for (const match of keyframes) {
      const body = block(match[0]).text;
      for (const property of body.matchAll(/([a-z-]+)\s*:/g)) {
        expect(
          ["opacity", "transform"],
          `keyframe animates ${property[1]}`,
        ).toContain(property[1]);
      }
    }
  });

  it("transitions only paint and compositor properties, never layout", () => {
    const layout = [
      "width",
      "height",
      "margin",
      "padding",
      "top",
      "left",
      "right",
      "bottom",
      "inset",
      "flex",
      "grid",
      "gap",
      "font-size",
      "line-height",
    ];
    for (const match of css.matchAll(/transition:\s*([^;]+);/g)) {
      const properties = match[1]
        .split(",")
        .map((part) => part.trim().split(/\s+/)[0]);
      for (const property of properties) {
        expect(layout, `transition on ${property}`).not.toContain(property);
        expect(property).not.toBe("all");
      }
    }
  });

  it("takes every duration and easing from tokens", () => {
    const withoutTokenBlocks = css
      .replace(lightBlock.text, "")
      .replace(/\/\*[\s\S]*?\*\//g, "");
    const rawTime =
      withoutTokenBlocks.match(/(?<![\w-])\d*\.?\d+m?s\b(?![\w-])/g) ?? [];
    // Only the animation-delay stagger (a few tens of ms) and the reduced-motion
    // override (0.01ms, 0s) may be written out.
    expect(
      rawTime.filter((t) => !["160ms", "320ms", "0.01ms", "0s"].includes(t)),
    ).toEqual([]);
    expect(withoutTokenBlocks).not.toMatch(/cubic-bezier\(/);
  });

  it("has a reduced-motion equivalent that stops every animation and timed transition", () => {
    const reduced = block("@media (prefers-reduced-motion: reduce)").text;
    expect(reduced).toContain(".maya-app *");
    expect(reduced).toMatch(/animation:\s*none\s*!important/);
    expect(reduced).toMatch(/transition-duration:\s*0\.01ms\s*!important/);
  });

  it("keeps the waiting dots paired with a text label in the markup", () => {
    // The animation is decoration; the label carries the meaning (see MessageItem).
    const source = readFileSync(resolve(here, "MessageItem.tsx"), "utf8");
    expect(source).toContain("maya-pending__label");
  });

  it("allows only named infinite loops with written purposes and static fallbacks", () => {
    expect(css).toContain(
      "Shimmer keeps history loading legible without a spinner.",
    );
    expect(css).toContain(
      "Ambient motion is only a subtle landing-page orientation cue.",
    );
    expect(css).toContain(
      '.maya-app[data-page-hidden="true"] .maya-home::before',
    );
    expect(css).toContain("animation-play-state: paused");
    const reduced = block("@media (prefers-reduced-motion: reduce)").text;
    expect(reduced).toMatch(/animation:\s*none\s*!important/);
  });

  it("does not loop an animation next to body text", () => {
    const caret = block(".maya-caret {").text;
    expect(caret).not.toContain("infinite");
    const loops = Array.from(
      css.matchAll(/animation:[^;]*infinite[^;]*;/g),
    ).map((match) => match[0]);
    expect(loops).toHaveLength(3);
    expect(loops.join(" ")).toContain("maya-pulse");
    expect(loops.join(" ")).toContain("maya-shimmer");
    expect(loops.join(" ")).toContain("maya-ambient");
    expect(css).toContain(
      "Shimmer keeps history loading legible without a spinner.",
    );
    expect(css).toContain(
      "Ambient motion is only a subtle landing-page orientation cue.",
    );
  });
});

describe("accessibility and safety of the stylesheet", () => {
  it("draws a visible focus indicator for links, buttons and focusable regions", () => {
    expect(css).toMatch(
      /:is\(a, button, \[tabindex\]\):focus-visible\s*\{[^}]*outline:\s*2px solid var\(--maya-focus\)/,
    );
    expect(css).toContain(".maya-composer__box:focus-within");
  });

  it("outlines the focused composer with the same 2 px indicator, not just a border colour", () => {
    // The idle border is #7b8aa0-ish; a 1 px change to the focus colour measured
    // about 1.8:1, below the 3:1 a focus indicator needs.
    const outsideForcedColors = css.slice(
      0,
      css.indexOf("@media (forced-colors: active)"),
    );
    const at = outsideForcedColors.indexOf(
      ".maya-composer__box:focus-within {",
    );
    expect(at).toBeGreaterThan(0);
    const focused = blockAt(at).text;
    expect(focused).toMatch(/outline:\s*2px solid var\(--maya-focus\)/);
    expect(focused).toMatch(/outline-offset:\s*2px/);
    expect(focused).toMatch(/border-color:\s*var\(--maya-focus\)/);
  });

  it("keeps a high-contrast fallback for the focused composer", () => {
    const forced = block("@media (forced-colors: active)").text;
    expect(forced).toMatch(
      /\.maya-composer__box:focus-within\s*\{[^}]*outline:\s*2px solid Highlight/,
    );
  });

  it("keeps touch targets at least 44 px on touch layouts", () => {
    expect(light.touch).toBe("2.75rem");
    expect(css).toMatch(/@media \(pointer: coarse\), \(max-width: 40rem\)/);
  });

  it("gives stand-alone links and inline note actions a 44 px touch height", () => {
    // Measured at 360 px before this rule: the brand link 39 px, the signed-out
    // link 23 px.
    const touch = block("@media (pointer: coarse), (max-width: 40rem)").text;
    for (const name of [
      ".maya-btn",
      ".maya-theme__option",
      ".maya-jump",
      ".maya-code__copy",
      ".maya-brand",
      ".maya-signedout__link",
      ".maya-msg__action",
    ]) {
      expect(touch, `${name} has a touch rule`).toContain(name);
    }
    expect(touch).toMatch(
      /\.maya-brand,[\s\S]*?\.maya-msg__action\s*\{[^}]*min-height:\s*var\(--maya-touch\)/,
    );
  });

  it("lets the stand-alone link center its text so a taller box looks right", () => {
    const link = block(".maya-signedout__link {").text;
    expect(link).toMatch(/display:\s*inline-flex/);
    expect(link).toMatch(/align-items:\s*center/);
  });

  it("makes no external requests: no url(), @import or font host", () => {
    const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");
    expect(withoutComments).not.toMatch(/url\(/);
    expect(withoutComments).not.toMatch(/@import/);
    expect(withoutComments).not.toMatch(/https?:\/\//);
  });

  it("styles the forced-colors mode so state does not depend on shadows", () => {
    expect(css).toContain("@media (forced-colors: active)");
  });

  it("provides solid fallbacks for every frosted surface", () => {
    expect(css).toContain("@supports not (backdrop-filter: blur(1px))");
    expect(css).toContain("@media (prefers-reduced-transparency: reduce)");
    expect(css).toContain(
      ".maya-chat__header {\n    background: var(--maya-raised)",
    );
    expect(css).toContain(
      ".maya-chat__dock {\n    background: var(--maya-raised)",
    );
    expect(css).toContain(
      ".maya-sidebar.is-open {\n    background: var(--maya-raised)",
    );
  });
});

describe("hover states", () => {
  const hoverBlocks = blocks("@media (hover: hover)");
  const hoverCss = hoverBlocks.join("\n");
  const outsideHover = hoverBlocks.reduce(
    (rest, text) => rest.replace(text, ""),
    css,
  );

  it.each([
    ".maya-composer__action:hover",
    ".maya-brand:hover",
    ".maya-signedout__link:hover",
    ".maya-btn--primary:hover",
    ".maya-msg__action:hover",
  ])("defines %s, only where hover exists", (selector) => {
    expect(hoverCss).toContain(selector);
    // Not on touch devices, where :hover sticks after a tap.
    expect(outsideHover).not.toContain(selector);
  });

  it("does not change a disabled button on hover", () => {
    expect(hoverCss).toMatch(/\.maya-composer__action:hover:not\(:disabled\)/);
    expect(hoverCss).toMatch(/\.maya-btn--primary:hover:not\(:disabled\)/);
  });

  it("gives the stop state its own hover so it does not turn accent-coloured", () => {
    expect(hoverCss).toMatch(
      /\.maya-composer__action\[data-mode="stop"\]:hover:not\(:disabled\)\s*\{[^}]*background:\s*var\(--maya-hover\)/,
    );
  });

  it("keeps the primary button readable when disabled", () => {
    const disabled = block(".maya-btn:disabled {").text;
    expect(disabled).toContain("background: var(--maya-disabled-bg)");
    expect(disabled).toContain("color: var(--maya-disabled-text)");
  });
});

describe("tokens for recurring sizes", () => {
  it("takes the recurring letter spacings from tokens", () => {
    expect(light["tracking-wide"]).toBe("0.14em");
    expect(light["tracking-caps"]).toBe("0.1em");
    expect(light["tracking-tight"]).toBe("-0.02em");
    const withoutTokenBlocks = css
      .replace(lightBlock.text, "")
      .replace(/\/\*[\s\S]*?\*\//g, "");
    for (const raw of ["0.14em", "0.1em", "-0.02em"]) {
      expect(withoutTokenBlocks, `raw letter-spacing ${raw}`).not.toMatch(
        new RegExp(`letter-spacing:\\s*${raw.replace(".", "\\.")}`),
      );
    }
  });

  it("does not leave an unused focus-ring token behind", () => {
    expect(css).not.toContain("--maya-focus-ring");
  });

  it("names no typeface beyond the system stack behind one token", () => {
    const families = Array.from(css.matchAll(/font-family:\s*([^;]+);/g)).map(
      (match) => match[1].trim(),
    );
    for (const family of families) {
      expect(family).toMatch(/^var\(--maya-font-(sans|mono)\)$/);
    }
    expect(light["font-sans"]).toContain("system-ui");
  });
});
