import assert from "node:assert/strict";
import { test } from "node:test";

import {
  buildTaskXml,
  createScheduleWakeIpc,
  runHeadless,
  TASK_NAME,
  validateExePath,
  wantsHeadlessRun,
  xmlEscape,
} from "../src/scheduler-run.mjs";

const ORIGIN = "http://127.0.0.1:5000";
const good = { senderFrame: { url: `${ORIGIN}/settings/` } };
const EXE = "C:\\Program Files\\MAYA\\MAYA.exe";

function make(overrides = {}) {
  const calls = { schtasks: [], written: [], removed: [], confirms: [] };
  const deps = {
    getOrigin: () => ORIGIN,
    platform: "win32",
    exe: EXE,
    args: "--run-schedules",
    tempFile: () => "C:\\Temp\\maya-task.xml",
    writeTask: async (file, xml) => calls.written.push({ file, xml }),
    removeFile: async (file) => calls.removed.push(file),
    // A fake: the real schtasks is never executed in tests.
    runSchtasks: async (args) => {
      calls.schtasks.push(args);
      return overrides.schtasks?.(args) ?? { code: 0, stdout: "", stderr: "" };
    },
    confirm: async (enabled) => {
      calls.confirms.push(enabled);
      return overrides.confirm ?? true;
    },
    ...overrides.deps,
  };
  return { ipc: createScheduleWakeIpc(deps), calls };
}

test("wantsHeadlessRun detects the flag only", () => {
  assert.equal(wantsHeadlessRun(["electron", ".", "--run-schedules"]), true);
  assert.equal(wantsHeadlessRun(["electron", "."]), false);
});

test("validateExePath: absolute local .exe only", () => {
  assert.equal(validateExePath(EXE), EXE);
  for (const bad of [42, "", "MAYA.exe", "\\\\srv\\share\\a.exe", "C:\\a\\b.bat", "C:\\a\0.exe", "C:\\" + "a".repeat(1100) + ".exe"]) {
    assert.equal(validateExePath(bad), null, String(bad));
  }
});

test("task XML: one task, logon + 30 minute triggers, least privilege, escaped values", () => {
  const xml = buildTaskXml({ exe: EXE, args: '"C:\\Dev & Co\\app" --run-schedules' });
  assert.equal((xml.match(/<Task /g) ?? []).length, 1);
  assert.match(xml, /<LogonTrigger>/);
  assert.match(xml, /<Interval>PT30M<\/Interval>/);
  assert.match(xml, /<LogonType>InteractiveToken<\/LogonType>/);
  assert.match(xml, /<RunLevel>LeastPrivilege<\/RunLevel>/);
  assert.doesNotMatch(xml, /Password|HighestAvailable|S4U/);
  assert.match(xml, /Dev &amp; Co/);
  assert.equal(xmlEscape(`<a href="x">&'`), "&lt;a href=&quot;x&quot;&gt;&amp;&apos;");
});

test("enable: confirm, write xml, create the task, remove the temp file", async () => {
  const { ipc, calls } = make();
  assert.deepEqual(await ipc.register(good, true), { ok: true });
  assert.deepEqual(calls.confirms, [true]);
  assert.equal(calls.schtasks.length, 1);
  assert.deepEqual(calls.schtasks[0], ["/Create", "/TN", TASK_NAME, "/XML", "C:\\Temp\\maya-task.xml", "/F"]);
  assert.equal(calls.written.length, 1);
  assert.deepEqual(calls.removed, ["C:\\Temp\\maya-task.xml"]);
});

test("disable: removes the task when it exists, succeeds when it does not", async () => {
  const { ipc, calls } = make();
  assert.deepEqual(await ipc.register(good, false), { ok: true });
  assert.deepEqual(calls.schtasks.map((a) => a[0]), ["/Query", "/Delete"]);
  const none = make({ schtasks: (a) => (a[0] === "/Query" ? { code: 1, stdout: "", stderr: "" } : { code: 0 }) });
  assert.deepEqual(await none.ipc.register(good, false), { ok: true });
  assert.deepEqual(none.calls.schtasks.map((a) => a[0]), ["/Query"]);
});

test("refuses an untrusted sender, a bad argument, another OS and a declined dialog", async () => {
  const { ipc, calls } = make();
  for (const event of [undefined, {}, { senderFrame: { url: "https://evil.example/" } }, { senderFrame: { url: "file:///x.html" } }]) {
    assert.deepEqual(await ipc.register(event, true), { ok: false, reason: "untrusted-sender" });
  }
  for (const arg of ["yes", 1, null, undefined, {}]) {
    assert.deepEqual(await ipc.register(good, arg), { ok: false, reason: "bad-request" });
  }
  assert.equal(calls.schtasks.length, 0);
  assert.deepEqual(await make({ deps: { platform: "linux" } }).ipc.register(good, true), { ok: false, reason: "unsupported-platform" });
  assert.deepEqual(await make({ deps: { exe: "relative.exe" } }).ipc.register(good, true), { ok: false, reason: "bad-executable" });
  const declined = make({ confirm: false });
  assert.deepEqual(await declined.ipc.register(good, true), { ok: false, reason: "declined" });
  assert.equal(declined.calls.schtasks.length, 0);
});

test("a failing schtasks is reported and the temp file is still removed", async () => {
  const { ipc, calls } = make({ schtasks: () => ({ code: 1, stdout: "", stderr: "denied" }) });
  assert.deepEqual(await ipc.register(good, true), { ok: false, reason: "schtasks-failed" });
  assert.equal(calls.removed.length, 1);
});

function headless(over = {}) {
  const calls = { run: [], stop: 0, log: [] };
  const deps = {
    supervisor: {
      start: async () => over.start ?? { ok: true },
      stop: async () => void calls.stop++,
    },
    run: async (cmd, args, o) => {
      calls.run.push({ cmd, args, o });
      return { code: over.code ?? 0, stdout: '{"idle":true}', stderr: "" };
    },
    config: { python: "py", backendDir: "B", envFile: "E" },
    env: { PATH: "x" },
    log: (l) => calls.log.push(l),
  };
  return { deps, calls };
}

test("headless run: supervisor, run_due, stop (no window involved)", async () => {
  const { deps, calls } = headless();
  assert.deepEqual(await runHeadless(deps), { ok: true, code: 0 });
  assert.deepEqual(calls.run[0].args.slice(0, 3), ["-m", "app.schedules.run_due", "--env-file"]);
  assert.equal(calls.stop, 1);
});

test("headless run: a failed startup never calls run_due and still stops", async () => {
  const { deps, calls } = headless({ start: { ok: false, reason: "docker-unavailable" } });
  assert.deepEqual(await runHeadless(deps), { ok: false, code: 1, reason: "docker-unavailable" });
  assert.equal(calls.run.length, 0);
  assert.equal(calls.stop, 1);
});

test("headless run: cap reached (3) is not a crash; a crash code is", async () => {
  assert.equal((await runHeadless(headless({ code: 3 }).deps)).ok, true);
  assert.equal((await runHeadless(headless({ code: 1 }).deps)).ok, false);
});
