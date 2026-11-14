import assert from "node:assert/strict";
import path from "node:path";
import { test } from "node:test";

import { createFolderIpc, isInsideRoot, isTrustedSender, validateAbsolutePath } from "../src/ipc.mjs";

const ORIGIN = "http://127.0.0.1:5000";
const goodEvent = { senderFrame: { url: `${ORIGIN}/settings/` } };

function make(overrides = {}) {
  const calls = { dialog: [], reveal: [], saved: [] };
  const deps = {
    dialog: {
      showOpenDialog: async (win, options) => {
        calls.dialog.push({ win, options });
        return overrides.dialogResult ?? { canceled: false, filePaths: ["C:\\Work\\Site"] };
      },
    },
    shell: { showItemInFolder: (p) => calls.reveal.push(p) },
    getWindow: () => ("win" in overrides ? overrides.win : { id: 1 }),
    getOrigin: () => ORIGIN,
    exists: () => true,
    loadRoots: () => overrides.roots ?? [],
    saveRoots: (r) => calls.saved.push(r),
    path: path.win32,
  };
  return { ipc: createFolderIpc(deps), calls };
}

test("validateAbsolutePath: only absolute local paths", () => {
  assert.equal(validateAbsolutePath("C:\\Work\\a.txt", path.win32), "C:\\Work\\a.txt");
  assert.equal(validateAbsolutePath("C:\\Work\\..\\Work\\a.txt", path.win32), "C:\\Work\\a.txt");
  for (const bad of [42, null, undefined, {}, "", "relative\\x", "..\\x", "\\\\server\\share\\x", "//host/x", "\\\\?\\C:\\x", "C:\\a\0b", "C:\\" + "a".repeat(1100)]) {
    assert.equal(validateAbsolutePath(bad, path.win32), null, String(bad));
  }
  assert.equal(validateAbsolutePath("/home/u/x", path.posix), "/home/u/x");
  assert.equal(validateAbsolutePath("rel/x", path.posix), null);
});

test("isInsideRoot: inside, equal, siblings and traversal", () => {
  const w = path.win32;
  assert.equal(isInsideRoot("C:\\Work\\Site\\a.txt", "C:\\Work\\Site", w), true);
  assert.equal(isInsideRoot("c:\\work\\site", "C:\\Work\\Site", w), true);
  assert.equal(isInsideRoot("C:\\Work\\Site2\\a.txt", "C:\\Work\\Site", w), false);
  assert.equal(isInsideRoot("C:\\Work\\Other", "C:\\Work\\Site", w), false);
  assert.equal(isInsideRoot("D:\\Work\\Site\\a", "C:\\Work\\Site", w), false);
});

test("isTrustedSender: only the app origin", () => {
  assert.equal(isTrustedSender(`${ORIGIN}/chat/`, ORIGIN), true);
  for (const url of ["http://127.0.0.1:5001/", "file:///app/pages/failed.html", "https://evil.example/", "nope", undefined]) {
    assert.equal(isTrustedSender(url, ORIGIN), false, String(url));
  }
  assert.equal(isTrustedSender(`${ORIGIN}/`, ""), false);
});

test("pickFolder: opens a directory dialog parented to the window and returns the path", async () => {
  const { ipc, calls } = make();
  assert.equal(await ipc.pickFolder(goodEvent), "C:\\Work\\Site");
  assert.deepEqual(calls.dialog[0].options, { properties: ["openDirectory", "createDirectory"] });
  assert.deepEqual(calls.dialog[0].win, { id: 1 });
  assert.deepEqual(calls.saved[0], ["C:\\Work\\Site"]);
});

test("pickFolder: cancel, no window, bad result and untrusted sender give null", async () => {
  assert.equal(await make({ dialogResult: { canceled: true, filePaths: [] } }).ipc.pickFolder(goodEvent), null);
  assert.equal(await make({ win: null }).ipc.pickFolder(goodEvent), null);
  assert.equal(await make({ dialogResult: { canceled: false, filePaths: ["\\\\srv\\share"] } }).ipc.pickFolder(goodEvent), null);
  const { ipc, calls } = make();
  assert.equal(await ipc.pickFolder({ senderFrame: { url: "https://evil.example/" } }), null);
  assert.equal(await ipc.pickFolder({}), null);
  assert.equal(calls.dialog.length, 0);
});

test("revealPath: only inside a folder the person picked", async () => {
  const { ipc, calls } = make({ roots: ["C:\\Work\\Site"] });
  assert.equal(await ipc.revealPath(goodEvent, "C:\\Work\\Site\\index.html"), true);
  assert.equal(await ipc.revealPath(goodEvent, "C:\\Work\\Site"), true);
  assert.deepEqual(calls.reveal, ["C:\\Work\\Site\\index.html", "C:\\Work\\Site"]);
  for (const bad of ["C:\\Windows\\System32\\cmd.exe", "C:\\Work\\Site\\..\\Secret\\x", "\\\\srv\\share\\x", "relative.txt", 7, null]) {
    assert.equal(await ipc.revealPath(goodEvent, bad), false, String(bad));
  }
  assert.equal(calls.reveal.length, 2);
});

test("revealPath: a path picked in this session is allowed; missing files and untrusted senders are not", async () => {
  const { ipc, calls } = make();
  assert.equal(await ipc.revealPath(goodEvent, "C:\\Work\\Site\\a"), false); // nothing picked yet
  await ipc.pickFolder(goodEvent);
  assert.equal(await ipc.revealPath(goodEvent, "C:\\Work\\Site\\a"), true);
  assert.equal(await ipc.revealPath({ senderFrame: { url: "http://127.0.0.1:9/" } }, "C:\\Work\\Site\\a"), false);
  assert.equal(calls.reveal.length, 1);

  const gone = createFolderIpc({
    dialog: {}, shell: { showItemInFolder: () => assert.fail("must not reveal") },
    getWindow: () => null, getOrigin: () => ORIGIN, exists: () => false,
    loadRoots: () => ["C:\\Work\\Site"], saveRoots: () => {}, path: path.win32,
  });
  assert.equal(await gone.revealPath(goodEvent, "C:\\Work\\Site\\gone.txt"), false);
});

test("loaded roots that are not absolute paths are dropped", () => {
  const { ipc } = make({ roots: ["C:\\Ok", "relative", 5, "\\\\srv\\x"] });
  assert.deepEqual(ipc.roots, ["C:\\Ok"]);
});
