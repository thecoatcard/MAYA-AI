import assert from "node:assert/strict";
import fs from "node:fs";
import net from "node:net";
import os from "node:os";
import path from "node:path";
import { test } from "node:test";

import { RotatingLog } from "../src/logs.mjs";
import { findRepoRoot, resolveEnvFile, resolveLocations } from "../src/paths.mjs";
import { findFreePort } from "../src/ports.mjs";
import { sanitizeBounds } from "../src/window-state.mjs";

test("findFreePort returns a bindable loopback port", async () => {
  const port = await findFreePort();
  assert.ok(port > 0 && port < 65536);
  await new Promise((resolve, reject) => {
    const s = net.createServer().once("error", reject);
    s.listen(port, "127.0.0.1", () => s.close(resolve));
  });
});

test("findFreePort rejects when the OS gives no port", async () => {
  const fake = {
    createServer: () => ({
      unref() {}, once() {}, address: () => null,
      listen(_p, _h, cb) { cb(); }, close(cb) { cb(); },
    }),
  };
  await assert.rejects(findFreePort(/** @type {any} */ (fake)));
});

test("env file: MAYA_ENV_FILE wins, then .env, then .env.example; contents are never read", () => {
  const root = path.resolve("/repo");
  const seen = [];
  const exists = (p) => { seen.push(p); return existing.has(p); };
  let existing = new Set([path.join(root, ".env"), path.resolve("/custom.env")]);
  assert.deepEqual(resolveEnvFile({ env: { MAYA_ENV_FILE: "/custom.env" }, repoRoot: root, exists }), { file: path.resolve("/custom.env"), source: "MAYA_ENV_FILE" });
  assert.equal(resolveEnvFile({ env: {}, repoRoot: root, exists }).source, ".env");
  existing = new Set();
  assert.equal(resolveEnvFile({ env: {}, repoRoot: root, exists }).file, path.join(root, ".env.example"));
  assert.throws(() => resolveEnvFile({ env: { MAYA_ENV_FILE: "/missing.env" }, repoRoot: root, exists }), /does not exist/);
});

test("env file resolution only calls exists(), never touches the filesystem contents", () => {
  const spy = fs.readFileSync;
  let reads = 0;
  // @ts-expect-error test spy
  fs.readFileSync = (...a) => { reads++; return spy(...a); };
  try {
    resolveEnvFile({ env: {}, repoRoot: path.resolve("/repo"), exists: () => true });
  } finally {
    fs.readFileSync = spy;
  }
  assert.equal(reads, 0);
});

test("repo root and locations", () => {
  const root = path.resolve("/r");
  const exists = (p) => p === path.join(root, "docker-compose.yml") || p === path.join(root, "backend", "alembic.ini");
  assert.equal(findRepoRoot(path.join(root, "apps", "desktop", "src"), exists), root);
  assert.equal(findRepoRoot(path.resolve("/elsewhere"), exists), null);
  const dev = resolveLocations({ isPackaged: false, appDir: path.join(root, "apps", "desktop", "src"), env: {}, exists });
  assert.equal(dev.python, path.join(root, "backend", ".venv", "Scripts", "python.exe"));
  assert.equal(dev.webDir, path.join(root, "apps", "web", ".next-desktop"));
  assert.throws(() => resolveLocations({ isPackaged: true, appDir: "/x", resourcesPath: "/res", env: {}, exists }), /MAYA_REPO_ROOT/);
  const packaged = resolveLocations({ isPackaged: true, appDir: "/x", resourcesPath: path.resolve("/res"), env: { MAYA_REPO_ROOT: root, MAYA_PYTHON: "C:\\py\\python.exe" }, exists });
  assert.equal(packaged.webDir, path.join(path.resolve("/res"), "web"));
  assert.equal(packaged.python, "C:\\py\\python.exe");
});

test("RotatingLog redacts, rotates and keeps a bounded number of files", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "maya-log-test-"));
  try {
    const log = new RotatingLog({ dir, maxBytes: 100, keep: 3, home: "C:\\Users\\Someone" });
    log.write("path C:\\Users\\Someone\\x token=SECRET\n");
    assert.match(fs.readFileSync(log.file, "utf8"), /path ~\\x token=\[redacted\]/);
    for (let i = 0; i < 10; i++) log.write("x".repeat(60) + "\n");
    const files = fs.readdirSync(dir).sort();
    assert.deepEqual(files, ["api.1.log", "api.2.log", "api.log"]);
  } finally {
    fs.rmSync(dir, { recursive: true, force: true });
  }
});

test("sanitizeBounds falls back on garbage and clamps to the minimum", () => {
  const d = { width: 1280, height: 820, minWidth: 960, minHeight: 640 };
  assert.deepEqual(sanitizeBounds(null, d), { width: 1280, height: 820, maximized: false });
  assert.deepEqual(sanitizeBounds({ width: "a", height: 5 }, d), { width: 1280, height: 820, maximized: false });
  assert.deepEqual(sanitizeBounds({ width: 100, height: 100, x: 10, y: 20, maximized: true }, d), { width: 960, height: 640, x: 10, y: 20, maximized: true });
});
