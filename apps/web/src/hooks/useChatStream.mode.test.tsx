import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ChatEvent, StreamChatMessageParams } from "../lib/api-client";
import { useChatStream, type UseChatStreamOptions } from "./useChatStream";

type Stream = NonNullable<UseChatStreamOptions["stream"]>;

function setup() {
  const events: ChatEvent[] = [
    {
      type: "done",
      assistantMessageId: "a1",
      finishReason: "stop",
      usage: null,
    },
  ];
  const stream = vi.fn<Stream>(async () =>
    (async function* () {
      for (const e of events) yield e;
    })(),
  );
  let n = 0;
  const { result } = renderHook(() =>
    useChatStream({ stream, createKey: () => `key-${(n += 1)}` }),
  );
  return { result, stream };
}

describe("useChatStream mode", () => {
  it("sends mode deep_research and marks the user message", async () => {
    const { result, stream } = setup();
    await act(async () => {
      await result.current.send("compare X and Y", { mode: "deep_research" });
    });
    const params = stream.mock.calls[0]?.[0] as StreamChatMessageParams;
    expect(params.mode).toBe("deep_research");
    expect(result.current.messages[0]?.mode).toBe("deep_research");
  });

  it("omits mode on a normal message", async () => {
    const { result, stream } = setup();
    await act(async () => {
      await result.current.send("hello");
    });
    const params = stream.mock.calls[0]?.[0] as StreamChatMessageParams;
    expect("mode" in params).toBe(false);
    expect(result.current.messages[0]?.mode).toBeUndefined();
  });
});
