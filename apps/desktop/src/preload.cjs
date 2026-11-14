// Sandboxed preload (must be CommonJS). Exposes a small frozen surface: read-only facts plus three
// invoke-only calls (folder picker, reveal in file manager, open an artifact in its own app). Nothing else crosses the bridge and
// no Electron object or listener is handed to the page.
const { contextBridge, ipcRenderer } = require("electron");

const versionArg = process.argv.find((a) => a.startsWith("--maya-version="));

contextBridge.exposeInMainWorld(
  "maya",
  Object.freeze({
    isDesktop: true,
    platform: process.platform,
    version: versionArg ? versionArg.slice("--maya-version=".length) : "unknown",
    /** Opens the native folder dialog. Resolves the absolute path, or null when cancelled. */
    pickFolder: () => ipcRenderer.invoke("maya:pick-folder"),
    /** Shows a path inside a folder you chose in the file manager. Resolves true when shown. */
    revealPath: (target) =>
      typeof target === "string" ? ipcRenderer.invoke("maya:reveal-path", target) : Promise.resolve(false),
    /** Tells the shell the app theme so the window-control strip matches it. Fire and forget. */
    setTheme: (theme) =>
      theme === "dark" || theme === "light" ? ipcRenderer.invoke("maya:set-theme", theme) : Promise.resolve(false),
    /**
     * EXPERIMENTAL: creates (true) or removes (false) the one Windows Scheduled Task "MAYA AI
     * Scheduler" that runs due schedules while you are logged on. Asks you to confirm first.
     * Resolves { ok, reason? }.
     */
    registerScheduleWake: (enabled) =>
      typeof enabled === "boolean"
        ? ipcRenderer.invoke("maya:register-schedule-wake", enabled)
        : Promise.resolve({ ok: false, reason: "bad-request" }),
    /**
     * Downloads a stored artifact to a MAYA temp folder and opens it with the PC's default app.
     * Takes { artifactId, fileName, token }, never a path. Resolves { ok, reason? }.
     */
    openArtifact: (request) =>
      request !== null && typeof request === "object"
        ? ipcRenderer.invoke("maya:open-path", {
            artifactId: request.artifactId,
            fileName: request.fileName,
            token: request.token,
          })
        : Promise.resolve({ ok: false, reason: "bad-request" }),
  }),
);
