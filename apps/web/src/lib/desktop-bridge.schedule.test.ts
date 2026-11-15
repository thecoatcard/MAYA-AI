import { afterEach, describe, expect, it, vi } from "vitest";

import {
  canRegisterScheduleWake,
  registerScheduleWake,
} from "./desktop-bridge";

function setMaya(value: unknown): void {
  (window as unknown as { maya?: unknown }).maya = value;
}

describe("registerScheduleWake bridge", () => {
  afterEach(() => setMaya(undefined));

  it("is unavailable in a browser and never throws", async () => {
    setMaya(undefined);
    expect(canRegisterScheduleWake()).toBe(false);
    expect(await registerScheduleWake(true)).toEqual({
      ok: false,
      reason: "not-desktop",
    });
  });

  it("passes the flag to the preload API and returns ok", async () => {
    const fn = vi.fn(async () => ({ ok: true }));
    setMaya({ isDesktop: true, registerScheduleWake: fn });
    expect(canRegisterScheduleWake()).toBe(true);
    expect(await registerScheduleWake(false)).toEqual({ ok: true });
    expect(fn).toHaveBeenCalledWith(false);
  });

  it("turns a refusal or a throw into { ok: false, reason }", async () => {
    setMaya({
      isDesktop: true,
      registerScheduleWake: async () => ({ ok: false, reason: "no-docker" }),
    });
    expect(await registerScheduleWake(true)).toEqual({
      ok: false,
      reason: "no-docker",
    });
    setMaya({
      isDesktop: true,
      registerScheduleWake: async () => {
        throw new Error("boom");
      },
    });
    expect(await registerScheduleWake(true)).toEqual({
      ok: false,
      reason: "failed",
    });
  });
});
