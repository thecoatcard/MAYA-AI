// Electron main process: one window, hardened, supervising Docker data services and the API.
import { app, BrowserWindow, dialog, ipcMain, screen, session, shell } from "electron";
import { execFile, spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { Readable } from "node:stream";
import { pipeline } from "node:stream/promises";
import { fileURLToPath, pathToFileURL } from "node:url";

import { greetingScript } from "./greeting.mjs";
import {
  createFolderIpc,
  isTrustedSender,
  PICK_FOLDER_CHANNEL,
  REVEAL_PATH_CHANNEL,
  SET_THEME_CHANNEL,
  titleBarColorsFor,
} from "./ipc.mjs";
import { createOpenFileIpc, OPEN_PATH_CHANNEL } from "./open-file.mjs";
import { RotatingLog } from "./logs.mjs";
import { resolveEnvFile, resolveLocations } from "./paths.mjs";
import { classifyNavigation, classifyWindowOpen, isPermissionAllowed, redact } from "./policy.mjs";
import { findFreePort } from "./ports.mjs";
import { createScheduleWakeIpc, runHeadless, SCHEDULE_WAKE_CHANNEL, wantsHeadlessRun } from "./scheduler-run.mjs";
import { buildChildEnv, createSupervisor, STEPS } from "./supervisor.mjs";
import { isWithinAnyDisplay, sanitizeBounds } from "./window-state.mjs";

const here = path.dirname(fileURLToPath(import.meta.url));
const pagesDir = path.join(here, "pages");
const pagesBase = pathToFileURL(pagesDir + path.sep).href;
const SMOKE = process.env.MAYA_DESKTOP_SMOKE === "1";
// `electron . --run-schedules`: no window; run due scheduled reports once and quit (ADR-0033).
const HEADLESS = wantsHeadlessRun(process.argv);
const PARTITION = "persist:maya";
const TITLEBAR_HEIGHT = 36;
const DEFAULTS = { width: 1280, height: 820, minWidth: 960, minHeight: 640 };

/** @type {BrowserWindow | null} */
let win = null;
let origin = "";
let currentPage = "";
let quitting = false;
/** UI updates run strictly in order. */
let queue = Promise.resolve();
/** @type {ReturnType<typeof createSupervisor> | null} */
let supervisor = null;
/** @type {RotatingLog | null} */
let log = null;

if (!SMOKE && !app.requestSingleInstanceLock()) {
  // Another MAYA is open (it runs schedules itself) or a headless run is under way.
  app.quit();
} else if (HEADLESS) {
  app.on("second-instance", async () => {
    // The person opened MAYA while the headless run was working: stop it and open the app.
    app.relaunch({ args: process.argv.slice(1).filter((a) => a !== "--run-schedules") });
    quitting = true;
    if (supervisor) {
      try {
        await supervisor.stop();
      } catch {
        // stop errors should not block app exit
      }
    }
    app.exit(0);
  });
  app.whenReady().then(bootHeadless);
} else {
  app.on("second-instance", () => {
    if (win) {
      if (win.isMinimized()) win.restore();
      win.focus();
    }
  });
  app.whenReady().then(boot);
}

/** Every web contents gets the same navigation rules, including ones created later. */
app.on("web-contents-created", (_event, contents) => {
  const guard = (/** @type {Electron.Event} */ event, /** @type {string} */ url) => {
    const decision = classifyNavigation(url, { origin, pagesBase });
    if (decision.kind === "allow") return;
    event.preventDefault();
    if (decision.kind === "action") void handleAction(decision.action);
  };
  contents.on("will-navigate", (event, url) => guard(event, url));
  contents.on("will-redirect", (event, url) => guard(event, url));
  contents.on("will-attach-webview", (event) => event.preventDefault());
  contents.setWindowOpenHandler(({ url }) => {
    const decision = classifyWindowOpen(url);
    if (decision.openExternal) void shell.openExternal(decision.openExternal);
    return { action: "deny" };
  });
});

app.on("window-all-closed", () => app.quit());
app.on("before-quit", (event) => {
  if (quitting) return;
  quitting = true;
  clearOpenTemp();
  if (!supervisor) return;
  event.preventDefault();
  supervisor.stop().finally(() => app.quit());
});

const stateFile = () => path.join(app.getPath("userData"), "window-state.json");

function createWindow() {
  let saved = sanitizeBounds(null, DEFAULTS);
  if (!SMOKE) {
    try {
      const displays = screen.getAllDisplays();
      saved = sanitizeBounds(JSON.parse(fs.readFileSync(stateFile(), "utf8")), DEFAULTS, displays);
    } catch {
      // first run or unreadable: defaults
    }
  }
  if (saved.x !== undefined && saved.y !== undefined && !isWithinAnyDisplay(saved, screen.getAllDisplays())) {
    delete saved.x;
    delete saved.y;
  }
  win = new BrowserWindow({
    ...saved,
    minWidth: DEFAULTS.minWidth,
    minHeight: DEFAULTS.minHeight,
    show: !SMOKE,
    title: "MAYA",
    backgroundColor: "#0b0d11",
    titleBarStyle: "hidden",
    titleBarOverlay: {
      color: "#12151b",
      symbolColor: "#e8eaee",
      height: TITLEBAR_HEIGHT,
    },
    webPreferences: {
      preload: path.join(here, "preload.cjs"),
      additionalArguments: [`--maya-version=${app.getVersion()}`],
      partition: PARTITION,
      contextIsolation: true,
      sandbox: true,
      nodeIntegration: false,
      webSecurity: true,
      allowRunningInsecureContent: false,
      webviewTag: false,
      devTools: !app.isPackaged,
    },
  });
  if (saved.maximized) win.maximize();
  win.on("close", () => {
    if (SMOKE || !win || win.isMinimized()) return;
    try {
      fs.writeFileSync(stateFile(), JSON.stringify({ ...win.getNormalBounds(), maximized: win.isMaximized() }));
    } catch {
      // not worth failing the close
    }
  });
  win.on("closed", () => (win = null));
}

const rootsFile = () => path.join(app.getPath("userData"), "picked-folders.json");

/** Folder picker and reveal: only the app page on the API origin may call them. */
function installFolderIpc() {
  const folderIpc = createFolderIpc({
    dialog,
    shell,
    getWindow: () => win,
    getOrigin: () => origin,
    exists: fs.existsSync,
    loadRoots: () => {
      try {
        const list = JSON.parse(fs.readFileSync(rootsFile(), "utf8"));
        return Array.isArray(list) ? list.filter((x) => typeof x === "string") : [];
      } catch {
        return [];
      }
    },
    saveRoots: (roots) => fs.writeFileSync(rootsFile(), JSON.stringify(roots)),
  });
  ipcMain.handle(PICK_FOLDER_CHANNEL, (event) => folderIpc.pickFolder(event));
  ipcMain.handle(REVEAL_PATH_CHANNEL, (event, target) => folderIpc.revealPath(event, target));
  // The app's own theme drives the native window-control strip (Windows/Linux overlay).
  ipcMain.handle(SET_THEME_CHANNEL, (event, theme) => {
    const colors = titleBarColorsFor(theme);
    if (!colors || !win || !isTrustedSender(event?.senderFrame?.url, origin)) return false;
    try {
      win.setBackgroundColor(colors.background);
      if (typeof win.setTitleBarOverlay === "function") {
        win.setTitleBarOverlay({ color: colors.color, symbolColor: colors.symbolColor, height: TITLEBAR_HEIGHT });
      }
      return true;
    } catch {
      return false;
    }
  });
}

/** Folder MAYA downloads files into before opening them; emptied on start and on quit. */
const openTempRoot = () => path.join(app.getPath("temp"), "maya-open");

function clearOpenTemp() {
  try {
    fs.rmSync(openTempRoot(), { recursive: true, force: true });
  } catch {
    // a file may still be open in its app; it is cleaned next time
  }
}

/** Open an artifact with the PC's own app: only files MAYA downloaded itself into its temp folder. */
function installOpenFileIpc() {
  clearOpenTemp();
  const openIpc = createOpenFileIpc({
    getOrigin: () => origin,
    tempRoot: openTempRoot(),
    mkdir: (dir) => fs.promises.mkdir(dir, { recursive: true }),
    fetch: (url, init) => fetch(url, { ...init, signal: AbortSignal.timeout(120_000) }),
    save: (dest, body) => pipeline(Readable.fromWeb(/** @type {any} */ (body)), fs.createWriteStream(dest)),
    openPath: (target) => shell.openPath(target),
  });
  ipcMain.handle(OPEN_PATH_CHANNEL, (event, request) => openIpc.openArtifact(event, request));
}

function installSessionPolicy() {
  const ses = session.fromPartition(PARTITION);
  ses.setPermissionRequestHandler((contents, permission, callback, details) => {
    callback(isPermissionAllowed(permission, details.requestingUrl || contents.getURL(), { origin }, details));
  });
  ses.setPermissionCheckHandler((_contents, permission, requestingOrigin, details) =>
    isPermissionAllowed(permission, details.requestingUrl || requestingOrigin, { origin }, details),
  );
}

/**
 * Show one of the local static pages and fill its [data-field] elements. Values go in through
 * textContent only, so no markup from logs or errors can run.
 * @param {string} name
 * @param {Record<string, string>} fields
 */
async function showPage(name, fields) {
  if (!win) return;
  if (currentPage !== name) {
    currentPage = name;
    await win.loadFile(path.join(pagesDir, `${name}.html`));
  }
  await win.webContents.executeJavaScript(
    `for (const [k, v] of Object.entries(${JSON.stringify(fields)})) {
       const els = document.querySelectorAll('[data-field="' + k + '"]');
       for (const el of els) el.textContent = v;
     }
     const stepKey = ${JSON.stringify(fields.stepKey ?? "")};
     document.body.dataset.step = stepKey;
     const steps = ["docker", "data", "schema", "api", "ready"];
     const currentIndex = steps.indexOf(stepKey);
     for (const li of document.querySelectorAll('ol li')) {
       const stepIdx = steps.findIndex((s) => li.classList.contains(s));
       let statusEl = li.querySelector('.status-text');
       if (!statusEl) {
         statusEl = document.createElement('span');
         statusEl.className = 'visually-hidden status-text';
         li.appendChild(statusEl);
       }
       if (stepIdx !== -1 && stepIdx === currentIndex) {
         li.setAttribute('aria-current', 'step');
         statusEl.textContent = ' (current step)';
       } else if (stepIdx !== -1 && currentIndex !== -1 && stepIdx < currentIndex) {
         li.removeAttribute('aria-current');
         statusEl.textContent = ' (completed)';
       } else {
         li.removeAttribute('aria-current');
         statusEl.textContent = ' (pending)';
       }
     }
     const retry = document.querySelector('a[href="maya-action:retry"]');
     if (retry && typeof retry.focus === 'function') retry.focus();`,
  );
}

let greeted = false;

/**
 * MAYA says hello once per launch while it starts, with the PC's own offline voice (no network, no
 * backend needed). Off in smoke runs and with MAYA_STARTUP_GREETING=0.
 */
async function greetOnce() {
  if (greeted || SMOKE || !win || process.env.MAYA_STARTUP_GREETING === "0") return;
  greeted = true;
  // Not awaited: speaking takes seconds and must not hold up the start-up progress updates. The
  // script picks a female voice and stays quiet when none is available.
  win.webContents
    .executeJavaScript(greetingScript("Hi, I'm MAYA. Getting everything ready for you."))
    .catch(() => {});
}

/** @param {import('./supervisor.mjs').SupervisorState} s */
async function onState(s) {
  const stepKey = Object.keys(STEPS).find((k) => STEPS[/** @type {keyof typeof STEPS} */ (k)] === s.step) ?? "";
  if (s.phase === "starting" || s.phase === "reconnecting") {
    const stepText = s.phase === "reconnecting" ? "Reconnecting to the MAYA engine" : (s.step ?? "");
    await showPage("starting", {
      step: stepText,
      stepKey,
      statusText: `Starting MAYA: ${stepText}`,
    });
    if (s.phase === "starting") await greetOnce();
  } else if (s.phase === "failed") {
    currentPage = "";
    const page = s.reason === "docker-unavailable" ? "docker-required" : "failed";
    await showPage(page, {
      message: s.message ?? "",
      logPath: redact(log?.file ?? "", os.homedir()),
    });
  } else if (s.phase === "ready" && s.url && win) {
    currentPage = "";
    await win.loadURL(s.url);
  }
}

/** @param {string} action */
async function handleAction(action) {
  if (action === "retry") await startUp();
  if (action === "open-logs" && log) void shell.openPath(log.dir);
}

/** @param {string} cmd @param {string[]} args @param {{ cwd?: string, env?: Record<string,string>, timeoutMs: number }} o */
function run(cmd, args, o) {
  return new Promise((resolve) => {
    execFile(
      cmd, args,
      { cwd: o.cwd, env: o.env, timeout: o.timeoutMs, windowsHide: true, maxBuffer: 8_000_000 },
      (error, stdout, stderr) => {
        const code = error ? (typeof error.code === "number" ? error.code : -1) : 0;
        resolve({ code, stdout: String(stdout), stderr: String(stderr) });
      },
    );
  });
}

async function confirmMigration() {
  if (SMOKE || !win) return false; // never run a migration unattended
  const { response } = await dialog.showMessageBox(win, {
    type: "question",
    title: "Update the MAYA database?",
    message: "The local MAYA database is behind this version of the app.",
    detail:
      "MAYA can run the pending schema updates now (alembic upgrade head). Back up anything " +
      "important first. Nothing is changed unless you choose Update.",
    buttons: ["Update database", "Not now"],
    defaultId: 1,
    cancelId: 1,
    noLink: true,
  });
  return response === 0;
}

async function startUp() {
  if (!supervisor) return;
  const result = await supervisor.start();
  if (SMOKE) await finishSmoke(result);
}

/** @param {{ ok: boolean, reason?: string }} result */
async function finishSmoke(result) {
  let code = 1;
  try {
    if (!result.ok || !win) {
      console.log(`SMOKE fail startup reason=${result.reason}`);
    } else {
      await queue;
      await new Promise((r) => setTimeout(r, 2000)); // let the exported page hydrate
      const { runSmokeChecks } = await import("./smoke.mjs");
      const checks = await runSmokeChecks(win);
      for (const c of checks) console.log(`SMOKE ${c.ok ? "ok  " : "FAIL"} ${c.name}: ${c.detail}`);
      code = checks.every((c) => c.ok) ? 0 : 1;
    }
  } finally {
    await supervisor?.stop();
    console.log(`SMOKE exit=${code}`);
    quitting = true;
    app.exit(code);
  }
}

/** @param {any} locations @param {{ file: string }} envFile @param {(s: import('./supervisor.mjs').SupervisorState) => void} onState */
function makeSupervisor(locations, envFile, onState) {
  return createSupervisor(
    {
      run,
      spawn: (cmd, args, options) => spawn(cmd, args, options),
      fetch: (url) => fetch(url, { signal: AbortSignal.timeout(2000) }),
      sleep: (ms) => new Promise((r) => setTimeout(r, ms)),
      now: () => Date.now(),
      findPort: findFreePort,
      killTree: (pid) => run("taskkill", ["/pid", String(pid), "/T", "/F"], { timeoutMs: 10_000 }).then(() => {}),
      // Never migrate unattended: the headless run declines and reports it.
      confirmMigration: HEADLESS ? async () => false : confirmMigration,
      onState,
      log: (chunk) => log?.write(chunk),
    },
    { ...locations, envFile: envFile.file, baseEnv: process.env },
  );
}

/** The headless one-shot (`--run-schedules`): supervisor + run_due, no window, then exit. */
async function bootHeadless() {
  let code = 1;
  try {
    const locations = resolveLocations({
      isPackaged: app.isPackaged,
      appDir: here,
      resourcesPath: process.resourcesPath,
      env: process.env,
      exists: fs.existsSync,
    });
    const envFile = resolveEnvFile({ env: process.env, repoRoot: locations.repoRoot, exists: fs.existsSync });
    log = new RotatingLog({ dir: app.getPath("logs"), home: os.homedir() });
    log.write(`--- MAYA headless schedule run; env file source: ${envFile.source}
`);
    supervisor = makeSupervisor(locations, envFile, () => {});
    const result = await runHeadless({
      supervisor,
      run,
      config: { python: locations.python, backendDir: locations.backendDir, envFile: envFile.file },
      env: buildChildEnv(process.env),
      log: (line) => log?.write(line),
    });
    code = result.ok ? 0 : 1;
  } catch (error) {
    log?.write(`headless: failed ${redact(String(error?.message ?? error), os.homedir())}
`);
  } finally {
    quitting = true;
    app.exit(code);
  }
}

/** One Windows Scheduled Task for the headless run: only after the person confirms (experimental). */
function installScheduleWakeIpc() {
  const wakeIpc = createScheduleWakeIpc({
    getOrigin: () => origin,
    platform: process.platform,
    exe: process.execPath,
    args: app.isPackaged ? "--run-schedules" : `"${here.replace(/[\/]src$/, "")}" --run-schedules`,
    tempFile: () => path.join(app.getPath("temp"), `maya-scheduler-${process.pid}.xml`),
    // schtasks wants UTF-16 with a byte order mark.
    writeTask: (file, xml) => fs.promises.writeFile(file, Buffer.from("﻿" + xml, "utf16le")),
    removeFile: (file) => fs.promises.rm(file, { force: true }),
    runSchtasks: (args, timeoutMs) => run("schtasks", args, { env: buildChildEnv(process.env), timeoutMs }),
    confirm: async (enabled) => {
      if (!win) return false;
      const { response } = await dialog.showMessageBox(win, {
        type: "question",
        title: enabled ? "Wake my PC to run schedules (experimental)" : "Stop running schedules in the background",
        message: enabled
          ? "Let MAYA run your scheduled reports in the background?"
          : "Remove the background schedule runner?",
        detail: enabled
          ? "Experimental. This creates ONE Windows Scheduled Task named 'MAYA AI Scheduler' for your user " +
            "account only: it runs at logon and every 30 minutes while you are logged on, starts MAYA without a " +
            "window, runs due reports and exits. It needs Docker Desktop running and the PC on. No admin " +
            "rights and no password are used; your secrets stay in the env file. You can remove it here at any time."
          : "This deletes the 'MAYA AI Scheduler' Windows task. Schedules still run while MAYA is open.",
        buttons: [enabled ? "Create the task" : "Remove it", "Cancel"],
        defaultId: 1,
        cancelId: 1,
        noLink: true,
      });
      return response === 0;
    },
  });
  ipcMain.handle(SCHEDULE_WAKE_CHANNEL, (event, enabled) => wakeIpc.register(event, enabled));
}

async function boot() {
  try {
    const locations = resolveLocations({
      isPackaged: app.isPackaged,
      appDir: here,
      resourcesPath: process.resourcesPath,
      env: process.env,
      exists: fs.existsSync,
    });
    const envFile = resolveEnvFile({ env: process.env, repoRoot: locations.repoRoot, exists: fs.existsSync });
    if (!fs.existsSync(path.join(locations.webDir, "index.html"))) {
      throw new Error("The web app export is missing. Run: npm --prefix apps/desktop run build:web");
    }
    log = new RotatingLog({ dir: app.getPath("logs"), home: os.homedir() });
    log.write(`--- MAYA desktop start; env file source: ${envFile.source}\n`);

    createWindow();
    installSessionPolicy();
    installFolderIpc();
    installOpenFileIpc();
    installScheduleWakeIpc();
    supervisor = makeSupervisor(locations, envFile, (s) => {
      if (s.url) origin = new URL(s.url).origin;
      queue = queue.then(() => onState(s)).catch(() => {});
    });
    await startUp();
  } catch (error) {
    dialog.showErrorBox("MAYA could not start", redact(String(error?.message ?? error), os.homedir()));
    app.exit(1);
  }
}
