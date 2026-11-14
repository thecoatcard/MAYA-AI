import { describe, expect, it } from "vitest";

import { isVoiceServerFrame } from "./voice-protocol";

describe("CHAT_MESSAGE frame", () => {
  it("accepts a stored assistant message", () => {
    expect(
      isVoiceServerFrame({
        type: "CHAT_MESSAGE",
        message_id: "m1",
        role: "assistant",
        content: "```py\nx = 1\n```",
        tool: "show_in_chat",
      }),
    ).toBe(true);
  });

  it("rejects a missing id or empty content", () => {
    expect(
      isVoiceServerFrame({
        type: "CHAT_MESSAGE",
        message_id: "",
        content: "a",
      }),
    ).toBe(false);
    expect(
      isVoiceServerFrame({
        type: "CHAT_MESSAGE",
        message_id: "m",
        content: "  ",
      }),
    ).toBe(false);
    expect(isVoiceServerFrame({ type: "CHAT_MESSAGE", content: "a" })).toBe(
      false,
    );
  });
});
