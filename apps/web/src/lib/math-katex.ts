/**
 * KaTeX typesetting for chat math, plus the sanitiser for the markup KaTeX returns.
 *
 * KaTeX is loaded on first use (dynamic import) so it is not part of the first load. Its HTML is
 * never trusted as a string: it is parsed inertly with DOMParser and rebuilt node by node from an
 * allowlist of elements, attributes and CSS properties, so a script, an event handler, a
 * `javascript:` or `data:` URL, a stylesheet or a CSS `url()` can not survive. The caller appends
 * the returned DocumentFragment; no HTML string is ever assigned to the page.
 */

import type katexType from "katex";

export type KatexApi = typeof katexType;

/** Real macros the chat may use; small on purpose. */
const MACROS: Record<string, string> = {
  "\\R": "\\mathbb{R}",
  "\\N": "\\mathbb{N}",
  "\\Z": "\\mathbb{Z}",
  "\\Q": "\\mathbb{Q}",
  "\\C": "\\mathbb{C}",
};

const UNTRUSTED_COMMAND =
  /\\(href|url|includegraphics|html[A-Za-z]*)(?![A-Za-z])/;

let katexPromise: Promise<KatexApi> | null = null;
let katexLoaded: KatexApi | null = null;

/** Load KaTeX once. A failed load is retried on the next call. */
export function loadKatex(): Promise<KatexApi> {
  if (katexPromise === null) {
    katexPromise = import("katex").then(
      (module) => {
        katexLoaded = module.default;
        return module.default;
      },
      (error: unknown) => {
        katexPromise = null;
        throw error;
      },
    );
  }
  return katexPromise;
}

/** KaTeX if it has already finished loading, else null (never starts a load). */
export function getLoadedKatex(): KatexApi | null {
  return katexLoaded;
}

// ---------------------------------------------------------------------------
// Sanitiser
// ---------------------------------------------------------------------------

const HTML_NS = "http://www.w3.org/1999/xhtml";
const MATHML_NS = "http://www.w3.org/1998/Math/MathML";
const SVG_NS = "http://www.w3.org/2000/svg";

const ALLOWED_ELEMENTS: Record<string, ReadonlySet<string>> = {
  [HTML_NS]: new Set(["span"]),
  [MATHML_NS]: new Set([
    "math",
    "semantics",
    "mrow",
    "mi",
    "mn",
    "mo",
    "mtext",
    "mspace",
    "msup",
    "msub",
    "msubsup",
    "mfrac",
    "msqrt",
    "mroot",
    "munder",
    "mover",
    "munderover",
    "mtable",
    "mtr",
    "mtd",
    "mstyle",
    "mpadded",
    "mphantom",
    "menclose",
    "mmultiscripts",
    "mprescripts",
    "none",
    "annotation",
  ]),
  [SVG_NS]: new Set(["svg", "path", "line"]),
};

const ALLOWED_ATTRIBUTES: ReadonlySet<string> = new Set([
  "class",
  "style",
  "aria-hidden",
  "xmlns",
  "display",
  "displaystyle",
  "scriptlevel",
  "mathvariant",
  "mathsize",
  "mathcolor",
  "mathbackground",
  "encoding",
  "width",
  "height",
  "depth",
  "lspace",
  "rspace",
  "voffset",
  "minsize",
  "maxsize",
  "linethickness",
  "stretchy",
  "fence",
  "separator",
  "accent",
  "accentunder",
  "movablelimits",
  "largeop",
  "symmetric",
  "form",
  "notation",
  "columnalign",
  "rowalign",
  "columnspacing",
  "rowspacing",
  "columnlines",
  "rowlines",
  "equalrows",
  "equalcolumns",
  "columnwidth",
  "align",
  "frame",
  "viewBox",
  "preserveAspectRatio",
  "d",
  "x1",
  "x2",
  "y1",
  "y2",
  "stroke-width",
  "fill",
  "stroke",
]);

const ALLOWED_STYLE_PROPERTIES: ReadonlySet<string> = new Set([
  "height",
  "width",
  "min-width",
  "top",
  "bottom",
  "left",
  "right",
  "margin-left",
  "margin-right",
  "margin-top",
  "margin-bottom",
  "padding-left",
  "padding-right",
  "vertical-align",
  "border-bottom-width",
  "border-top-width",
  "border-left-width",
  "border-right-width",
  "border-color",
  "color",
  "background-color",
]);

