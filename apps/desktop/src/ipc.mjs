// Folder-picker and reveal IPC, written against injected dependencies so it runs under
// node --test without Electron. main.mjs wires the real ones.
import path from "node:path";

export const PICK_FOLDER_CHANNEL = "maya:pick-folder";
export const REVEAL_PATH_CHANNEL = "maya:reveal-path";

export const SET_THEME_CHANNEL = "maya:set-theme";

/** Window-control colours (the min/max/close strip) for each app theme. */
export const TITLEBAR_COLORS = Object.freeze({
  dark: Object.freeze({ color: "#12151b", symbolColor: "#e8eaee", background: "#0b0d11" }),
  light: Object.freeze({ color: "#ffffff", symbolColor: "#101216", background: "#f1f2f5" }),
});

/**
 * Only the two known theme names are accepted, from the app page itself.
 * @param {unknown} theme
 * @returns {{ color: string, symbolColor: string, background: string } | null}
 */
export function titleBarColorsFor(theme) {
  return theme === "dark" || theme === "light" ? TITLEBAR_COLORS[theme] : null;
}

const MAX_PATH_LENGTH = 1024;
const MAX_ROOTS = 20;

/**
 * Accept only an absolute local path: a string, bounded, no NUL, no UNC or device prefix.
 * @param {unknown} value
 * @param {typeof path} [p] path flavour (tests pass path.win32 or path.posix)
 * @returns {string | null} the normalised path, or null when it is not acceptable
 */
export function validateAbsolutePath(value, p = path) {
  if (typeof value !== "string") return null;
  if (value.length === 0 || value.length > MAX_PATH_LENGTH) return null;
  if (value.includes("\0")) return null;
  if (/^[\\/]{2}/.test(value)) return null; // UNC (\\server\share), //host and \\?\ device paths
  if (!p.isAbsolute(value)) return null;
  return p.normalize(value);
}

/**
 * True when `target` is `root` or lies inside it. Case-insensitive for Windows paths.
 * @param {string} target
 * @param {string} root
 * @param {typeof path} [p]
 */
export function isInsideRoot(target, root, p = path) {
  const fold = p === path.win32 || (p === path && process.platform === "win32");
  const a = fold ? target.toLowerCase() : target;
  const b = fold ? root.toLowerCase() : root;
  const rel = p.relative(b, a);
  return rel === "" || (!rel.startsWith("..") && !p.isAbsolute(rel));
}

/**
 * The app page that asked must be the one served from the API origin; the local status
 * pages and any other frame get nothing.
 * @param {string | undefined} senderUrl
 * @param {string} origin
 */
export function isTrustedSender(senderUrl, origin) {
  if (!origin || typeof senderUrl !== "string") return false;
  try {
    return new URL(senderUrl).origin === origin;
  } catch {
    return false;
  }
}

/**
 * @param {{
 *   dialog: { showOpenDialog: (win: any, options: any) => Promise<{ canceled: boolean, filePaths: string[] }> },
 *   shell: { showItemInFolder: (p: string) => void },
 *   getWindow: () => any,
 *   getOrigin: () => string,
 *   exists: (p: string) => boolean,
 *   loadRoots: () => string[],
 *   saveRoots: (roots: string[]) => void,
 *   path?: typeof path,
 * }} deps
 */
export function createFolderIpc(deps) {
  const p = deps.path ?? path;
  /** Folders the person chose with the dialog: the only places "reveal" may point into. */
  let roots = deps.loadRoots().filter((r) => validateAbsolutePath(r, p) !== null);

  /** @param {{ senderFrame?: { url?: string } }} event */
  const trusted = (event) => isTrustedSender(event?.senderFrame?.url, deps.getOrigin());

  return {
    /** @returns {Promise<string | null>} */
    async pickFolder(event) {
      if (!trusted(event)) return null;
      const win = deps.getWindow();
      if (!win) return null;
      const result = await deps.dialog.showOpenDialog(win, {
        properties: ["openDirectory", "createDirectory"],
      });
      if (result.canceled || result.filePaths.length === 0) return null;
      const picked = validateAbsolutePath(result.filePaths[0], p);
      if (picked === null) return null;
      roots = [picked, ...roots.filter((r) => r !== picked)].slice(0, MAX_ROOTS);
      try {
        deps.saveRoots(roots);
      } catch {
        // remembered for this session only
      }
      return picked;
    },

    /** @returns {Promise<boolean>} true when the item was revealed */
    async revealPath(event, target) {
      if (!trusted(event)) return false;
      const clean = validateAbsolutePath(target, p);
      if (clean === null) return false;
      if (!roots.some((r) => isInsideRoot(clean, r, p))) return false;
      if (!deps.exists(clean)) return false;
      deps.shell.showItemInFolder(clean);
      return true;
    },

    /** For tests and diagnostics. */
    get roots() {
      return [...roots];
    },
  };
}
