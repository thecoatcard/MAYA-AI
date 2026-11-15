// Starts the desktop app in development. It removes ELECTRON_RUN_AS_NODE first: VS Code's terminal
// (and some other Electron-based tools) set it, and with it Electron runs main.mjs as plain Node,
// which fails with "The requested module 'electron' does not provide an export named 'BrowserWindow'".
import { spawn } from "node:child_process";
import fs from "node:fs";
import { createRequire } from "node:module";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const require = createRequire(import.meta.url);
const electronPath = require("electron"); // in plain Node this is the path of the Electron binary
const appDir = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..");

const env = { ...process.env };
delete env.ELECTRON_RUN_AS_NODE;

// Windows: Electron 44 dies instantly with STATUS_BREAKPOINT (0x80000003, exit code -2147483645) when its
// binary lives in a folder with a restrictive ACL (e.g. an "Everyone: DENY" entry or a Low integrity label,
// as left behind by some sandboxed coding tools). The same binary runs fine from a clean folder, so on that
// crash we copy Electron's dist to the local app-data folder once and launch it from there.
const STATUS_BREAKPOINT = -2147483645;
const args = [appDir, ...process.argv.slice(2)];

function launch(exe, onCrash) {
  const startedAt = Date.now();
  const child = spawn(exe, args, { stdio: "inherit", env, windowsHide: false });
  child.on("exit", (code, signal) => {
    const early = Date.now() - startedAt < 15_000;
    if (onCrash && process.platform === "win32" && early && (code === STATUS_BREAKPOINT || code === STATUS_BREAKPOINT + 2 ** 32)) return onCrash();
    process.exit(code ?? (signal ? 1 : 0));
  });
  child.on("error", (error) => {
    console.error(`Could not start Electron: ${error.message}`);
    process.exit(1);
  });
}

function relocatedElectron() {
  const distDir = path.dirname(electronPath);
  const version = fs.readFileSync(path.join(distDir, "version"), "utf8").trim();
  // The per-user local app-data folder (what %LOCALAPPDATA% points to), derived from the home folder.
  const localData = path.join(os.homedir(), "AppData", "Local");
  const target = path.join(fs.existsSync(localData) ? localData : os.tmpdir(), "maya-ai", "electron", version);
  const exe = path.join(target, path.basename(electronPath));
  if (!fs.existsSync(exe)) {
    console.warn(`Electron crashed on startup from ${distDir}; copying it to ${target} and retrying...`);
    fs.mkdirSync(target, { recursive: true });
    fs.cpSync(distDir, target, { recursive: true });
  }
  return exe;
}

launch(electronPath, () => launch(relocatedElectron(), null));
