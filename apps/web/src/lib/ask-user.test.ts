import { describe, expect, it } from "vitest";

import { decodeChatEvent } from "./api-client";
import {
  answerReadableText,
  needsDetailQuestion,
  readInputAnswer,
  readInputRequest,
} from "./ask-user";

const wire = {
  ask_id: "ask_1",
  source: "chat",
  state: "awaiting_input",
  questions: [
    {
      question: "Language?",
      header: "Language header too long",
      options: [
        { label: "Java", description: "typed" },
        { label: "" },
        { label: "Go" },
      ],
      multi_select: true,
      allow_text: false,
    },
    { question: "Folder?", options: [] },
    { question: "" },
  ],
};

describe("ask_user wire readers", () => {
  it("reads a card defensively", () => {
    const card = readInputRequest(wire);
    expect(card?.askId).toBe("ask_1");
    expect(card?.questions).toHaveLength(2);
    expect(card?.questions[0]?.header).toHaveLength(12);
    expect(card?.questions[0]?.options.map((o) => o.label)).toEqual([
      "Java",
      "Go",
    ]);
    expect(card?.questions[0]?.allowText).toBe(false);
    expect(card?.questions[1]?.allowText).toBe(true); // no options: text is forced on
    expect(readInputRequest({ ask_id: "", questions: [] })).toBeNull();
    expect(readInputRequest("x")).toBeNull();
  });

  it("decodes the input.requested SSE event and ignores a malformed one", () => {
    const event = decodeChatEvent({
      event: "input.requested",
      data: JSON.stringify(wire),
      id: null,
      retry: null,
    });
    expect(event?.type).toBe("input.requested");
    expect(
      decodeChatEvent({
        event: "input.requested",
        data: '{"ask_id":"a"}',
        id: null,
        retry: null,
      }),
    ).toBeNull();
  });

  it("reads a stored answer and renders it as readable text", () => {
    const answer = readInputAnswer("ask_1", [
      { question: "Language?", selected: ["Java"], text: "" },
      { question: "Folder?", selected: [], text: "reports" },
    ]);
    expect(answer?.answers).toHaveLength(2);
    const card = readInputRequest(wire);
    expect(
      answerReadableText(card?.questions ?? [], answer?.answers ?? []),
    ).toBe("Language hea: Java; Folder?: reports");
    expect(readInputAnswer(1, [])).toBeNull();
  });

  it("recognises a task step that needs a detail", () => {
    expect(
      needsDetailQuestion("I need a little more detail: What is the topic?"),
    ).toBe("What is the topic?");
    expect(needsDetailQuestion("boom")).toBeNull();
    expect(needsDetailQuestion(null)).toBeNull();
  });

  it("decodes the schedule.draft SSE event", () => {
    expect(
      decodeChatEvent({
        event: "schedule.draft",
        data: JSON.stringify({
          draft_id: "d1",
          name: "AI news",
          preview: "Daily at 8",
          schedule: {},
          expires_at: null,
        }),
        id: null,
        retry: null,
      }),
    ).toEqual({ type: "schedule.draft", draftId: "d1" });
    expect(
      decodeChatEvent({
        event: "schedule.draft",
        data: JSON.stringify({ schedule_draft: { id: "" } }),
        id: null,
        retry: null,
      }),
    ).toBeNull();
  });
});
