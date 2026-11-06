import { describe, expect, it } from "vitest";

import {
  resolveApiProxyTarget,
  resolveProxyTimeoutMs,
} from "./api-proxy-target";

describe("resolveProxyTimeoutMs", () => {
  it("outlasts the approved 120-second reply deadline by default", () => {
    expect(resolveProxyTimeoutMs()).toBe(150_000);
    expect(resolveProxyTimeoutMs(" ")).toBe(150_000);
  });

  it("follows the API's configured deadline", () => {
    expect(resolveProxyTimeoutMs("60")).toBe(90_000);
    expect(resolveProxyTimeoutMs("900")).toBe(930_000);
  });

  it.each(["0", "901", "-5", "1.5", "two minutes"])(
    "rejects an invalid deadline %s",
    (value) => {
      expect(() => resolveProxyTimeoutMs(value)).toThrow(
        /MAYA_CHAT_REPLY_DEADLINE_SECONDS/,
      );
    },
  );
});

describe("resolveApiProxyTarget", () => {
  it("defaults to the local API origin", () => {
    expect(resolveApiProxyTarget()).toBe("http://127.0.0.1:8000");
    expect(resolveApiProxyTarget("  ")).toBe("http://127.0.0.1:8000");
  });

  it.each([
    ["https://api.example.test", "https://api.example.test"],
    ["https://api.example.test:8443", "https://api.example.test:8443"],
    ["http://localhost:8000", "http://localhost:8000"],
    ["http://localhost:8000/", "http://localhost:8000"],
    ["http://localhost", "http://localhost"],
    ["HTTP://LOCALHOST:3000", "http://localhost:3000"],
    ["http://127.0.0.1:8000", "http://127.0.0.1:8000"],
    ["http://[::1]:8000", "http://[::1]:8000"],
    ["https://127.0.0.1:8443", "https://127.0.0.1:8443"],
  ])("accepts allowed origin %s", (value, expected) => {
    expect(resolveApiProxyTarget(value)).toBe(expected);
  });

  it.each([
    "http://api.example.test",
    "http://api.example.test:8000/",
    "http://10.0.0.2:8000",
    "http://192.168.1.20",
    "http://localhost.evil.example",
    "http://127.0.0.1.evil.example",
    "http://localhost@evil.example",
    "http://evil.example/localhost",
    "http://[::2]:8000",
    "http://0.0.0.0:8000",
    "HTTP://API.EXAMPLE.TEST",
    "http:api.example.test",
    "http://",
    "https://user:password@api.example.test",
    "https://api.example.test/path",
    "https://api.example.test?key=value",
    "https://api.example.test#fragment",
    "not an origin",
  ])("rejects unsafe target %s", (value) => {
    expect(() => resolveApiProxyTarget(value)).toThrow(
      "MAYA_API_PROXY_TARGET must be a valid HTTPS origin or a loopback HTTP origin.",
    );
  });
});
