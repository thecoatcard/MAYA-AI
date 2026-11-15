import { describe, expect, it } from "vitest";

import { pickFemaleVoice, scoreVoice } from "./tts-voice";

const v = (name: string, lang = "en-US") => ({ name, lang });

describe("pickFemaleVoice", () => {
  it("prefers a natural female voice over a plain one", () => {
    const voices = [
      v("Microsoft David - English (United States)"),
      v("Microsoft Zira - English (United States)"),
      v("Microsoft Aria Online (Natural) - English (United States)"),
    ];
    expect(pickFemaleVoice(voices)?.name).toContain("Aria");
  });

  it("never picks a male voice, even when it is the only English voice", () => {
    const voices = [
      v("Microsoft David - English (United States)"),
      v("Microsoft Mark - English (United States)"),
      v("Alex"),
    ];
    expect(pickFemaleVoice(voices)).toBeNull();
  });

  it("knows the macOS and Chrome female voices", () => {
    expect(pickFemaleVoice([v("Alex"), v("Samantha")])?.name).toBe("Samantha");
    expect(
      pickFemaleVoice([v("Google UK English Female", "en-GB")])?.name,
    ).toBe("Google UK English Female");
  });

  it("returns null for an empty list", () => {
    expect(pickFemaleVoice([])).toBeNull();
  });

  it("scores non-English female voices below English ones", () => {
    expect(scoreVoice(v("Microsoft Zira", "en-US"))).toBeGreaterThan(
      scoreVoice(v("Microsoft Heera", "hi-IN")),
    );
  });
});
