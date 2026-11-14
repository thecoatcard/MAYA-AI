import assert from "node:assert/strict";
import { test } from "node:test";

import {
  classifyNavigation, classifyWindowOpen, isPermissionAllowed, redact,
} from "../src/policy.mjs";

const ctx = { origin: "http://127.0.0.1:5000", pagesBase: "file:///app/src/pages/" };

test("navigation: only the API origin and local pages are allowed", () => {
  assert.equal(classifyNavigation("http://127.0.0.1:5000/chat/", ctx).kind, "allow");
  assert.equal(classifyNavigation("file:///app/src/pages/failed.html", ctx).kind, "allow");
  for (const url of [
    "http://127.0.0.1:5001/", "http://localhost:5000/", "https://127.0.0.1:5000/",
    "https://evil.example/", "file:///C:/Windows/win.ini", "javascript:alert(1)",
    "http://127.0.0.1:5000@evil.example/", "not a url", "",
  ]) {
    assert.equal(classifyNavigation(url, ctx).kind, "deny", url);
  }
});

test("navigation: only known in-page actions are accepted", () => {
  assert.deepEqual(classifyNavigation("maya-action:retry", ctx), { kind: "action", action: "retry" });
  assert.deepEqual(classifyNavigation("maya-action:open-logs", ctx), { kind: "action", action: "open-logs" });
  assert.equal(classifyNavigation("maya-action:format-disk", ctx).kind, "deny");
});

test("window.open: https goes to the system browser, everything else is dropped", () => {
  assert.deepEqual(classifyWindowOpen("https://example.com/a?b=1"), {
    action: "deny", openExternal: "https://example.com/a?b=1",
  });
  for (const url of ["http://example.com", "file:///x", "javascript:1", "mailto:a@b.c", "https://u:p@example.com", "nope"]) {
    assert.deepEqual(classifyWindowOpen(url), { action: "deny" }, url);
  }
});

test("permissions: microphone for the app origin only", () => {
  const o = { origin: "http://127.0.0.1:5000" };
  assert.equal(isPermissionAllowed("media", "http://127.0.0.1:5000/chat/", o, { mediaTypes: ["audio"] }), true);
  assert.equal(isPermissionAllowed("media", "http://127.0.0.1:5000/", o), true);
  assert.equal(isPermissionAllowed("media", "http://127.0.0.1:5000/", o, { mediaTypes: ["audio", "video"] }), false);
  assert.equal(isPermissionAllowed("media", "http://127.0.0.1:5000/", o, { mediaType: "video" }), false);
  assert.equal(isPermissionAllowed("media", "https://evil.example/", o), false);
  assert.equal(isPermissionAllowed("media", "http://127.0.0.1:6000/", o), false);
  for (const p of ["geolocation", "notifications", "clipboard-read", "midi", "openExternal", "fullscreen", "display-capture"]) {
    assert.equal(isPermissionAllowed(p, "http://127.0.0.1:5000/", o), false, p);
  }
});

test("redact: home directory and secret-looking values", () => {
  const home = "C:\\Users\\Someone";
  assert.equal(redact("C:\\Users\\Someone\\AppData\\logs\\api.log", home), "~\\AppData\\logs\\api.log");
  assert.equal(redact("C:/Users/Someone/x", home), "~/x");
  assert.equal(redact("GET /ws?token=abc123&x=1 password=hunter2 api_key=zzz", home), "GET /ws?token=[redacted]&x=1 password=[redacted] api_key=[redacted]");
});
