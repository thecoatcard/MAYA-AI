import { act, renderHook } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ChatEvent, StreamChatMessageParams } from "../lib/api-client";
import { useChatStream, type UseChatStreamOptions } from "./useChatStream";

type Stream = NonNullable<UseChatStreamOptions["stream"]>;

const card = {
  ask_id: "ask_1",
  source: "chat",
  state: "awaiting_input",
  questions: [
    {
      question: "Which language?",
      header: "Language",
      options: [{ label: "Java" }, { label: "Go" }],
      multi_select: false,
      allow_text: true,
    },
  ],
};

function setup(events: ChatEvent[]) {
  const stream = vi.fn<Stream>(async () =>
    (async function* () {
      for (const e of events) yield e;
    })(),
  );
  let n = 0;
  const hook = renderHook(() =>
    useChatStream({
      stream,
      createKey: () => `key-${String(++n).padStart(4, "0")}`,
    }),
  );
  return { stream, ...hook };
}

describe("useChatStream and ask_user", () => {
  it("keeps the card of an input.requested event on the reply", async () => {
    const { result } = setup([
      {
        type: "message.accepted",
        sessionId: "s1",
        userMessageId: "u1",
        assistantMessageId: "a1",
        replayed: false,
      },
      {
        type: "input.requested",
        request: {
          askId: "ask_1",
          source: "chat",
          questions: [
            {
              question: "Which language?",
              header: "Language",
              options: [{ label: "Java", description: "" }],
              multiSelect: false,
              allowText: true,
            },
          ],
        },
      },
      {
        type: "done",
        assistantMessageId: "a1",
        finishReason: "stop",
        usage: null,
      },
    ]);
    await act(async () => {
      await result.current.send("write a sorter");
    });
    const reply = result.current.messages[1];
    expect(reply?.inputRequest?.askId).toBe("ask_1");
    expect(reply?.state).toBe("complete");
  });

  it("sends the answer as a structured field with the readable text, and marks the user message", async () => {
    const { result, stream } = setup([
      {
        type: "done",
        assistantMessageId: "a2",
        finishReason: "stop",
        usage: null,
      },
    ]);
    await act(async () => {
      await result.current.send("Language: Java", {
        answer: {
          askId: "ask_1",
          answers: [
            { question: "Which language?", selected: ["Java"], text: "" },
          ],
        },
      });
    });
    const params = stream.mock.calls[0]?.[0] as StreamChatMessageParams;
    expect(params.content).toBe("Language: Java");
    expect(params.answer).toEqual({
      ask_id: "ask_1",
      answers: [{ selected: ["Java"], text: "" }],
    });
    expect(result.current.messages[0]?.inputAnswer?.askId).toBe("ask_1");
  });

  it("replays the card and the answer from stored metadata after a reload", () => {
    const { result } = setup([]);
    act(() => {
      result.current.openSession("s1", [
        {
          id: "u1",
          role: "user",
          content: "write a sorter",
          created_at: "2026-11-13T10:00:00Z",
        },
        {
          id: "a1",
          role: "assistant",
          content: "Which language?",
          created_at: "2026-11-13T10:00:01Z",
          finish_reason: "stop",
          metadata: { input_request: card },
        },
        {
          id: "u2",
          role: "user",
          content: "Language: Java",
          created_at: "2026-11-13T10:00:02Z",
          metadata: {
            answer: {
              ask_id: "ask_1",
              answers: [
                { question: "Which language?", selected: ["Java"], text: "" },
              ],
            },
          },
        },
        {
          id: "bad",
          role: "assistant",
          content: "x",
          created_at: "2026-11-13T10:00:03Z",
          metadata: { input_request: { ask_id: "z" } },
        },
      ]);
    });
    const [, reply, answer, bad] = result.current.messages;
    expect(reply?.inputRequest?.questions[0]?.options[0]?.label).toBe("Java");
    expect(answer?.inputAnswer?.answers[0]?.selected).toEqual(["Java"]);
    expect(bad?.inputRequest).toBeUndefined();
  });
});
