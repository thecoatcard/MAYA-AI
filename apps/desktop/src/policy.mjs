// Pure security policy for the desktop shell. No Electron imports, so it is unit-testable.

/** Scheme of the in-page links the shell's own pages use to ask for an action (no IPC needed). */
export const ACTION_SCHEME = "maya-action:";
const KNOWN_ACTIONS = new Set(["retry", "open-logs"]);

/** @param {number} port */
export function apiOrigin(port) {
  return `http://127.0.0.1:${port}`;
}

/** @param {string} raw @returns {URL | null} */
function parse(raw) {
  try {
    return new URL(raw);
  } catch {
    return null;
  }
}

/**
 * Decide what to do with a navigation in a shell window.
 * @param {string} raw target URL
 * @param {{ origin: string, pagesBase: string }} ctx app origin and the file:// URL of src/pages/
 * @returns {{ kind: 'allow' } | { kind: 'action', action: string } | { kind: 'deny' }}
 */
export function classifyNavigation(raw, { origin, pagesBase }) {
  const url = parse(raw);
  if (!url) return { kind: "deny" };
  if (url.protocol === ACTION_SCHEME) {
    return KNOWN_ACTIONS.has(url.pathname)
      ? { kind: "action", action: url.pathname }
      : { kind: "deny" };
  }
  if (url.protocol === "http:" && url.origin === origin) return { kind: "allow" };
  if (url.protocol === "file:" && url.href.startsWith(pagesBase)) return { kind: "allow" };
  return { kind: "deny" };
}

/**
 * Where window.open / target=_blank goes: only https: opens, in the system browser.
 * @param {string} raw
 * @returns {{ action: 'deny', openExternal?: string }}
 */
export function classifyWindowOpen(raw) {
  const url = parse(raw);
  if (url && url.protocol === "https:" && !url.username && !url.password) {
    return { action: "deny", openExternal: url.href };
  }
  return { action: "deny" };
}

/**
 * Permission policy: microphone (`media`, audio only) for the app origin, nothing else.
 * @param {string} permission
 * @param {string} requestingUrl
 * @param {{ origin: string }} ctx
 * @param {{ mediaTypes?: string[], mediaType?: string }} [details]
 */
export function isPermissionAllowed(permission, requestingUrl, { origin }, details = {}) {
  if (permission !== "media") return false;
  const url = parse(requestingUrl);
  if (!url || url.origin !== origin) return false;
  if (Array.isArray(details.mediaTypes) && details.mediaTypes.some((t) => t !== "audio")) {
    return false;
  }
  if (details.mediaType && details.mediaType !== "audio") return false;
  return true;
}

/**
 * Redact what must not land in a log line or on screen: the user's home directory and
 * `token=`/`password=`/`secret=`/`key=` style values.
 * @param {string} text
 * @param {string} [home]
 */
export function redact(text, home) {
  let out = String(text);
  if (home) {
    for (const variant of new Set([home, home.replaceAll("\\", "/")])) {
      out = out.split(variant).join("~");
    }
  }
  return out.replace(
    /\b(token|password|passwd|secret|api[_-]?key|key)=[^\s&"']+/gi,
    "$1=[redacted]",
  );
}
