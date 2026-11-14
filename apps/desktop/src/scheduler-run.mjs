// "Wake my PC to run schedules" (EXPERIMENTAL, ADR-0033): the headless one-shot run and the one
// Windows Scheduled Task that starts it. Pure Node: Electron, schtasks, the supervisor and the
// file system are injected, so everything is unit-testable and schtasks is never executed in tests.
import path from "node:path";

import { isTrustedSender } from "./ipc.mjs";

export const SCHEDULE_WAKE_CHANNEL = "maya:register-schedule-wake";
export const RUN_SCHEDULES_FLAG = "--run-schedules";
export const TASK_NAME = "MAYA AI Scheduler";
export const REPEAT_MINUTES = 30;
export const RUN_DUE_CAP_MINUTES = 25;
const SCHTASKS_TIMEOUT_MS = 30_000;

/** @param {readonly string[]} argv */
export function wantsHeadlessRun(argv) {
  return argv.includes(RUN_SCHEDULES_FLAG);
}

/** @param {string} value */
export function xmlEscape(value) {
  return value
    .replace(/&/g, "&amp;")
    .replace(/</g, "&lt;")
    .replace(/>/g, "&gt;")
    .replace(/"/g, "&quot;")
    .replace(/'/g, "&apos;");
}

/**
 * Accept only an absolute local Windows path to an .exe, bounded, no NUL, no UNC.
 * @param {unknown} value
 */
export function validateExePath(value, p = path.win32) {
  if (typeof value !== "string" || value.length === 0 || value.length > 1024) return null;
  if (value.includes("\0") || /^[\\/]{2}/.test(value) || !p.isAbsolute(value)) return null;
  return /\.exe$/i.test(value) ? p.normalize(value) : null;
}

/**
 * The task definition: user-level, only while the user is logged on, no stored password, no admin
 * rights. Two triggers (logon, then every 30 minutes) on ONE task. `args` is the full argument line.
 * @param {{ exe: string, args: string, user?: string }} o
 */
export function buildTaskXml({ exe, args, user }) {
  const principal = user
    ? `<UserId>${xmlEscape(user)}</UserId>`
    : "";
  return `<?xml version="1.0" encoding="UTF-16"?>
<Task version="1.2" xmlns="http://schemas.microsoft.com/windows/2004/02/mit/task">
  <RegistrationInfo><Description>MAYA AI: runs due scheduled reports while you are logged on (experimental). Remove it in MAYA Settings, Schedules.</Description></RegistrationInfo>
  <Triggers>
    <LogonTrigger><Enabled>true</Enabled>${principal}</LogonTrigger>
    <TimeTrigger>
      <Repetition><Interval>PT${REPEAT_MINUTES}M</Interval><StopAtDurationEnd>false</StopAtDurationEnd></Repetition>
      <StartBoundary>2026-01-01T00:00:00</StartBoundary>
      <Enabled>true</Enabled>
    </TimeTrigger>
  </Triggers>
  <Principals>
    <Principal id="Author"><LogonType>InteractiveToken</LogonType><RunLevel>LeastPrivilege</RunLevel></Principal>
  </Principals>
  <Settings>
    <MultipleInstancesPolicy>IgnoreNew</MultipleInstancesPolicy>
    <DisallowStartIfOnBatteries>false</DisallowStartIfOnBatteries>
    <StopIfGoingOnBatteries>false</StopIfGoingOnBatteries>
    <StartWhenAvailable>true</StartWhenAvailable>
    <ExecutionTimeLimit>PT40M</ExecutionTimeLimit>
    <Enabled>true</Enabled>
  </Settings>
  <Actions Context="Author">
    <Exec><Command>${xmlEscape(exe)}</Command><Arguments>${xmlEscape(args)}</Arguments></Exec>
  </Actions>
</Task>
`;
}

/** @param {string} xmlFile */
export const createCommand = (xmlFile) => ["/Create", "/TN", TASK_NAME, "/XML", xmlFile, "/F"];
export const deleteCommand = () => ["/Delete", "/TN", TASK_NAME, "/F"];
export const queryCommand = () => ["/Query", "/TN", TASK_NAME];

/**
 * IPC: create or remove the ONE scheduled task. Only the app page on the API origin may call it,
 * only with a boolean, and only after the person confirmed in a native dialog.
 * @param {{
 *   getOrigin: () => string,
 *   platform: string,
 *   exe: string,
 *   args: string,
 *   tempFile: () => string,
 *   writeTask: (file: string, xml: string) => Promise<void>,
 *   removeFile: (file: string) => Promise<void>,
 *   runSchtasks: (args: string[], timeoutMs: number) => Promise<{ code: number, stdout: string, stderr: string }>,
 *   confirm: (enabled: boolean) => Promise<boolean>,
 * }} deps
 */
export function createScheduleWakeIpc(deps) {
  return {
    /** @returns {Promise<{ ok: boolean, reason?: string }>} */
    async register(event, enabled) {
      if (!isTrustedSender(event?.senderFrame?.url, deps.getOrigin())) {
        return { ok: false, reason: "untrusted-sender" };
      }
      if (typeof enabled !== "boolean") return { ok: false, reason: "bad-request" };
      if (deps.platform !== "win32") return { ok: false, reason: "unsupported-platform" };
      const exe = validateExePath(deps.exe);
      if (exe === null) return { ok: false, reason: "bad-executable" };
      if (!(await deps.confirm(enabled))) return { ok: false, reason: "declined" };
      if (!enabled) {
        const query = await deps.runSchtasks(queryCommand(), SCHTASKS_TIMEOUT_MS);
        if (query.code !== 0) return { ok: true }; // nothing to remove
        const del = await deps.runSchtasks(deleteCommand(), SCHTASKS_TIMEOUT_MS);
        return del.code === 0 ? { ok: true } : { ok: false, reason: "schtasks-failed" };
      }
      const file = deps.tempFile();
      try {
        await deps.writeTask(file, buildTaskXml({ exe, args: deps.args }));
        const created = await deps.runSchtasks(createCommand(file), SCHTASKS_TIMEOUT_MS);
        return created.code === 0 ? { ok: true } : { ok: false, reason: "schtasks-failed" };
      } finally {
        await deps.removeFile(file).catch(() => {});
      }
    },
  };
}

/**
 * The headless one-shot: start the supervisor (Docker check, database and cache, schema check,
 * the API with its scheduler), wait for `python -m app.schedules.run_due`, stop. No window.
 * @param {{
 *   supervisor: { start: () => Promise<{ ok: boolean, reason?: string }>, stop: () => Promise<void> },
 *   run: (cmd: string, args: string[], o: { cwd?: string, env?: Record<string,string>, timeoutMs: number }) => Promise<{ code: number, stdout: string, stderr: string }>,
 *   config: { python: string, backendDir: string, envFile: string },
 *   env: Record<string, string>,
 *   log: (line: string) => void,
 * }} deps
 * @returns {Promise<{ ok: boolean, code: number, reason?: string }>}
 */
export async function runHeadless(deps) {
  let started = false;
  try {
    const result = await deps.supervisor.start();
    started = true;
    if (!result.ok) {
      deps.log(`headless: startup failed (${result.reason})\n`);
      return { ok: false, code: 1, reason: result.reason };
    }
    const done = await deps.run(
      deps.config.python,
      ["-m", "app.schedules.run_due", "--env-file", deps.config.envFile, "--max-minutes", String(RUN_DUE_CAP_MINUTES)],
      { cwd: deps.config.backendDir, env: deps.env, timeoutMs: (RUN_DUE_CAP_MINUTES + 3) * 60_000 },
    );
    deps.log(`headless: run_due exit=${done.code} ${done.stdout.trim().slice(0, 300)}\n`);
    // 0 = idle, 2 = scheduler off, 3 = cap reached with work left: none of these is a crash.
    return { ok: [0, 2, 3].includes(done.code), code: done.code };
  } finally {
    if (started) await deps.supervisor.stop().catch(() => {});
  }
}
