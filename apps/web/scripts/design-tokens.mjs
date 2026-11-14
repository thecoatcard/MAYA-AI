/**
 * Reads the colour tokens out of src/app/globals.css and proves the WCAG 2.2 contrast of every
 * text and UI pair, for both themes. Shared by the vitest guard (src/app/design-tokens.test.tsx)
 * and the report script (scripts/contrast-report.mjs). Pure functions, no dependencies.
 */

const DARK_BLOCK = /^:root\s*\{/m;
const LIGHT_BLOCK = /^:root\[data-theme="light"\]\s*\{/m;

function blockBody(css, start) {
  const match = start.exec(css);
  if (match === null) throw new Error(`token block not found: ${start}`);
  const open = css.indexOf("{", match.index);
  let depth = 0;
  for (let i = open; i < css.length; i += 1) {
    if (css[i] === "{") depth += 1;
    if (css[i] === "}") depth -= 1;
    if (depth === 0) return css.slice(open + 1, i);
  }
  throw new Error("unbalanced css");
}

function declarations(body) {
  const out = {};
  const stripped = body.replace(/\/\*[\s\S]*?\*\//g, "");
  for (const match of stripped.matchAll(
    /--maya-([a-z0-9-]+)\s*:\s*([^;]+);/g,
  )) {
    out[match[1]] = match[2].replace(/\s+/g, " ").trim();
  }
  return out;
}

/** Resolve var(--x) references (colour tokens only) until none remain. */
function resolve(tokens) {
  const out = {};
  for (const [name, raw] of Object.entries(tokens)) {
    let value = raw;
    for (let guard = 0; guard < 8 && value.includes("var("); guard += 1) {
      value = value.replace(
        /var\(--([a-z0-9-]+)\)/g,
        (_, ref) => tokens[ref] ?? "",
      );
    }
    out[name] = value;
  }
  return out;
}

/** { dark: {token: value}, light: {token: value} } with the light theme layered over dark. */
export function parseThemes(css) {
  const dark = declarations(blockBody(css, DARK_BLOCK));
  const light = { ...dark, ...declarations(blockBody(css, LIGHT_BLOCK)) };
  return { dark: resolve(dark), light: resolve(light) };
}

export function parseHex(value) {
  const match = /^#([0-9a-f]{6})$/i.exec(value ?? "");
  if (match === null) return null;
  const n = Number.parseInt(match[1], 16);
  return [(n >> 16) & 255, (n >> 8) & 255, n & 255];
}

/** "rgb(r g b / a)" (or "rgb(r g b)") to { rgb: [r, g, b], alpha }, else null. */
export function parseRgba(value) {
  const match =
    /^rgba?\(\s*(\d+)[\s,]+(\d+)[\s,]+(\d+)(?:\s*[/,]\s*([\d.]+))?\s*\)$/i.exec(
      value ?? "",
    );
  if (match === null) return null;
  return {
    rgb: [Number(match[1]), Number(match[2]), Number(match[3])],
    alpha: match[4] === undefined ? 1 : Number(match[4]),
  };
}

function toHex(rgb) {
  return (
    "#" +
    rgb
      .map((c) =>
        Math.round(Math.min(255, Math.max(0, c)))
          .toString(16)
          .padStart(2, "0"),
      )
      .join("")
  );
}

/**
 * Resolve a token name to a hex colour. "vib-side@bg" means the translucent vibrancy token
 * blended over the opaque surface token behind it, which is what the eye (and a contrast
 * checker) actually sees.
 */
export function colourOf(tokens, name) {
  const at = name.indexOf("@");
  if (at < 0) return tokens[name];
  const top = parseRgba(tokens[name.slice(0, at)]);
  const base = parseHex(tokens[name.slice(at + 1)]);
  if (top === null || base === null) return undefined;
  return toHex(
    top.rgb.map((c, i) => c * top.alpha + base[i] * (1 - top.alpha)),
  );
}

function channel(c) {
  const s = c / 255;
  return s <= 0.03928 ? s / 12.92 : ((s + 0.055) / 1.055) ** 2.4;
}

export function luminance([r, g, b]) {
  return 0.2126 * channel(r) + 0.7152 * channel(g) + 0.0722 * channel(b);
}

export function contrastRatio(a, b) {
  const la = luminance(a);
  const lb = luminance(b);
  return (Math.max(la, lb) + 0.05) / (Math.min(la, lb) + 0.05);
}

const SURFACES = ["bg", "pane", "raised", "overlay"];
/** Vibrancy tokens and the opaque surfaces they can sit over (content scrolls under bars). */
const VIBRANT = {
  "vib-bar": ["bg", "pane", "raised"],
  "vib-side": ["bg", "pane"],
  "vib-pop": ["bg", "pane", "raised"],
  "vib-sheet": ["bg", "pane", "raised"],
};
const STATUS = ["success", "warning", "danger", "info"];

/**
 * Every pair the UI relies on: [group, label, foreground token, background token, minimum].
 * 4.5 for text, 3 for UI boundaries and indicators. Disabled text is exempt from WCAG but is held
 * to 3 here so a disabled control stays legible.
 */
export function pairs() {
  const list = [];
  for (const bg of SURFACES) {
    for (const fg of ["text", "text-secondary", "text-muted", "link"]) {
      list.push(["Text", `${fg} on ${bg}`, fg, bg, 4.5]);
    }
    list.push([
      "UI",
      `border-strong on ${bg} (control edge)`,
      "border-strong",
      bg,
      3,
    ]);
    list.push(["UI", `focus ring on ${bg}`, "focus", bg, 3]);
    list.push(["UI", `accent fill on ${bg}`, "accent", bg, 3]);
  }
  list.push(["Text", "text-muted on hover", "text-muted", "hover", 4.5]);
  list.push(["Text", "text on hover", "text", "hover", 4.5]);
  list.push([
    "Text",
    "text-secondary on hover",
    "text-secondary",
    "hover",
    4.5,
  ]);
  for (const fg of ["text", "text-secondary", "text-muted"]) {
    list.push(["Text", `${fg} on pressed`, fg, "pressed", 4.5]);
    list.push([
      "Text",
      `${fg} on accent-soft (selected row)`,
      fg,
      "accent-soft",
      4.5,
    ]);
  }
  for (const [vib, bases] of Object.entries(VIBRANT)) {
    for (const base of bases) {
      for (const fg of ["text", "text-secondary", "text-muted", "link"]) {
        list.push([
          "Vibrancy",
          `${fg} on ${vib} over ${base}`,
          fg,
          `${vib}@${base}`,
          4.5,
        ]);
      }
    }
  }
  for (const bg of ["accent", "accent-hover", "accent-active"]) {
    list.push([
      "Text",
      `accent-text on ${bg} (primary button)`,
      "accent-text",
      bg,
      4.5,
    ]);
  }
  list.push([
    "Text",
    "link on accent-soft (accent badge)",
    "link",
    "accent-soft",
    4.5,
  ]);
  list.push(["UI", "accent border on accent-soft", "accent", "accent-soft", 3]);
  for (const status of STATUS) {
    list.push(["Text", `${status} on pane`, status, "pane", 4.5]);
    list.push([
      "Text",
      `${status} on ${status}-soft (badge)`,
      status,
      `${status}-soft`,
      4.5,
    ]);
    list.push([
      "Text",
      `text on ${status}-soft (notice body)`,
      "text",
      `${status}-soft`,
      4.5,
    ]);
    list.push([
      "Text",
      `text-secondary on ${status}-soft`,
      "text-secondary",
      `${status}-soft`,
      4.5,
    ]);
    list.push(["UI", `${status} border on pane`, status, "pane", 3]);
  }
  list.push([
    "Text",
    "selection-text on selection",
    "selection-text",
    "selection",
    4.5,
  ]);
  list.push([
    "Text",
    "disabled-text on disabled-bg",
    "disabled-text",
    "disabled-bg",
    3,
  ]);
  list.push(["Text", "text on code-bg", "text", "code-bg", 4.5]);
  for (const tok of [
    "keyword",
    "string",
    "number",
    "comment",
    "name",
    "type",
  ]) {
    list.push([
      "Text",
      `syntax ${tok} on code-bg`,
      `tok-${tok}`,
      "code-bg",
      4.5,
    ]);
  }
  list.push(["UI", "scrollbar thumb on pane", "scroll-thumb", "pane", 3]);
  list.push([
    "UI",
    "scrollbar thumb hover on pane",
    "scroll-thumb-hover",
    "pane",
    3,
  ]);
  return list;
}

/** Evaluate every pair in both themes: [{theme, group, label, fg, bg, ratio, min, pass}]. */
export function evaluate(css) {
  const themes = parseThemes(css);
  const rows = [];
  for (const theme of ["dark", "light"]) {
    for (const [group, label, fgToken, bgToken, min] of pairs()) {
      const fg = colourOf(themes[theme], fgToken);
      const bg = colourOf(themes[theme], bgToken);
      const fgRgb = parseHex(fg);
      const bgRgb = parseHex(bg);
      if (fgRgb === null || bgRgb === null) {
        rows.push({ theme, group, label, fg, bg, ratio: 0, min, pass: false });
        continue;
      }
      const ratio = contrastRatio(fgRgb, bgRgb);
      rows.push({
        theme,
        group,
        label,
        fg,
        bg,
        ratio,
        min,
        pass: ratio >= min,
      });
    }
  }
  return rows;
}
