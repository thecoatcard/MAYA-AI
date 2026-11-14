import { describe, expect, it } from "vitest";

import { describeVoiceUnavailable } from "./voice-reasons";

describe("describeVoiceUnavailable", () => {
  it("explains a connection failure in plain words and keeps the code", () => {
    const text = describeVoiceUnavailable("connect_failed");
    expect(text).toContain("could not reach the voice service");
    expect(text).toContain("internet connection");
    expect(text).toContain("connect_failed");
    expect(text).toContain("Text chat still works.");
  });

  it("covers quota, a missing key and a disabled server", () => {
    expect(describeVoiceUnavailable("quota")).toContain("over its limit");
    expect(describeVoiceUnavailable("no_key")).toContain("no Gemini key");
    expect(describeVoiceUnavailable("disabled")).toContain("turned off");
  });

  it("falls back to the raw code for an unknown reason, and to no code for none", () => {
    expect(describeVoiceUnavailable("no_api_key")).toBe(
      "Voice is unavailable (no_api_key). Text chat still works.",
    );
    expect(describeVoiceUnavailable(null)).toBe(
      "Voice is unavailable. Text chat still works.",
    );
  });
});
