import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import { describe, expect, it } from "vitest";

import {
  contrastRatio,
  parseHex,
  parseThemes,
} from "../../../scripts/design-tokens.mjs";

/**
 * Static checks on chat.css. jsdom does no layout or painting, so these tests read the
 * stylesheet as text to hold the UI/UX standards that can be checked without a browser: it uses
 * the real design tokens (it declares none of its own colours), contrast of the real token pairs
 * it relies on, compositor-only motion, a reduced-motion equivalent, a composer that reflows, and
 * no external requests. They do not replace looking at the page in a browser.
 */

const here = dirname(fileURLToPath(import.meta.url));
const css = readFileSync(resolve(here, "chat.css"), "utf8");
const globals = readFileSync(resolve(here, "../../app/globals.css"), "utf8");
const withoutComments = css.replace(/\/\*[\s\S]*?\*\//g, "");

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

const localTokens = block(":root {");

describe("tokens: chat.css uses the real design tokens and declares no theme", () => {
  it("has no copy of the colour tokens, no theme switch and no system colour scheme", () => {
    expect(css).not.toContain("data-maya-theme");
    expect(css).not.toContain("prefers-color-scheme");
    expect(css).not.toContain(".maya-app {");
    // The tokens live in globals.css, once.
    expect(globals).toMatch(/^:root\s*\{/m);
    expect(globals).not.toContain("html:root .maya-app");
  });

  it("declares only chat-local, non-colour custom properties", () => {
    const declared = Array.from(
      localTokens.text.matchAll(/(--maya-[a-z0-9-]+):/g),
    ).map((match) => match[1]);
    expect(declared.sort()).toEqual(
      [
        "--maya-composer-max",
        "--maya-dot",
        "--maya-dur-loop",
        "--maya-dur-ripple",
        "--maya-ease-emphasized",
        "--maya-ripple-size",
        "--maya-state-focus-opacity",
        "--maya-state-hover-opacity",
        "--maya-state-pressed-opacity",
        "--maya-state-ripple-opacity",
      ].sort(),
    );
  });

  it("uses no colour literals at all: every colour is a token", () => {
    const literals =
      withoutComments.match(/#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/g) ?? [];
    expect(literals).toEqual([]);
  });

  it("only reads tokens that exist in globals.css or in its own :root block", () => {
    const used = new Set(
      Array.from(withoutComments.matchAll(/var\((--maya-[a-z0-9-]+)/g)).map(
        (match) => match[1],
      ),
    );
    expect(used.size).toBeGreaterThan(40);
    const missing = [...used].filter(
      (name) =>
        !globals.includes(`${name}:`) && !localTokens.text.includes(`${name}:`),
    );
    expect(missing).toEqual([]);
  });

  it("has no blur, backdrop filter, gradient or glow", () => {
    expect(withoutComments).not.toMatch(/backdrop-filter|\bblur\(/);
    expect(withoutComments).not.toMatch(/gradient\(/);
    expect(withoutComments).not.toMatch(/box-shadow:\s*0 0 /);
  });

  it("no longer carries the dead rules of the retired layouts", () => {
    for (const dead of [
      ".maya-home",
      ".maya-chat--embedded",
      ".maya-workspace",
      ".maya-sidebar",
      ".maya-session",
      ".maya-conversation",
      ".maya-theme",
      ".maya-history-skeleton",
    ]) {
      expect(css, `${dead} is dead`).not.toContain(dead);
    }
  });

  it("does not reuse the class names of the shared primitives", () => {
    // maya-skeleton, maya-empty, maya-notice and maya-code are class names of components/ui;
    // an unlayered rule here would out-rank the primitive's own utilities.
    expect(withoutComments).not.toMatch(
      /\.maya-(skeleton|empty|notice|code)\s*[{,:[.]/,
    );
  });
});

describe("contrast of the real token pairs the conversation relies on (WCAG 2.2 AA)", () => {
  const themes = parseThemes(globals) as Record<
    "dark" | "light",
    Record<string, string>
  >;
  const pairs: Array<[string, string, string, number]> = [
    ["your message", "text", "raised", 4.5],
    ["composer field", "text", "pane", 4.5],
    ["MAYA text", "text", "bg", 4.5],
    ["quiet chip", "text-muted", "raised", 4.5],
    ["code on its block", "text", "code-bg", 4.5],
    ["muted on code", "text-muted", "code-bg", 4.5],
    ["muted on hover", "text-muted", "hover", 4.5],
    ["table header", "text", "raised", 4.5],
    ["link", "link", "bg", 4.5],
    ["error note", "danger", "bg", 4.5],
    ["error notice", "danger", "danger-soft", 4.5],
    ["warning count", "warning", "pane", 4.5],
    ["send button", "accent-text", "accent", 4.5],
    ["send button hover", "accent-text", "accent-hover", 4.5],
    ["keyword", "tok-keyword", "code-bg", 4.5],
    ["string", "tok-string", "code-bg", 4.5],
    ["number", "tok-number", "code-bg", 4.5],
    ["comment", "tok-comment", "code-bg", 4.5],
    ["name", "tok-name", "code-bg", 4.5],
    ["type", "tok-type", "code-bg", 4.5],
    ["composer border", "border-strong", "pane", 3],
    ["focus ring", "focus", "bg", 3],
    ["orb bars", "accent", "pane", 3],
  ];
  for (const theme of ["dark", "light"] as const) {
    it.each(pairs)(`${theme}: %s`, (_label, fg, bg, minimum) => {
      const a = parseHex(themes[theme][fg]);
      const b = parseHex(themes[theme][bg]);
      expect(a, `${fg} is a hex colour`).not.toBeNull();
      expect(b, `${bg} is a hex colour`).not.toBeNull();
      expect(
        contrastRatio(a as number[], b as number[]),
      ).toBeGreaterThanOrEqual(minimum);
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

  it("does not redefine keyframes that globals.css owns", () => {
    // maya-shimmer and maya-message are the motion kit's; a second definition here would win
    // or lose by import order.
    for (const name of ["maya-shimmer", "maya-message", "maya-rise"]) {
      expect(css).not.toMatch(new RegExp(`@keyframes\\s+${name}\\b`));
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
    const outsideTokens = withoutComments.replace(localTokens.text, "");
    const rawTime =
      outsideTokens.match(/(?<![\w-])\d*\.?\d+m?s\b(?![\w-])/g) ?? [];
    // Only the dots' animation-delay stagger and the reduced-motion override may be written out.
    expect(
      rawTime.filter((t) => !["160ms", "320ms", "0.01ms", "0s"].includes(t)),
    ).toEqual([]);
    expect(outsideTokens).not.toMatch(/cubic-bezier\(/);
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

  it("allows one infinite loop (the waiting dots) and no loop next to body text", () => {
    const caret = block(".maya-caret {").text;
    expect(caret).not.toContain("infinite");
    const loops = Array.from(
      css.matchAll(/animation:[^;]*infinite[^;]*;/g),
    ).map((match) => match[0]);
    expect(loops).toHaveLength(1);
    expect(loops[0]).toContain("maya-pulse");
    expect(css).toContain("a loop next to a text label");
  });

  it("uses the motion kit for message arrival and the voice orb", () => {
    for (const file of ["MessageItem.tsx", "ThreadList.tsx"]) {
      expect(readFileSync(resolve(here, file), "utf8")).toContain(
        "motion-message",
      );
    }
    const orb = readFileSync(resolve(here, "../voice/VoiceOrb.tsx"), "utf8");
    expect(orb).toContain("motion-orb");
    expect(css).toContain(".maya-orb");
  });

  it("guards the inline approval card with motion-none", () => {
    const source = readFileSync(
      resolve(here, "../approvals/InlineApproval.tsx"),
      "utf8",
    );
    // The card is a <MenuSurface as="section"> since the context menus (it renders the same <section>).
    expect(source).toMatch(/<(section|MenuSurface)[\s\S]{0,500}motion-none/);
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
    expect(globals).toContain("--maya-touch: 2.75rem");
    expect(css).toMatch(/@media \(pointer: coarse\), \(max-width: 40rem\)/);
  });

  it("gives the composer controls, links and inline note actions a 44 px touch height", () => {
    const touch = block("@media (pointer: coarse), (max-width: 40rem)").text;
    for (const name of [
      ".maya-btn",
      ".maya-jump",
      ".maya-composer__mic",
      ".maya-composer__action",
      ".maya-code__copy",
      ".maya-msg__speak",
      ".maya-brand",
      ".maya-msg__action",
    ]) {
      expect(touch, `${name} has a touch rule`).toContain(name);
    }
    expect(touch).toMatch(
      /\.maya-brand,[\s\S]*?\.maya-msg__action\s*\{[^}]*min-height:\s*var\(--maya-touch\)/,
    );
  });

  it("makes no external requests: no url(), @import or font host", () => {
    expect(withoutComments).not.toMatch(/url\(/);
    expect(withoutComments).not.toMatch(/@import/);
    expect(withoutComments).not.toMatch(/https?:\/\//);
  });

  it("styles the forced-colors mode so state does not depend on shadows", () => {
    expect(css).toContain("@media (forced-colors: active)");
  });
});

describe("layout: overflow, reflow and containment", () => {
  it("keeps one scroll region in the thread and lets it anchor to the reader", () => {
    const scroll = block(".maya-thread__scroll {").text;
    expect(scroll).toMatch(/overflow-y:\s*auto/);
    expect(scroll).toMatch(/overflow-x:\s*hidden/);
    expect(scroll).toMatch(/min-height:\s*0/);
    expect(scroll).toMatch(/min-width:\s*0/);
    expect(scroll).toMatch(/overflow-anchor:\s*auto/);
  });

  it("puts the jump button inside the thread pane and the dock outside any overlay layer", () => {
    expect(block(".maya-jump {").text).toMatch(/position:\s*absolute/);
    expect(css).toMatch(
      /\.maya-thread:has\(\[data-testid="inline-approval"\]:not\(\[data-pending="false"\]\)\)\s+\.maya-jump\s*\{\s*display:\s*none;?\s*\}/,
    );
    const dock = block(".maya-chat__dock {").text;
    expect(dock).not.toMatch(/position:\s*(fixed|absolute|sticky)/);
    expect(dock).toMatch(/flex:\s*none/);
  });

  it("wraps the composer row and reflows it by the composer's own width", () => {
    expect(block(".maya-composer__box {").text).toMatch(/flex-wrap:\s*wrap/);
    expect(block(".maya-composer {").text).toMatch(
      /container-type:\s*inline-size/,
    );
    const field = block(".maya-composer__field {").text;
    // The field keeps a real width basis and may shrink only by wrapping, never to one character.
    expect(field).toMatch(/flex:\s*1 1 12rem/);
    expect(field).toMatch(/min-width:\s*0/);
    const narrow = block("@container composer (max-width: 30rem)").text;
    expect(narrow).toMatch(/flex-basis:\s*100%/);
    expect(narrow).toMatch(/order:\s*-1/);
  });

  it("keeps the quiet chip as wide as its text, never a full-width box", () => {
    const chip = block(".maya-chip {").text;
    expect(chip).toMatch(/display:\s*inline-flex/);
    expect(chip).toMatch(/width:\s*fit-content/);
    expect(chip).not.toMatch(/border:/);
  });

  it("wraps long unbroken strings in messages, markdown and notices", () => {
    expect(block(".maya-msg__text {").text).toMatch(
      /overflow-wrap:\s*anywhere/,
    );
    expect(block(".maya-md {").text).toMatch(/overflow-wrap:\s*anywhere/);
    expect(block(".maya-cnotice__text {").text).toMatch(
      /overflow-wrap:\s*anywhere/,
    );
    expect(block('.maya-code__pre[data-wrap="true"] {').text).toMatch(
      /white-space:\s*pre-wrap/,
    );
  });

  it("scrolls wide tables inside their own box with natural column widths", () => {
    const table = block(".maya-md__table table {").text;
    expect(table).toMatch(/width:\s*max-content/);
    expect(table).toMatch(/min-width:\s*100%/);
  });

  it("draws the voice orb as nine bars driven by a per-bar resting height", () => {
    expect(block(".maya-orb > i {").text).toMatch(/scaleY\(var\(--h/);
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
    ".maya-btn--primary:hover",
    ".maya-msg__action:hover",
    ".maya-msg__speak:hover",
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

describe("type and tokens for recurring sizes", () => {
  it("takes letter spacing from tokens, never a raw em value", () => {
    expect(withoutComments).not.toMatch(/letter-spacing:\s*-?[\d.]+em/);
  });

  it("names no typeface beyond the two font tokens", () => {
    const families = Array.from(css.matchAll(/font-family:\s*([^;]+);/g)).map(
      (match) => match[1].trim(),
    );
    expect(families.length).toBeGreaterThan(0);
    for (const family of families) {
      expect(family).toMatch(/^var\(--maya-font-(sans|mono)\)$/);
    }
  });

  it("keeps chrome text at 13 px or more: no caption or label size below the token floor", () => {
    expect(globals).toContain("--maya-text-caption: 0.8125rem");
    expect(globals).toContain("--maya-text-label: 0.75rem");
    expect(withoutComments).not.toMatch(/font-size:\s*0?\.[0-7]\d*rem/);
  });
});
