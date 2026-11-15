import assert from "node:assert/strict";
import { EventEmitter } from "node:events";
import { test } from "node:test";

import { buildChildEnv, createSupervisor, LIMITS, parseRevisions } from "../src/supervisor.mjs";

const settle = () => new Promise((r) => setImmediate(r));
const rev = (id) => `${id} (head)\n`;

function harness({ run, fetchOk = () => true, exitOnEnd = true, confirm = async () => true } = {}) {
  const events = [];
  const states = [];
  const logs = [];
  const children = [];
  let clock = 0;
  const sleeps = [];

  const defaultRun = async (cmd, args) => {
    if (cmd === "docker" && args[0] === "info") return { code: 0, stdout: "", stderr: "" };
    if (cmd === "docker") return { code: 0, stdout: "", stderr: "" };
    if (args.includes("current")) return { code: 0, stdout: rev("0012_x"), stderr: "" };
    if (args.includes("heads")) return { code: 0, stdout: rev("0012_x"), stderr: "" };
    return { code: 0, stdout: "", stderr: "" };
  };

  const deps = {
    run: async (cmd, args, o) => {
      events.push([cmd, ...args].join(" "));
      return (run ?? defaultRun)(cmd, args, o);
    },
    spawn: (cmd, args, o) => {
      const child = Object.assign(new EventEmitter(), {
        pid: 1000 + children.length, stdout: new EventEmitter(), stderr: new EventEmitter(),
        args, options: o, ended: false,
        stdin: { end() { events.push("stdin.end"); child.ended = true; if (exitOnEnd) child.emit("exit", 0); } },
      });
      events.push("spawn");
      children.push(child);
      return child;
    },
    fetch: async (url) => ({ ok: fetchOk(url) }),
    sleep: async (ms) => { sleeps.push(ms); clock += ms; },
    now: () => clock,
    findPort: async () => 4321,
    killTree: async (pid) => { events.push(`killTree ${pid}`); children.find((c) => c.pid === pid)?.emit("exit", 1); },
    confirmMigration: async (info) => { events.push("confirm"); return confirm(info); },
    onState: (s) => states.push(s),
    log: (c) => logs.push(c),
  };
  const config = {
    python: "py.exe", backendDir: "B", repoRoot: "R", composeFile: "R/docker-compose.yml",
    envFile: "R/.env.example", webDir: "W", baseEnv: { PATH: "p", MAYA_JWT_SIGNING_KEY: "TOPSECRET", SystemRoot: "C:\\Windows" },
  };
  return { sup: createSupervisor(deps, config), events, states, logs, children, sleeps, advance: (ms) => { clock += ms; } };
}

test("happy path runs the gates in order and reports the app URL", async () => {
  const h = harness();
  const result = await h.sup.start();
  assert.deepEqual(result, { ok: true, url: "http://127.0.0.1:4321/", port: 4321 });
  const order = h.events.map((e) => e.split(" ").slice(0, 3).join(" "));
  assert.equal(order[0], "docker info");
  assert.ok(order[1].startsWith("docker compose --env-file"));
  assert.ok(h.events[1].includes("up -d --wait --no-recreate postgres redis"));
  assert.ok(h.events[2].includes("-m alembic -x env_file=R/.env.example current"));
  assert.ok(h.events[3].includes("heads"));
  assert.equal(h.events[4], "spawn");
  assert.equal(h.states.at(-1).phase, "ready");
  assert.deepEqual(h.states.filter((s) => s.step).map((s) => s.step), [
    "Checking Docker", "Starting database and cache", "Checking database schema",
    "Starting the MAYA engine", "Waiting for the engine to be ready",
  ]);
});

test("never removes containers or volumes and only names MAYA's services", async () => {
  const h = harness();
  await h.sup.start();
  await h.sup.stop();
  for (const e of h.events) {
    assert.ok(!/\b(down|rm|prune|-v|volume)\b/.test(e), e);
  }
  const compose = h.events.find((e) => e.startsWith("docker compose"));
  assert.ok(compose.endsWith("postgres redis"));
});

test("API gets the web dir and the env-file PATH, and no unrelated environment", async () => {
  const h = harness();
  await h.sup.start();
  const child = h.children[0];
  assert.deepEqual(child.args, ["-m", "app.desktop", "--port", "4321", "--env-file", "R/.env.example"]);
  assert.equal(child.options.env.MAYA_WEB_STATIC_DIR, "W");
  assert.equal(child.options.env.PATH, "p");
  assert.equal(child.options.env.MAYA_JWT_SIGNING_KEY, undefined);
  assert.equal(child.options.cwd, "B");
});

test("docker missing: stops at gate 1, nothing else runs", async () => {
  const h = harness({ run: async () => ({ code: -1, stdout: "", stderr: "not found" }) });
  const result = await h.sup.start();
  assert.equal(result.reason, "docker-unavailable");
  assert.deepEqual(h.events, ["docker info"]);
  assert.equal(h.states.at(-1).phase, "failed");
});

test("compose failure fails before migrations and logs stderr", async () => {
  const h = harness({
    run: async (cmd, args) =>
      args[0] === "compose" ? { code: 1, stdout: "", stderr: "boom" } : { code: 0, stdout: "", stderr: "" },
  });
  const result = await h.sup.start();
  assert.equal(result.reason, "compose-failed");
  assert.ok(h.logs.includes("boom"));
  assert.ok(!h.events.some((e) => e.includes("alembic")));
});

