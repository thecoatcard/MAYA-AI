import { describe, expect, it } from "vitest";

import type { StoredMessage } from "@/lib/api-client";

import { storedModality } from "./useChatStream";

const base: StoredMessage = {
  id: "m1",
  role: "user",
  content: "hi",
  created_at: "2026-11-05T10:00:00Z",
};

describe("storedModality", () => {
  it("maps the server's audio modality to the spoken marker", () => {
    expect(storedModality({ ...base, modality: "audio" })).toBe("voice");
  });

  it("maps text and treats a missing field (older server) as unknown", () => {
    expect(storedModality({ ...base, modality: "text" })).toBe("text");
    expect(storedModality(base)).toBeUndefined();
  });
});
