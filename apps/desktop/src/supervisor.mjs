// Startup gates, API process ownership and shutdown for the desktop shell.
// Pure Node: no Electron imports; spawn/run/fetch/clock/UI prompts are injected so the state
// machine is unit-testable with fakes. It never reads the env file: it only passes its PATH on.

export const STEPS = {
  docker: "Checking Docker",
  data: "Starting database and cache",
  schema: "Checking database schema",
  api: "Starting the MAYA engine",
  ready: "Waiting for the engine to be ready",
};

export const LIMITS = {
  dockerInfoMs: 20_000,
  composeMs: 240_000,
  alembicCheckMs: 60_000,
  alembicUpgradeMs: 600_000,
  readyMs: 90_000,
  pollMs: 500,
  stopGraceMs: 12_000,
  restartWindowMs: 60_000,
  maxRestarts: 3,
};

const ENV_ALLOW = [
  "PATH", "PATHEXT", "SystemRoot", "SYSTEMROOT", "windir", "COMSPEC", "TEMP", "TMP",
  "USERPROFILE", "HOME", "HOMEDRIVE", "HOMEPATH", "APPDATA", "LOCALAPPDATA", "PROGRAMDATA",
  "ProgramFiles", "ProgramFiles(x86)", "ProgramW6432", "USERNAME", "COMPUTERNAME",
  "DOCKER_HOST", "DOCKER_CONTEXT", "DOCKER_CONFIG", "NUMBER_OF_PROCESSORS", "OS",
];

/**
 * Environment for child processes: an allow-list of OS variables, never the whole process.env.
 * @param {Record<string, string | undefined>} source
 * @param {Record<string, string>} [extra]
 */
export function buildChildEnv(source, extra = {}) {
  /** @type {Record<string, string>} */
  const env = {};
  for (const name of ENV_ALLOW) {
    if (source[name] !== undefined) env[name] = /** @type {string} */ (source[name]);
  }
  return { ...env, PYTHONUNBUFFERED: "1", PYTHONUTF8: "1", ...extra };
}

/**
 * Revision ids from `alembic current` / `alembic heads` stdout (one per line, optional "(head)").
 * @param {string} stdout
 * @returns {string[]}
 */
export function parseRevisions(stdout) {
  const ids = [];
  for (const line of stdout.split(/\r?\n/)) {
    const m = /^([A-Za-z0-9_]+)(?:\s+\([^)]*\))?\s*$/.exec(line.trim());
    if (m && !/^(INFO|WARNING|ERROR|DEBUG)$/.test(m[1])) ids.push(m[1]);
  }
  return ids;
}

/**
 * @typedef {{ phase: string, step?: string, reason?: string, message?: string, url?: string }} SupervisorState
 * @typedef {{ stdin: { end(): void }, stdout: any, stderr: any, pid?: number,
 *   on(event: string, cb: (...a: any[]) => void): any }} ChildLike
 * @typedef {{ run(cmd: string, args: string[], o: { cwd?: string, env?: Record<string,string>, timeoutMs: number }):
 *     Promise<{ code: number, stdout: string, stderr: string }>,
 *   spawn(cmd: string, args: string[], o: object): ChildLike,
 *   fetch(url: string): Promise<{ ok: boolean }>,
 *   sleep(ms: number): Promise<void>, now(): number,
 *   findPort(): Promise<number>, killTree(pid: number): Promise<void>,
 *   confirmMigration(info: { current: string[], heads: string[] }): Promise<boolean>,
 *   onState(state: SupervisorState): void, log(chunk: string): void }} Deps
 * @typedef {{ python: string, backendDir: string, repoRoot: string, composeFile: string,
 *   envFile: string, webDir: string, baseEnv: Record<string, string | undefined> }} Config
 */

