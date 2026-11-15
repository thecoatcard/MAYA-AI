/**
 * A small LaTeX-to-readable-text converter for chat math. Since KaTeX typesets math (see
 * lib/math-katex.ts), this is the FALLBACK: shown until KaTeX has loaded and whenever KaTeX
 * refuses a formula.
 *
 * Models write LaTeX ("$O(n^2)$"). This turns the
 * common subset into plain Unicode: superscripts and subscripts, Greek letters, relations,
 * `\text{...}`, `\frac{a}{b}` as "a/b" and `\sqrt{x}` as "√x". The result is only ever rendered
 * through React text nodes, so nothing here can produce markup.
 *
 * Contract: `ok` is true only when EVERY command was understood. A string with an unknown command
 * (a matrix, `\binom`, an unmapped macro) returns `ok: false` and the caller shows the original
 * source in a code-styled span, never a half-converted formula.
 *
 */

export interface LatexText {
  text: string;
  ok: boolean;
}

const SYMBOLS: Readonly<Record<string, string>> = {
  alpha: "α",
  beta: "β",
  gamma: "γ",
  delta: "δ",
  epsilon: "ε",
  varepsilon: "ε",
  zeta: "ζ",
  eta: "η",
  theta: "θ",
  vartheta: "ϑ",
  iota: "ι",
  kappa: "κ",
  lambda: "λ",
  mu: "μ",
  nu: "ν",
  xi: "ξ",
  pi: "π",
  rho: "ρ",
  sigma: "σ",
  tau: "τ",
  upsilon: "υ",
  phi: "φ",
  varphi: "φ",
  chi: "χ",
  psi: "ψ",
  omega: "ω",
  Gamma: "Γ",
  Delta: "Δ",
  Theta: "Θ",
  Lambda: "Λ",
  Xi: "Ξ",
  Pi: "Π",
  Sigma: "Σ",
  Phi: "Φ",
  Psi: "Ψ",
  Omega: "Ω",
  times: "×",
  cdot: "·",
  div: "÷",
  pm: "±",
  mp: "∓",
  le: "≤",
  leq: "≤",
  ge: "≥",
  geq: "≥",
  ne: "≠",
  neq: "≠",
  approx: "≈",
  equiv: "≡",
  sim: "∼",
  infty: "∞",
  sum: "Σ",
  prod: "∏",
  to: "→",
  rightarrow: "→",
  leftarrow: "←",
  leftrightarrow: "↔",
  Rightarrow: "⇒",
  Leftarrow: "⇐",
  Leftrightarrow: "⇔",
  implies: "⇒",
  iff: "⇔",
  in: "∈",
  notin: "∉",
  subset: "⊂",
  subseteq: "⊆",
  supset: "⊃",
  supseteq: "⊇",
  cup: "∪",
  cap: "∩",
  emptyset: "∅",
  forall: "∀",
  exists: "∃",
  neg: "¬",
  land: "∧",
  lor: "∨",
  oplus: "⊕",
  mid: "∣",
  ldots: "…",
  dots: "…",
  cdots: "⋯",
  lfloor: "⌊",
  rfloor: "⌋",
  lceil: "⌈",
  rceil: "⌉",
  partial: "∂",
  nabla: "∇",
  degree: "°",
  prime: "′",
  bmod: " mod ",
  mod: " mod ",
};

/** Operator names that are written as plain words. */
const OPERATORS: ReadonlySet<string> = new Set([
  "log",
  "ln",
  "lg",
  "exp",
  "sin",
  "cos",
  "tan",
  "min",
  "max",
  "lim",
  "gcd",
  "lcm",
  "det",
  "arg",
  "sup",
  "inf",
]);

/** Commands that only style their argument; the argument is kept. */
const STYLES: ReadonlySet<string> = new Set([
  "mathrm",
  "mathit",
  "mathbf",
  "mathsf",
  "mathtt",
  "text",
  "textbf",
  "textit",
  "textrm",
  "operatorname",
  "boldsymbol",
  "mbox",
]);