/** Only plain numbers with units, keywords and hex colours: no function, no url(), no escape. */
const SAFE_STYLE_VALUE = /^[a-z0-9\s.#%+-]{1,40}$/i;
const SAFE_CLASS = /^[A-Za-z0-9_\- ]*$/;
// Whitespace and C0 control characters browsers ignore inside a URL scheme.
const SCHEME_NOISE = new RegExp("[\\s\\u0000-\\u001f]+", "g");

function isSafeAttributeValue(value: string): boolean {
  const compact = value.replace(SCHEME_NOISE, "").toLowerCase();
  return (
    !compact.includes("javascript:") &&
    !compact.includes("data:") &&
    !compact.includes("vbscript:") &&
    !value.includes("<")
  );
}

function sanitizeStyle(value: string): string {
  const kept: string[] = [];
  for (const declaration of value.split(";")) {
    const colon = declaration.indexOf(":");
    if (colon < 0) continue;
    const property = declaration.slice(0, colon).trim().toLowerCase();
    const propertyValue = declaration.slice(colon + 1).trim();
    if (
      ALLOWED_STYLE_PROPERTIES.has(property) &&
      SAFE_STYLE_VALUE.test(propertyValue)
    ) {
      kept.push(`${property}:${propertyValue}`);
    }
  }
  return kept.join(";");
}

function copyNode(source: Node, target: Document): Node | null {
  if (source.nodeType === 3) {
    return target.createTextNode(source.nodeValue ?? "");
  }
  if (source.nodeType !== 1) {
    return null; // comments, processing instructions, CDATA
  }
  const element = source as Element;
  const namespace = element.namespaceURI ?? HTML_NS;
  const name = element.localName;
  if (!ALLOWED_ELEMENTS[namespace]?.has(name)) {
    return null; // script, style, a, img, foreignObject, ... dropped with their content
  }
  const copy = target.createElementNS(namespace, name);
  for (const attribute of Array.from(element.attributes)) {
    const attrName = attribute.name;
    if (attribute.namespaceURI !== null && attrName !== "xmlns") continue;
    if (!ALLOWED_ATTRIBUTES.has(attrName)) continue; // also drops every on* handler and href
    let value = attribute.value;
    if (!isSafeAttributeValue(value)) continue;
    if (attrName === "class" && !SAFE_CLASS.test(value)) continue;
    if (attrName === "style") {
      value = sanitizeStyle(value);
      if (value === "") continue;
    }
    copy.setAttribute(attrName, value);
  }
  for (const child of Array.from(element.childNodes)) {
    const childCopy = copyNode(child, target);
    if (childCopy !== null) copy.appendChild(childCopy);
  }
  return copy;
}

/**
 * Rebuild KaTeX output from an allowlist. Returns a DocumentFragment that holds only allowed
 * elements, attributes and style properties (an empty fragment for hostile input).
 */
export function sanitizeMathHtml(html: string): DocumentFragment {
  const parsed = new DOMParser().parseFromString(html, "text/html");
  const fragment = document.createDocumentFragment();
  for (const child of Array.from(parsed.body.childNodes)) {
    const copy = copyNode(child, document);
    if (copy !== null) fragment.appendChild(copy);
  }
  return fragment;
}

// ---------------------------------------------------------------------------
// Typesetting
// ---------------------------------------------------------------------------

/**
 * Typeset `tex` with KaTeX. Returns a sanitised fragment, or null when KaTeX reports any error,
 * refuses a command (\href, \url, \htmlClass, ... need trust), hits a limit, or the sanitised
 * result is empty. The caller then shows the text fallback.
 */
export function typesetMath(
  katex: KatexApi,
  tex: string,
  display: boolean,
): DocumentFragment | null {
  // Commands that need trust. Without trust KaTeX would draw them as plain letters (no active
  // content, but misleading), so refuse them up front and let the caller show the source.
  if (UNTRUSTED_COMMAND.test(tex)) {
    return null;
  }
  let html: string;
  try {
    html = katex.renderToString(tex, {
      displayMode: display,
      // true (not false): KaTeX then throws on a parse error, an unsupported or untrusted
      // command (\href, \htmlClass, ...) and a limit hit, instead of drawing red error text.
      // The caller shows the text fallback for every one of those.
      throwOnError: true,
      strict: "ignore",
      trust: false,
      output: "htmlAndMathml",
      macros: { ...MACROS },
      maxSize: 20,
      maxExpand: 1000,
    });
  } catch {
    return null;
  }
  const fragment = sanitizeMathHtml(html);
  return fragment.childNodes.length === 0 ? null : fragment;
}
