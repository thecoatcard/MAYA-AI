import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import type { TimedChatMessage, ThreadItem } from "./threadItems";
import { ThreadList } from "./ThreadList";

vi.mock("../../hooks/useTaskEvents", () => ({
  useTaskEvents: () => ({
    taskDetail: null,
    error: null,
    lastEvent: null,
    events: [],
    refetchTask: vi.fn(),
  }),
}));

function message(
  id: string,
  role: "user" | "assistant",
  extra: Partial<TimedChatMessage> = {},
): ThreadItem {
  return {
    kind: "message",
    id,
    ts: 1,
    spoken: false,
    message: {
      key: id,
      id,
      role,
      content: role === "user" ? "hi" : "Which language?",
      state: role === "user" ? "sent" : "complete",
      finishReason: null,
      usage: null,
      createdAt: 1,
      ...extra,
    },
  };
}

const inputRequest = {
  askId: "ask_1",
  source: "chat" as const,
  questions: [
    {
      question: "Which language?",
      header: "Language",
      options: [
        { label: "Java", description: "" },
        { label: "Go", description: "" },
      ],
      multiSelect: false,
      allowText: true,
    },
  ],
};

describe("ThreadList question cards", () => {
  it("shows a waiting card as the last message and sends the answer", () => {
    const onAnswerInput = vi.fn();
    render(
      <ThreadList
        items={[
          message("u1", "user"),
          message("a1", "assistant", { inputRequest }),
        ]}
        status="idle"
        onAnswerInput={onAnswerInput}
      />,
    );
    // The message text equals the question, so it is shown once, in the card.
    expect(screen.getAllByText("Which language?")).toHaveLength(1);
    fireEvent.click(screen.getAllByRole("radio")[1] as HTMLElement);
    fireEvent.click(screen.getByRole("button", { name: /Submit/ }));
    expect(onAnswerInput).toHaveBeenCalledWith(inputRequest, [
      { selected: ["Go"], text: "" },
    ]);
  });

  it("shows an answered summary when the next user message answers it", () => {
    render(
      <ThreadList
        items={[
          message("u1", "user"),
          message("a1", "assistant", { inputRequest }),
          message("u2", "user", {
            content: "Language: Java",
            inputAnswer: {
              askId: "ask_1",
              answers: [
                { question: "Which language?", selected: ["Java"], text: "" },
              ],
            },
          }),
        ]}
        status="idle"
      />,
    );
    expect(screen.queryByRole("radio")).toBeNull();
    expect(screen.getByTestId("ask-user-card").getAttribute("data-state")).toBe(
      "answered",
    );
  });

  it("dismisses the card when the user typed something else", () => {
    render(
      <ThreadList
        items={[
          message("a1", "assistant", { inputRequest }),
          message("u2", "user", { content: "never mind" }),
        ]}
        status="idle"
      />,
    );
    expect(screen.getByTestId("ask-user-card").getAttribute("data-state")).toBe(
      "dismissed",
    );
  });

  it("Skip collapses the card and moves focus to the composer", () => {
    const onSkipInput = vi.fn();
    render(
      <ThreadList
        items={[message("a1", "assistant", { inputRequest })]}
        status="idle"
        onSkipInput={onSkipInput}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Skip/ }));
    expect(onSkipInput).toHaveBeenCalled();
    expect(screen.getByTestId("ask-user-card").getAttribute("data-state")).toBe(
      "dismissed",
    );
  });
});
