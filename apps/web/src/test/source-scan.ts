/**
 * Helpers for the static "no HTML injection" scan. Test support only: nothing in
 * the application imports this file, and the scan skips the src/test folder.
 */

import { readdirSync, readFileSync } from "node:fs";
import { join, relative, sep } from "node:path";

/**
 * Remove // and block comments but keep everything else, string contents
 * included. It tracks string and template literals, so "//" inside a URL string
 * is not taken for a comment and the code after the string on the same line is
 * kept. A backslash outside a string (a regular expression escape) protects the
 * next character, so /https?:\/\// does not start a comment. A string that is not
 * closed on its line ends there, which limits the damage of a quote inside a
 * regular expression: the scan keeps more text than it strictly must, never less.
 */
export function stripComments(source: string): string {
  const n = source.length;
  let out = "";
  let i = 0;
  while (i < n) {
    const ch = source[i];
    const next = source[i + 1];
    if (ch === "/" && next === "*") {
      const end = source.indexOf("*/", i + 2);
      i = end === -1 ? n : end + 2;
      out += " ";
      continue;
    }
    if (ch === "/" && next === "/") {
      while (i < n && source[i] !== "\n") {
        i += 1;
      }
      continue;
    }
    if (ch === "\\") {
      out += ch + (next ?? "");
      i += 2;
      continue;
    }
    if (ch === '"' || ch === "'" || ch === "`") {
      out += ch;
      i += 1;
      while (i < n) {
        const c = source[i];
        out += c;
        i += 1;
        if (c === "\\") {
          if (i < n) {
            out += source[i];
            i += 1;
          }
          continue;
        }
        if (c === ch || (c === "\n" && ch !== "`")) {
          break;
        }
      }
      continue;
    }
    out += ch;
    i += 1;
  }
  return out;
}

/** Calls that can turn a string into markup or code. */
export const FORBIDDEN_PATTERNS: ReadonlyArray<[string, RegExp]> = [
  ["dangerouslySetInnerHTML", /dangerouslySetInnerHTML/],
  ["innerHTML", /\binnerHTML\b/],
  ["outerHTML", /\bouterHTML\b/],
  ["insertAdjacentHTML", /insertAdjacentHTML/],
  ["document.write", /document\s*\.\s*write(ln)?\s*\(/],
  ["eval(", /\beval\s*\(/],
  ["new Function(", /\bnew\s+Function\s*\(/],
  ["setTimeout with a string", /\bset(Timeout|Interval)\s*\(\s*["'`]/],
];

function isTestSupport(relativePath: string): boolean {
  const parts = relativePath.split(sep);
  const name = parts[parts.length - 1];
  return (
    /\.test\.tsx?$/.test(name) ||
    name === "chat-test-utils.ts" ||
    name.endsWith(".d.ts") ||
    parts.includes("test")
  );
}

/**
 * Every shipped TypeScript source file under `root` (paths relative to `root`):
 * everything except test files, test helpers and declaration files.
 */
export function listSourceFiles(root: string): string[] {
  const found: string[] = [];
  const walk = (dir: string) => {
    for (const entry of readdirSync(dir, { withFileTypes: true })) {
      const full = join(dir, entry.name);
      if (entry.isDirectory()) {
        walk(full);
      } else if (
        /\.tsx?$/.test(entry.name) &&
        !isTestSupport(relative(root, full))
      ) {
        found.push(relative(root, full).split(sep).join("/"));
      }
    }
  };
  walk(root);
  return found.sort();
}

export function readCode(root: string, file: string): string {
  return stripComments(readFileSync(join(root, file), "utf8"));
}

/** The file exactly as written, comments included. */
export function readRaw(root: string, file: string): string {
  return readFileSync(join(root, file), "utf8");
}

/**
 * Characters that are invisible or change text direction: no-break and soft
 * hyphen spaces, zero-width and direction marks, direction embeddings, overrides
 * and isolates, word joiners and the byte order mark. Built from code points so
 * this file contains none of them.
 */
export const INVISIBLE_CHARACTERS = new RegExp(
  "[" +
    [
      0x00a0, 0x00ad, 0x061c, 0x180e, 0x2000, 0x2001, 0x2002, 0x2003, 0x2004,
      0x2005, 0x2006, 0x2007, 0x2008, 0x2009, 0x200a, 0x200b, 0x200c, 0x200d,
      0x200e, 0x200f, 0x2028, 0x2029, 0x202a, 0x202b, 0x202c, 0x202d, 0x202e,
      0x202f, 0x205f, 0x2060, 0x2061, 0x2062, 0x2063, 0x2064, 0x2066, 0x2067,
      0x2068, 0x2069, 0x3000, 0xfeff,
    ]
      .map((code) => String.fromCharCode(code))
      .join("") +
    "]",
);