test("database behind: asks first, upgrades only after confirmation", async () => {
  const run = async (cmd, args) => {
    if (args.includes("current")) return { code: 0, stdout: rev("0010_old"), stderr: "" };
    if (args.includes("heads")) return { code: 0, stdout: rev("0012_x"), stderr: "" };
    return { code: 0, stdout: "", stderr: "" };
  };
  const yes = harness({ run, confirm: async (i) => { assert.deepEqual(i, { current: ["0010_old"], heads: ["0012_x"] }); return true; } });
  assert.equal((await yes.sup.start()).ok, true);
  const iConfirm = yes.events.indexOf("confirm");
  const iUp = yes.events.findIndex((e) => e.endsWith("upgrade head"));
  assert.ok(iConfirm > -1 && iUp > iConfirm);

  const no = harness({ run, confirm: async () => false });
  const result = await no.sup.start();
  assert.equal(result.reason, "migration-declined");
  assert.ok(!no.events.some((e) => e.includes("upgrade")));
  assert.ok(!no.events.includes("spawn"));
});

test("up-to-date database never prompts or upgrades", async () => {
  const h = harness();
  await h.sup.start();
  assert.ok(!h.events.includes("confirm"));
  assert.ok(!h.events.some((e) => e.includes("upgrade")));
});

test("failed migration check or upgrade stops startup", async () => {
  const bad = harness({ run: async (c, a) => (a.includes("current") ? { code: 1, stdout: "", stderr: "x" } : { code: 0, stdout: "", stderr: "" }) });
  assert.equal((await bad.sup.start()).reason, "migration-check-failed");
  const run = async (c, a) => {
    if (a.includes("current")) return { code: 0, stdout: "", stderr: "" };
    if (a.includes("heads")) return { code: 0, stdout: rev("0001"), stderr: "" };
    if (a.includes("upgrade")) return { code: 1, stdout: "", stderr: "bad" };
    return { code: 0, stdout: "", stderr: "" };
  };
  assert.equal((await harness({ run }).sup.start()).reason, "migration-failed");
});

test("readiness timeout kills the engine and fails", async () => {
  const h = harness({ fetchOk: () => false, exitOnEnd: false });
  const result = await h.sup.start();
  assert.equal(result.reason, "ready-timeout");
  assert.ok(h.events.includes("killTree 1000"));
  assert.ok(h.sleeps.reduce((a, b) => a + b, 0) >= LIMITS.readyMs);
});

test("healthz is polled before readyz", async () => {
  const urls = [];
  const h = harness({ fetchOk: (u) => { urls.push(u); return !(u.endsWith("/readyz") && urls.length < 4); } });
  await h.sup.start();
  assert.ok(urls[0].endsWith("/api/v1/healthz"));
  assert.ok(urls.some((u) => u.endsWith("/api/v1/readyz")));
});

test("engine exiting during startup fails with api-exited", async () => {
  let h;
  h = harness({ fetchOk: () => { h.children[0].emit("exit", 3); return false; } });
  assert.equal((await h.sup.start()).reason, "api-exited");
});

test("crash restart budget: 3 restarts per 60 s with backoff, then failed", async () => {
  const h = harness();
  await h.sup.start();
  for (let i = 0; i < 3; i++) {
    h.children.at(-1).emit("exit", 1);
    await settle();
    assert.equal(h.states.at(-1).phase, "ready", `restart ${i + 1}`);
  }
  assert.equal(h.children.length, 4);
  h.children.at(-1).emit("exit", 1);
  await settle();
  assert.equal(h.children.length, 4);
  assert.equal(h.states.at(-1).phase, "failed");
  assert.equal(h.states.at(-1).reason, "api-crash-loop");
  assert.deepEqual(h.sleeps.filter((s) => s >= 1000 && s <= 4000), [1000, 2000, 4000]);
});

test("crashes spread over more than 60 s do not exhaust the budget", async () => {
  const h = harness();
  await h.sup.start();
  for (let i = 0; i < 5; i++) {
    h.advance(LIMITS.restartWindowMs + 1);
    h.children.at(-1).emit("exit", 1);
    await settle();
    assert.equal(h.states.at(-1).phase, "ready");
  }
});

test("shutdown: stdin closes first; no kill when the engine exits gracefully", async () => {
  const h = harness();
  await h.sup.start();
  await h.sup.stop();
  assert.deepEqual(h.events.slice(-1), ["stdin.end"]);
  assert.ok(!h.events.some((e) => e.startsWith("killTree")));
  await settle();
  assert.equal(h.states.at(-1).phase, "stopped");
});

test("shutdown: waits the grace period, then kills the process tree after closing stdin", async () => {
  const h = harness({ exitOnEnd: false });
  await h.sup.start();
  await h.sup.stop();
  const tail = h.events.slice(-2);
  assert.deepEqual(tail, ["stdin.end", "killTree 1000"]);
  assert.ok(h.sleeps.includes(LIMITS.stopGraceMs));
  assert.equal(LIMITS.stopGraceMs, 12_000);
});

test("a stopped supervisor does not restart the engine", async () => {
  const h = harness();
  await h.sup.start();
  await h.sup.stop();
  await settle();
  assert.equal(h.children.length, 1);
});

test("parseRevisions and buildChildEnv", () => {
  assert.deepEqual(parseRevisions("INFO  [alembic.runtime.migration] Context impl\n0012_audit (head)\n\n0011_b\n"), ["0012_audit", "0011_b"]);
  assert.deepEqual(parseRevisions(""), []);
  const env = buildChildEnv({ PATH: "a", MAYA_GEMINI_API_KEY: "k", SystemRoot: "r" }, { X: "1" });
  assert.equal(env.PATH, "a");
  assert.equal(env.MAYA_GEMINI_API_KEY, undefined);
  assert.equal(env.X, "1");
});