const BLACKBOARD: Readonly<Record<string, string>> = {
  N: "ℕ",
  Z: "ℤ",
  Q: "ℚ",
  R: "ℝ",
  C: "ℂ",
};

const SPACES: ReadonlySet<string> = new Set([
  ",",
  ";",
  ":",
  "!",
  " ",
  "quad",
  "qquad",
]);
const LITERALS = "{}%$&#_|";

const SUPERSCRIPT: Readonly<Record<string, string>> = {
  "0": "⁰",
  "1": "¹",
  "2": "²",
  "3": "³",
  "4": "⁴",
  "5": "⁵",
  "6": "⁶",
  "7": "⁷",
  "8": "⁸",
  "9": "⁹",
  "+": "⁺",
  "-": "⁻",
  "=": "⁼",
  "(": "⁽",
  ")": "⁾",
  n: "ⁿ",
  i: "ⁱ",
  k: "ᵏ",
  x: "ˣ",
  m: "ᵐ",
  t: "ᵗ",
  a: "ᵃ",
  b: "ᵇ",
};

const SUBSCRIPT: Readonly<Record<string, string>> = {
  "0": "₀",
  "1": "₁",
  "2": "₂",
  "3": "₃",
  "4": "₄",
  "5": "₅",
  "6": "₆",
  "7": "₇",
  "8": "₈",
  "9": "₉",
  "+": "₊",
  "-": "₋",
  "=": "₌",
  "(": "₍",
  ")": "₎",
  i: "ᵢ",
  j: "ⱼ",
  k: "ₖ",
  n: "ₙ",
  m: "ₘ",
  x: "ₓ",
  a: "ₐ",
  e: "ₑ",
  o: "ₒ",
  t: "ₜ",
};

const MAX_DEPTH = 24;
const MAX_LENGTH = 4000;

class Unsupported extends Error {}

function isLetter(ch: string): boolean {
  return /[A-Za-z]/.test(ch);
}

function mapAll(
  value: string,
  table: Readonly<Record<string, string>>,
): string | null {
  let out = "";
  for (const ch of value) {
    const mapped = table[ch];
    if (mapped === undefined) {
      return null;
    }
    out += mapped;
  }
  return out;
}

class Converter {
  private i = 0;

  constructor(private readonly src: string) {}

  run(): string {
    const out = this.sequence(0, null);
    return out.replace(/[ \t]+/g, " ").trim();
  }

  /** Converts until the end, or until the unmatched `}` that closes a group. */
  private sequence(depth: number, close: "}" | null): string {
    if (depth > MAX_DEPTH) {
      throw new Unsupported();
    }
    let out = "";
    while (this.i < this.src.length) {
      const ch = this.src.charAt(this.i);
      if (ch === "}") {
        if (close === null) {
          throw new Unsupported();
        }
        this.i += 1;
        return out;
      }
      if (ch === "{") {
        this.i += 1;
        out += this.sequence(depth + 1, "}");
        continue;
      }
      if (ch === "\\") {
        out += this.command(depth);
        continue;
      }
      if (ch === "^" || ch === "_") {
        this.i += 1;
        out += this.script(ch, depth);
        continue;
      }
      if (ch === "~") {
        out += " ";
      } else if (ch === "&") {
        throw new Unsupported(); // alignment or matrix cell
      } else {
        out += ch;
      }
      this.i += 1;
    }
    if (close !== null) {
      throw new Unsupported(); // unclosed group
    }
    return out;
  }

  /** One argument: a braced group, a command, or a single character. */
  private argument(depth: number): string {
    this.skipSpaces();
    if (this.i >= this.src.length) {
      throw new Unsupported();
    }
    const ch = this.src.charAt(this.i);
    if (ch === "{") {
      this.i += 1;
      return this.sequence(depth + 1, "}");
    }
    if (ch === "\\") {
      return this.command(depth + 1);
    }
    this.i += 1;
    return ch;
  }

