import assert from "node:assert/strict";
import path from "node:path";
import { test } from "node:test";

import { createOpenFileIpc, sanitizeFileName } from "../src/open-file.mjs";

const ORIGIN = "http://127.0.0.1:5000";
const goodEvent = { senderFrame: { url: `${ORIGIN}/artifacts/` } };
const ID = "123e4567-e89b-42d3-a456-426614174000";
const TEMP = "C:\\Temp\\maya-open";

function make(overrides = {}) {
  const calls = { fetch: [], save: [], mkdir: [], open: [] };
  const ipc = createOpenFileIpc({
    getOrigin: () => ORIGIN,
    tempRoot: TEMP,
    path: path.win32,
    mkdir: (d) => calls.mkdir.push(d),
    fetch: async (url, init) => {
      calls.fetch.push({ url, init });
      return overrides.response ?? { ok: true, status: 200, body: "BODY" };
    },
    save: async (dest, body) => {
      if (overrides.saveFails) throw new Error("disk full");
      calls.save.push({ dest, body });
    },
    openPath: async (p) => {
      calls.open.push(p);
      return overrides.openResult ?? "";
    },
  });
  return { ipc, calls };
}

const req = (over = {}) => ({ artifactId: ID, fileName: "report.pdf", token: "tok", ...over });

test("sanitizeFileName: plain names pass, anything path-like or executable fails", () => {
  for (const ok of ["report.pdf", "site.zip", "Q3 plan.xlsx", "a.b.docx", "notes.txt"]) {
    assert.equal(sanitizeFileName(ok), ok, ok);
  }
  const bad = [
    "", "   ", ".hidden", "noext", "a/b.pdf", "a\\b.pdf", "C:\\x.pdf", "..\\x.pdf", "x.pdf.",
    "a\0b.pdf", "con.txt", "NUL", "x<y>.pdf", "a".repeat(250) + ".pdf", 7, null, undefined, {},
    "setup.exe", "run.BAT", "a.cmd", "a.ps1", "a.msi", "a.lnk", "a.scr", "a.js", "a.vbs", "a.hta",
    "report.pdf.exe", "x.exe.pdf", "a.JS",
  ];
  for (const name of bad) assert.equal(sanitizeFileName(name), null, String(name));
});

test("sanitizeFileName: surrounding spaces are trimmed", () => {
  assert.equal(sanitizeFileName("  x.pdf "), "x.pdf");
});

test("openArtifact: downloads with the bearer token into the temp folder, then opens that file", async () => {
  const { ipc, calls } = make();
  assert.deepEqual(await ipc.openArtifact(goodEvent, req()), { ok: true });
  assert.equal(calls.fetch[0].url, `${ORIGIN}/api/v1/artifacts/${ID}`);
  assert.deepEqual(calls.fetch[0].init.headers, { Authorization: "Bearer tok" });
  const dest = `${TEMP}\\${ID}\\report.pdf`;
  assert.equal(calls.save[0].dest, dest);
  assert.deepEqual(calls.open, [dest]);
  assert.deepEqual(calls.mkdir, [`${TEMP}\\${ID}`]);
});

test("openArtifact: untrusted senders get nothing and trigger no network or open", async () => {
  for (const event of [{ senderFrame: { url: "https://evil.example/" } }, { senderFrame: { url: "file:///x/pages/failed.html" } }, {}, undefined]) {
    const { ipc, calls } = make();
    assert.deepEqual(await ipc.openArtifact(event, req()), { ok: false, reason: "untrusted" });
    assert.equal(calls.fetch.length + calls.open.length, 0);
  }
});

test("openArtifact: paths, non-uuid ids, missing tokens and junk are rejected before any download", async () => {
  const cases = [
    req({ artifactId: "../../Windows" }),
    req({ artifactId: "C:\\Windows\\notepad.exe" }),
    req({ artifactId: `${ID}/../x` }),
    req({ artifactId: 5 }),
    req({ token: "" }),
    req({ token: undefined }),
    req({ token: "t".repeat(5000) }),
    req({ fileName: 5 }),
    null,
    "C:\\Windows\\notepad.exe",
    undefined,
  ];
  for (const bad of cases) {
    const { ipc, calls } = make();
    const result = await ipc.openArtifact(goodEvent, bad);
    assert.equal(result.ok, false, JSON.stringify(bad));
    assert.equal(calls.fetch.length + calls.open.length, 0, JSON.stringify(bad));
  }
});

test("openArtifact: absolute, UNC and traversal file names and executable types are refused", async () => {
  for (const fileName of ["C:\\Windows\\System32\\calc.exe", "\\\\srv\\share\\a.pdf", "..\\..\\a.pdf", "a.exe", "a.bat", "a.cmd", "a.ps1", "a.msi", "a.lnk", "a.scr", "a.js", "a.vbs", "a.hta", "a.pdf.exe"]) {
    const { ipc, calls } = make();
    assert.deepEqual(await ipc.openArtifact(goodEvent, req({ fileName })), { ok: false, reason: "blocked-type" }, fileName);
    assert.equal(calls.fetch.length + calls.open.length, 0, fileName);
  }
});

test("openArtifact: a failed download, failed write or failed open is reported, and nothing is opened after a failed download", async () => {
  let r = make({ response: { ok: false, status: 404, body: null } });
  assert.deepEqual(await r.ipc.openArtifact(goodEvent, req()), { ok: false, reason: "download-failed" });
  assert.equal(r.calls.open.length, 0);
  r = make({ saveFails: true });
  assert.deepEqual(await r.ipc.openArtifact(goodEvent, req()), { ok: false, reason: "download-failed" });
  assert.equal(r.calls.open.length, 0);
  r = make({ openResult: "No application is associated" });
  assert.deepEqual(await r.ipc.openArtifact(goodEvent, req()), { ok: false, reason: "open-failed" });
});
