// "Open with the PC's own app" for files MAYA produced. Written against injected dependencies so
// it runs under node --test without Electron; main.mjs wires the real ones.
//
// The renderer never sends a path. It sends an artifact id, a file name and its access token. The
// main process downloads that artifact from the app origin into a MAYA-owned temp folder and only
// ever opens a file inside that folder.
import path from "node:path";

import { isInsideRoot, isTrustedSender } from "./ipc.mjs";

export const OPEN_PATH_CHANNEL = "maya:open-path";

const UUID = /^[0-9a-f]{8}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{4}-[0-9a-f]{12}$/i;
const MAX_NAME_LENGTH = 200;
const MAX_TOKEN_LENGTH = 4096;

/**
 * Types that run code (or can be made to) when opened. Refused outright: the user can still use
 * Download. Every dot-separated part after the first is checked, case-insensitively.
 */
export const BLOCKED_EXTENSIONS = new Set([
  "exe", "com", "scr", "pif", "msi", "msp", "msix", "appx", "bat", "cmd", "ps1", "psm1", "psd1",
  "vbs", "vbe", "js", "jse", "mjs", "cjs", "wsf", "wsh", "hta", "lnk", "url", "scf", "reg",
  "inf", "cpl", "msc", "jar", "dll", "sys", "gadget", "application", "appref-ms", "sh", "app",
  "command", "py", "pyw", "rb", "pl", "php", "swf", "chm", "iso", "img", "vhd", "vhdx",
]);

/**
 * A safe single file name, or null. No separators, no reserved characters, no trailing dot or
 * space (Windows drops them, which would hide the real extension), an extension is required, and
 * no part after the first dot may be a blocked type ("report.pdf.exe" and "x.exe.pdf" both fail).
 * @param {unknown} value
 * @returns {string | null}
 */
export function sanitizeFileName(value) {
  if (typeof value !== "string") return null;
  const name = value.trim();
  if (name === "" || name.length > MAX_NAME_LENGTH) return null;
  // eslint-disable-next-line no-control-regex
  if (/[\x00-\x1f<>:"/\\|?*]/.test(name)) return null;
  if (/[. ]$/.test(name) || name.startsWith(".")) return null;
  if (/^(con|prn|aux|nul|com[1-9]|lpt[1-9])(\.|$)/i.test(name)) return null;
  const parts = name.split(".");
  if (parts.length < 2) return null;
  for (const part of parts.slice(1)) {
    if (BLOCKED_EXTENSIONS.has(part.toLowerCase())) return null;
  }
  return name;
}

/**
 * @param {{
 *   fetch: (url: string, init: { headers: Record<string, string> }) => Promise<{ ok: boolean, status: number, body: unknown }>,
 *   save: (dest: string, body: unknown) => Promise<void>,
 *   mkdir: (dir: string) => Promise<void> | void,
 *   openPath: (p: string) => Promise<string>,
 *   getOrigin: () => string,
 *   tempRoot: string,
 *   path?: typeof path,
 * }} deps
 */
export function createOpenFileIpc(deps) {
  const p = deps.path ?? path;

  /**
   * @param {{ senderFrame?: { url?: string } }} event
   * @param {unknown} request { artifactId, fileName, token }
   * @returns {Promise<{ ok: boolean, reason?: string }>}
   */
  async function openArtifact(event, request) {
    const origin = deps.getOrigin();
    if (!isTrustedSender(event?.senderFrame?.url, origin)) {
      return { ok: false, reason: "untrusted" };
    }
    const r = /** @type {Record<string, unknown>} */ (
      request !== null && typeof request === "object" ? request : {}
    );
    const { artifactId, fileName, token } = r;
    if (typeof artifactId !== "string" || !UUID.test(artifactId)) {
      return { ok: false, reason: "bad-request" };
    }
    if (typeof token !== "string" || token === "" || token.length > MAX_TOKEN_LENGTH) {
      return { ok: false, reason: "bad-request" };
    }
    if (typeof fileName !== "string") return { ok: false, reason: "bad-request" };
    const safe = sanitizeFileName(fileName);
    if (safe === null) return { ok: false, reason: "blocked-type" };

    const dir = p.join(deps.tempRoot, artifactId.toLowerCase());
    const dest = p.join(dir, safe);
    if (!isInsideRoot(dest, deps.tempRoot, p) || p.dirname(dest) !== dir) {
      return { ok: false, reason: "bad-request" };
    }

    try {
      await deps.mkdir(dir);
      const response = await deps.fetch(`${origin}/api/v1/artifacts/${artifactId}`, {
        headers: { Authorization: `Bearer ${token}` },
      });
      if (!response.ok) return { ok: false, reason: "download-failed" };
      await deps.save(dest, response.body);
    } catch {
      return { ok: false, reason: "download-failed" };
    }

    // shell.openPath resolves "" on success, otherwise an error message.
    try {
      const failure = await deps.openPath(dest);
      return failure === "" ? { ok: true } : { ok: false, reason: "open-failed" };
    } catch {
      return { ok: false, reason: "open-failed" };
    }
  }

  return { openArtifact };
}