  private skipSpaces(): void {
    while (this.src.charAt(this.i) === " ") {
      this.i += 1;
    }
  }

  private script(kind: "^" | "_", depth: number): string {
    const arg = this.argument(depth).trim();
    if (arg === "") {
      return "";
    }
    const mapped = mapAll(arg, kind === "^" ? SUPERSCRIPT : SUBSCRIPT);
    if (mapped !== null) {
      return mapped;
    }
    return arg.length === 1 ? `${kind}${arg}` : `${kind}(${arg})`;
  }

  private command(depth: number): string {
    this.i += 1; // the backslash
    const first = this.src.charAt(this.i);
    if (first === "") {
      throw new Unsupported();
    }
    if (!isLetter(first)) {
      this.i += 1;
      if (first === "\\") {
        return " ";
      }
      if (SPACES.has(first)) {
        return " ";
      }
      if (LITERALS.includes(first)) {
        return first;
      }
      if (first === "(" || first === ")" || first === "[" || first === "]") {
        return first;
      }
      throw new Unsupported();
    }
    let end = this.i;
    while (end < this.src.length && isLetter(this.src.charAt(end))) {
      end += 1;
    }
    const name = this.src.slice(this.i, end);
    this.i = end;
    return this.named(name, depth);
  }

  private named(name: string, depth: number): string {
    const symbol = SYMBOLS[name];
    if (symbol !== undefined) {
      return symbol;
    }
    if (OPERATORS.has(name)) {
      return name;
    }
    if (SPACES.has(name)) {
      return " ";
    }
    if (name === "left" || name === "right") {
      this.skipSpaces();
      const delim = this.src.charAt(this.i);
      if (delim === ".") {
        this.i += 1;
        return "";
      }
      if (delim === "\\") {
        return this.command(depth + 1); // \left\{ , \left\lfloor
      }
      this.i += 1;
      return delim;
    }
    if (name === "frac" || name === "dfrac" || name === "tfrac") {
      const top = this.argument(depth).trim();
      const bottom = this.argument(depth).trim();
      return `${wrapIfComplex(top)}/${wrapIfComplex(bottom)}`;
    }
    if (name === "sqrt") {
      let index = "";
      if (this.src.charAt(this.i) === "[") {
        const close = this.src.indexOf("]", this.i);
        if (close === -1) {
          throw new Unsupported();
        }
        const raw = this.src.slice(this.i + 1, close);
        index = mapAll(raw, SUPERSCRIPT) ?? "";
        if (index === "") {
          throw new Unsupported();
        }
        this.i = close + 1;
      }
      const body = this.argument(depth).trim();
      return `${index}√${/^[A-Za-z0-9]+$/.test(body) ? body : `(${body})`}`;
    }
    if (name === "mathbb") {
      const body = this.argument(depth).trim();
      const mapped = BLACKBOARD[body];
      if (mapped === undefined) {
        throw new Unsupported();
      }
      return mapped;
    }
    if (name === "mathcal" || name === "mathscr") {
      return this.argument(depth);
    }
    if (STYLES.has(name)) {
      return this.argument(depth);
    }
    throw new Unsupported();
  }
}

function wrapIfComplex(part: string): string {
  return /^[A-Za-z0-9.]+$/.test(part) ? part : `(${part})`;
}

/** Converts LaTeX source (no surrounding `$`) to readable text. See the module comment. */
export function latexToText(tex: string): LatexText {
  const source = tex.replace(/\s+/g, " ").trim();
  if (source === "" || source.length > MAX_LENGTH) {
    return { text: tex, ok: false };
  }
  try {
    return { text: new Converter(source).run(), ok: true };
  } catch (error) {
    if (error instanceof Unsupported) {
      return { text: tex, ok: false };
    }
    throw error;
  }
}