/** @param {Deps} deps @param {Config} config */
export function createSupervisor(deps, config) {
  /** @type {{ child: ChildLike, exited: Promise<number>, done: boolean } | null} */
  let current = null;
  let port = 0;
  let stopping = false;
  /** @type {number[]} */
  let restarts = [];

  const state = (/** @type {SupervisorState} */ s) => deps.onState(s);
  const fail = (/** @type {string} */ reason, /** @type {string} */ message) => {
    state({ phase: "failed", reason, message });
    return { ok: /** @type {const} */ (false), reason, message };
  };
  const childEnv = () => buildChildEnv(config.baseEnv);

  function spawnApi() {
    const child = deps.spawn(
      config.python,
      ["-m", "app.desktop", "--port", String(port), "--env-file", config.envFile],
      {
        cwd: config.backendDir,
        env: buildChildEnv(config.baseEnv, { MAYA_WEB_STATIC_DIR: config.webDir }),
        stdio: ["pipe", "pipe", "pipe"],
        windowsHide: true,
      },
    );
    child.stdout?.on("data", (c) => deps.log(String(c)));
    child.stderr?.on("data", (c) => deps.log(String(c)));
    /** @type {{ child: ChildLike, exited: Promise<number>, done: boolean }} */
    const entry = { child, done: false, exited: Promise.resolve(0) };
    entry.exited = new Promise((resolve) => {
      child.on("exit", (code) => {
        entry.done = true;
        resolve(code ?? -1);
      });
      child.on("error", () => {
        entry.done = true;
        resolve(-1);
      });
    });
    current = entry;
    return entry;
  }

  async function ok(/** @type {string} */ path) {
    try {
      return (await deps.fetch(`http://127.0.0.1:${port}${path}`)).ok;
    } catch {
      return false;
    }
  }

  /** @returns {Promise<'ready' | 'exited' | 'timeout'>} */
  async function waitReady(/** @type {NonNullable<typeof current>} */ entry) {
    const deadline = deps.now() + LIMITS.readyMs;
    let live = false;
    while (deps.now() < deadline) {
      if (entry.done) return "exited";
      if (!live) live = await ok("/api/v1/healthz");
      if (live && (await ok("/api/v1/readyz"))) return "ready";
      if (entry.done) return "exited";
      await deps.sleep(LIMITS.pollMs);
    }
    return entry.done ? "exited" : "timeout";
  }

  async function kill(/** @type {NonNullable<typeof current>} */ entry) {
    if (entry.done) return;
    entry.child.stdin.end();
    const graceful = await Promise.race([
      entry.exited.then(() => true),
      deps.sleep(LIMITS.stopGraceMs).then(() => false),
    ]);
    if (!graceful && !entry.done && entry.child.pid) {
      await deps.killTree(entry.child.pid);
      await Promise.race([entry.exited, deps.sleep(2000)]);
    }
  }

  function monitor(/** @type {NonNullable<typeof current>} */ entry) {
    entry.exited.then(() => {
      if (!stopping && current === entry) void recover();
    });
  }

  async function recover() {
    for (;;) {
      const t = deps.now();
      restarts = restarts.filter((x) => t - x < LIMITS.restartWindowMs);
      if (restarts.length >= LIMITS.maxRestarts) {
        fail("api-crash-loop", "The MAYA engine keeps stopping. See the log for details.");
        return;
      }
      restarts.push(t);
      state({ phase: "reconnecting", step: STEPS.api });
      await deps.sleep(1000 * 2 ** (restarts.length - 1));
      if (stopping) return;
      const entry = spawnApi();
      const result = await waitReady(entry);
      if (stopping) return;
      if (result === "ready") {
        monitor(entry);
        state({ phase: "ready", url: `http://127.0.0.1:${port}/` });
        return;
      }
      if (result === "timeout") {
        await kill(entry);
        fail("ready-timeout", "The MAYA engine did not become ready in time.");
        return;
      }
    }
  }

  async function checkSchema() {
    const alembic = (/** @type {string} */ sub, /** @type {number} */ timeoutMs) =>
      deps.run(
        config.python,
        ["-m", "alembic", "-x", `env_file=${config.envFile}`, ...sub.split(" ")],
        { cwd: config.backendDir, env: childEnv(), timeoutMs },
      );
    const [cur, heads] = [await alembic("current", LIMITS.alembicCheckMs), await alembic("heads", LIMITS.alembicCheckMs)];
    if (cur.code !== 0 || heads.code !== 0) {
      return fail("migration-check-failed", "Could not read the database schema version.");
    }
    const have = new Set(parseRevisions(cur.stdout));
    const want = parseRevisions(heads.stdout);
    if (want.every((r) => have.has(r))) return null;
    const confirmed = await deps.confirmMigration({ current: [...have], heads: want });
    if (!confirmed) {
      return fail("migration-declined", "The database needs an update. Nothing was changed.");
    }
    const up = await alembic("upgrade head", LIMITS.alembicUpgradeMs);
    if (up.code !== 0) deps.log(up.stderr);
    return up.code === 0 ? null : fail("migration-failed", "The database update failed.");
  }

  return {
    get port() {
      return port;
    },

    /** Run the startup gates in order. Resolves with ok:true and the app URL, or a failure. */
    async start() {
      stopping = false;
      state({ phase: "starting", step: STEPS.docker });
      const docker = await deps.run("docker", ["info"], { env: childEnv(), timeoutMs: LIMITS.dockerInfoMs });
      if (docker.code !== 0) {
        return fail("docker-unavailable", "Docker Desktop is required and is not running.");
      }

      state({ phase: "starting", step: STEPS.data });
      const compose = await deps.run(
        "docker",
        ["compose", "--env-file", config.envFile, "-f", config.composeFile, "up", "-d", "--wait", "--no-recreate", "postgres", "redis"],
        { cwd: config.repoRoot, env: childEnv(), timeoutMs: LIMITS.composeMs },
      );
      if (compose.code !== 0) {
        deps.log(compose.stderr);
        return fail("compose-failed", "The database and cache containers did not start.");
      }

      state({ phase: "starting", step: STEPS.schema });
      const schema = await checkSchema();
      if (schema) return schema;

      state({ phase: "starting", step: STEPS.api });
      try {
        port = await deps.findPort();
      } catch {
        return fail("no-port", "No free local port is available.");
      }
      const entry = spawnApi();
      state({ phase: "starting", step: STEPS.ready });
      const result = await waitReady(entry);
      if (result === "exited") return fail("api-exited", "The MAYA engine stopped while starting.");
      if (result === "timeout") {
        await kill(entry);
        return fail("ready-timeout", "The MAYA engine did not become ready in time.");
      }
      monitor(entry);
      const url = `http://127.0.0.1:${port}/`;
      state({ phase: "ready", url });
      return { ok: /** @type {const} */ (true), url, port };
    },

    /** Close the API's stdin, wait, then kill the process tree. Containers are left running. */
    async stop() {
      stopping = true;
      if (current) await kill(current);
      state({ phase: "stopped" });
    },
  };
}
