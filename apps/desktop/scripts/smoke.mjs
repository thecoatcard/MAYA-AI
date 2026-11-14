// End-to-end smoke check against a THROWAWAY database (never the dev database `maya`).
//   npm --prefix apps/desktop run smoke
// 1. create database maya_desktop_smoke inside MAYA's running postgres container
// 2. migrate that database only
// 3. run the real Electron shell hidden (supervisor + API + exported web) and assert
// 4. always drop the smoke database and the temp files
import { execFile, spawn } from "node:child_process";
import fs from "node:fs";
import os from "node:os";
import path from "node:path";
import { fileURLToPath } from "node:url";

const SMOKE_DB = "maya_desktop_smoke";
const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), "..", "..", "..");
const desktopDir = path.join(root, "apps", "desktop");
const backendDir = path.join(root, "backend");
const python = path.join(backendDir, ".venv", "Scripts", "python.exe");
const electronExe = path.join(desktopDir, "node_modules", "electron", "dist", "electron.exe");

/** @returns {Promise<{ code: number, stdout: string, stderr: string }>} */
const run = (cmd, args, opts = {}) =>
  new Promise((resolve) =>
    execFile(cmd, args, { windowsHide: true, maxBuffer: 16_000_000, ...opts }, (error, stdout, stderr) =>
      resolve({ code: error ? (typeof error.code === "number" ? error.code : -1) : 0, stdout: String(stdout), stderr: String(stderr) }),
    ),
  );

const envExample = path.join(root, ".env.example");
let container = "";
let created = false;

async function psql(database, sql) {
  if (database === "maya") throw new Error("Refusing to touch the dev database");
  const r = await run("docker", ["exec", container, "psql", "-U", "maya_migrator", "-d", database, "-v", "ON_ERROR_STOP=1", "-c", sql]);
  if (r.code !== 0) throw new Error(`psql failed: ${r.stderr.trim().split("\n").pop()}`);
}

async function main() {
  const info = await run("docker", ["info"], { timeout: 20_000 });
  if (info.code !== 0) {
    console.log("SMOKE blocked: Docker is not reachable. Nothing was started.");
    return 2;
  }
  const ps = await run("docker", ["compose", "--env-file", envExample, "-f", path.join(root, "docker-compose.yml"), "ps", "-q", "postgres"], { cwd: root });
  container = ps.stdout.trim().split(/\s+/)[0] ?? "";
  if (!container) {
    console.log("SMOKE blocked: MAYA's postgres container is not running (project maya-foundation).");
    return 2;
  }

  const tmp = fs.mkdtempSync(path.join(os.tmpdir(), "maya-desktop-smoke-"));
  try {
    console.log(`SMOKE step create database ${SMOKE_DB}`);
    await psql("postgres", `DROP DATABASE IF EXISTS ${SMOKE_DB} WITH (FORCE)`);
    await psql("postgres", `CREATE DATABASE ${SMOKE_DB} OWNER maya_migrator`);
    created = true;
    await psql(SMOKE_DB, "CREATE EXTENSION IF NOT EXISTS vector; CREATE EXTENSION IF NOT EXISTS pg_trgm");

    // .env.example-style config: example credentials only; compose-facing values stay unchanged
    // so `docker compose up --no-recreate` leaves the running containers alone.
    const text = fs.readFileSync(envExample, "utf8")
      .replace(/^(MAYA_DATABASE_URL=.*)\/maya$/m, `$1/${SMOKE_DB}`)
      .replace(/^(MAYA_MIGRATION_DATABASE_URL=.*)\/maya$/m, `$1/${SMOKE_DB}`)
      .replace(/^(MAYA_REDIS_URL=.*)\/0$/m, "$1/15");
    const envFile = path.join(tmp, "smoke.env");
    fs.writeFileSync(envFile, text);

    console.log("SMOKE step migrate smoke database");
    const up = await run(python, ["-m", "alembic", "-x", `env_file=${envFile}`, "upgrade", "head"], { cwd: backendDir, timeout: 300_000 });
    if (up.code !== 0) {
      console.log(`SMOKE FAIL migration: ${up.stderr.trim().split("\n").slice(-3).join(" | ")}`);
      return 1;
    }

    console.log("SMOKE step run the shell (hidden window)");
    const code = await new Promise((resolve) => {
      const child = spawn(electronExe, [".", `--user-data-dir=${path.join(tmp, "user-data")}`], {
        cwd: desktopDir,
        windowsHide: true,
        env: { ...process.env, MAYA_ENV_FILE: envFile, MAYA_DESKTOP_SMOKE: "1" },
      });
      const timer = setTimeout(() => {
        console.log("SMOKE FAIL timeout (240 s)");
        run("taskkill", ["/pid", String(child.pid), "/T", "/F"]);
      }, 240_000);
      const out = (b) => String(b).split(/\r?\n/).filter((l) => l.startsWith("SMOKE ")).forEach((l) => console.log(l));
      child.stdout.on("data", out);
      child.stderr.on("data", (b) => { if (/error|fail/i.test(String(b))) console.log(`electron stderr: ${String(b).trim().slice(0, 300)}`); });
      child.on("exit", (c) => { clearTimeout(timer); resolve(c ?? 1); });
    });
    return code;
  } finally {
    if (created) {
      try {
        await psql("postgres", `DROP DATABASE IF EXISTS ${SMOKE_DB} WITH (FORCE)`);
        console.log(`SMOKE cleanup: dropped ${SMOKE_DB}`);
      } catch (error) {
        console.log(`SMOKE cleanup FAILED, drop ${SMOKE_DB} manually: ${error.message}`);
      }
    }
    fs.rmSync(tmp, { recursive: true, force: true });
  }
}

main().then((code) => {
  console.log(code === 0 ? "SMOKE PASSED" : `SMOKE did not pass (exit ${code})`);
  process.exit(code);
}, (error) => {
  console.log(`SMOKE error: ${error.message}`);
  process.exit(1);
});
