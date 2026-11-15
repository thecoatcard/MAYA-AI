import { describe, expect, it } from "vitest";

import type { StoredMessage } from "../lib/api-client";
import { storedScheduleDraftIds } from "./useChatStream";

function msg(metadata: unknown): StoredMessage {
  return { id: "m", role: "assistant", content: "", metadata } as StoredMessage;
}

describe("storedScheduleDraftIds", () => {
  it("reads metadata.schedule_draft.id so the card survives a reload", () => {
    expect(
      storedScheduleDraftIds(msg({ schedule_draft: { id: "d1" } })),
    ).toEqual(["d1"]);
  });
  it("reads schedule_drafts[] in order and drops the duplicate single draft", () => {
    expect(
      storedScheduleDraftIds(
        msg({
          schedule_drafts: [{ id: "d1" }, { id: "d2" }, { id: 3 }],
          schedule_draft: { id: "d2", name: "x", preview: "y" },
        }),
      ),
    ).toEqual(["d1", "d2"]);
  });
  it("ignores anything else", () => {
    expect(storedScheduleDraftIds(msg(null))).toEqual([]);
    expect(storedScheduleDraftIds(msg({ schedule_draft: { id: 3 } }))).toEqual(
      [],
    );
    expect(storedScheduleDraftIds(msg({ schedule_draft: "d1" }))).toEqual([]);
    expect(storedScheduleDraftIds(msg({ schedule_drafts: "x" }))).toEqual([]);
  });
});
