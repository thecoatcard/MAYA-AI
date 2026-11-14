import { join } from "node:path";
import { describe, expect, it } from "vitest";

import { listSourceFiles, readCode } from "./source-scan";
import { findViolations } from "./token-scan";

/**
 * Token ratchet. A component may not use raw colours, blur, gradients or glow: colours come from
 * the design tokens (docs/architecture/UI_DESIGN_SYSTEM.md), so light and dark both work.
 *
 * LEGACY lists the files that have not been migrated yet, with the number of raw values they
 * still contain. The rules for editing it:
 *  - a file NOT in the list must have zero violations (new code is clean from the start);
 *  - a listed file may never get worse (count above its number fails);
 *  - when a file reaches zero, delete its entry (a stale entry fails), so the list only shrinks;
 *  - never add a file to the list or raise a number to make this test pass.
 * Each page package removes its files from this list as it migrates them.
 */
const LEGACY: Readonly<Record<string, number>> = {};

const SRC = join(__dirname, "..");

function counts(): Record<string, number> {
  const out: Record<string, number> = {};
  for (const file of listSourceFiles(SRC)) {
    const n = findViolations(readCode(SRC, file)).length;
    if (n > 0) out[file] = n;
  }
  return out;
}

describe("design token ratchet", () => {
  const current = counts();

  it("detects the raw values it is meant to ban", () => {
    const sample = [
      '<div className="bg-white text-zinc-400 border-slate-700/50 backdrop-blur-xl" />',
      'const c = "#1a2b3c"; const d = "rgba(0, 0, 0, 0.4)";',
      '<button className="bg-gradient-to-r from-blue-500 to-purple-600 shadow-[0_0_20px_red]" />',
    ].join("\n");
    const kinds = new Set(findViolations(sample).map((v) => v.kind));
    expect([...kinds].sort()).toEqual(
      [
        "backdrop blur",
        "colour function",
        "glow shadow",
        "gradient utility",
        "hex colour",
        "palette utility",
        "white/black utility",
      ].sort(),
    );
  });

  it("leaves token classes and ids alone", () => {
    const sample =
      '<a href="#maya-main" className="bg-maya-pane text-maya-muted border-maya-border-strong text-maya-small" />';
    expect(findViolations(sample)).toEqual([]);
  });

  it("scans a real set of files", () => {
    expect(listSourceFiles(SRC).length).toBeGreaterThan(50);
  });

  it("has no raw colours, blur, gradients or glow in files outside the legacy list", () => {
    const offenders = Object.entries(current).filter(
      ([file]) => LEGACY[file] === undefined,
    );
    expect(
      offenders,
      `New raw values. Use token classes. Counts: ${JSON.stringify(Object.fromEntries(offenders))}`,
    ).toEqual([]);
  });

  it("never lets a legacy file get worse", () => {
    const worse = Object.entries(LEGACY).filter(
      ([file, allowed]) => (current[file] ?? 0) > allowed,
    );
    expect(worse, "legacy file got worse").toEqual([]);
  });

  it("drops legacy entries once a file is clean", () => {
    const stale = Object.keys(LEGACY).filter(
      (file) => current[file] === undefined,
    );
    expect(
      stale,
      "delete these entries from LEGACY (file is clean or gone)",
    ).toEqual([]);
  });

  it("keeps the legacy numbers tight so progress cannot be undone", () => {
    const loose = Object.entries(LEGACY).filter(
      ([file, allowed]) =>
        current[file] !== undefined && current[file] < allowed,
    );
    expect(
      loose,
      `lower these numbers: ${JSON.stringify(loose.map(([f]) => [f, current[f]]))}`,
    ).toEqual([]);
  });
});
