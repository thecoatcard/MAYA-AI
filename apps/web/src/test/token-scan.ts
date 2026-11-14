/**
 * Test support for the design-token ratchet (src/test/token-scan.test.tsx). Nothing in the
 * application imports this file.
 *
 * Components must use token classes (bg-maya-pane, text-maya-muted, border-maya-border, ...) so
 * both themes work from one code path. These patterns find the raw values that bypass the tokens.
 */

const PALETTE =
  "zinc|slate|gray|neutral|stone|red|orange|amber|yellow|lime|green|emerald|teal|cyan|sky|blue|indigo|violet|purple|fuchsia|pink|rose";

const UTILITY =
  "bg|text|border|border-[xytblr]|ring|ring-offset|outline|fill|stroke|from|via|to|divide|shadow|decoration|accent|caret|placeholder";

export const RAW_COLOUR_PATTERNS: ReadonlyArray<[string, RegExp]> = [
  // bg-white, text-black/60, border-white, ...
  [
    "white/black utility",
    new RegExp(`(?<![\\w-])(?:${UTILITY})-(?:white|black)(?![\\w-])`, "g"),
  ],
  // bg-zinc-900, text-slate-400, border-red-500/20, ...
  [
    "palette utility",
    new RegExp(
      `(?<![\\w-])(?:${UTILITY})-(?:${PALETTE})-\\d{2,3}(?![\\w-])`,
      "g",
    ),
  ],
  // arbitrary values: bg-[#123], text-[rgb(...)]
  [
    "arbitrary colour",
    /(?<![\w-])(?:bg|text|border|ring|fill|stroke|shadow|from|via|to)-\[(?:#|rgb|hsl|oklch)[^\]]*\]/g,
  ],
  // #fff, #1a2b3c, #1a2b3cff in code or strings
  [
    "hex colour",
    /#(?:[0-9a-fA-F]{8}|[0-9a-fA-F]{6}|[0-9a-fA-F]{3})(?![0-9a-zA-Z_-])/g,
  ],
  // rgba(0,0,0,.4), hsl(210 ...) literals
  ["colour function", /\b(?:rgba?|hsla?|oklch|oklab)\(\s*[\d.]/g],
  // blur is banned (direction rule), also as a utility
  ["backdrop blur", /backdrop-(?:blur|filter)/g],
  // gradient buttons and glows are banned
  ["gradient utility", /(?<![\w-])bg-(?:gradient-to-|linear-|radial-|conic-)/g],
  ["glow shadow", /(?<![\w-])(?:shadow|drop-shadow)-\[0_0_/g],
];

export interface Violation {
  readonly kind: string;
  readonly match: string;
}

/** Violations in already comment-stripped source. */
export function findViolations(code: string): Violation[] {
  const found: Violation[] = [];
  for (const [kind, pattern] of RAW_COLOUR_PATTERNS) {
    for (const match of code.matchAll(pattern)) {
      found.push({ kind, match: match[0] });
    }
  }
  return found;
}
