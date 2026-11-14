import { act, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { afterEach, describe, expect, it, vi } from "vitest";

import type { ChatProgressStep } from "../../lib/api-client";
import { ProgressSteps } from "./ProgressSteps";

function step(
  id: string,
  label: string,
  o: Partial<ChatProgressStep> = {},
): ChatProgressStep {
  return {
    runId: "r",
    stepId: id,
    parentId: null,
    kind: "tool",
    label,
    detail: null,
    state: "running",
    index: null,
    total: null,
    elapsedMs: null,
    thought: null,
    ...o,
  };
}

afterEach(() => vi.useRealTimers());

describe("ProgressSteps phase header and status line", () => {
  it("titles the section with the current phase and shows one status line", () => {
    render(
      <ProgressSteps
        replyState="live"
        steps={[step("a", "Searching the web")]}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Searching the web" }),
    ).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByTestId("progress-timer")).toHaveTextContent(
      "Searching the web …",
    );
  });

  it("ticks the seconds, announces only the phase, and is removed when finished", () => {
    vi.useFakeTimers();
    const steps = [step("a", "Searching the web")];
    const { rerender } = render(
      <ProgressSteps replyState="live" steps={steps} />,
    );
    act(() => {
      vi.advanceTimersByTime(3000);
    });
    expect(screen.getByTestId("progress-timer")).toHaveTextContent(
      "Searching the web for 3s …",
    );
    expect(screen.getByRole("status")).toHaveTextContent(/^Searching the web$/);
    expect(
      screen.getByTestId("progress-status").querySelector("[aria-hidden]"),
    ).not.toBeNull();
    rerender(
      <ProgressSteps
        replyState="complete"
        steps={[step("a", "Searching the web", { state: "done" })]}
      />,
    );
    expect(screen.queryByTestId("progress-status")).toBeNull();
    expect(screen.queryByTestId("progress-timer")).toBeNull();
  });

  it("has no status line while a step waits for approval", () => {
    render(
      <ProgressSteps
        replyState="live"
        steps={[
          step("a", "Pm agent", {
            state: "skipped",
            detail: "Waiting for your approval.",
          }),
        ]}
      />,
    );
    expect(screen.queryByTestId("progress-status")).toBeNull();
    expect(
      screen.getByRole("button", { name: "Waiting for your approval" }),
    ).toBeInTheDocument();
  });

  it("hides housekeeping and sub-second times behind the section, shows meaningful rows", () => {
    render(
      <ProgressSteps
        replyState="live"
        steps={[
          step("p", "Planning the reply", {
            kind: "plan",
            state: "done",
            detail: "answer",
            elapsedMs: 0,
          }),
          step("w", "Searching the web", {
            state: "done",
            detail: "weather in Delhi",
            elapsedMs: 2400,
          }),
          step("m", "Using Notion", {
            state: "done",
            detail: "runtime-context",
            elapsedMs: 300,
          }),
        ]}
      />,
    );
    expect(screen.queryByText("Planning the reply")).toBeNull();
    expect(screen.getByText("weather in Delhi")).toBeInTheDocument();
    expect(screen.getByText("2.4 s")).toBeInTheDocument();
    expect(screen.queryByText(/\b0 ms\b|300 ms/)).toBeNull();
    expect(screen.queryByText("runtime-context")).toBeNull();
    expect(screen.queryByText("answer")).toBeNull();
    fireEvent.click(screen.getByTestId("progress-background-toggle"));
    expect(screen.getByText("Planning the reply")).toBeInTheDocument();
  });

  it("keeps a failed housekeeping step visible", () => {
    render(
      <ProgressSteps
        replyState="complete"
        steps={[
          step("p", "Planning the reply", {
            kind: "plan",
            state: "failed",
            detail: "Model unavailable",
          }),
        ]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Worked/ }));
    expect(screen.getByText("Planning the reply")).toBeInTheDocument();
    expect(screen.getByText("Model unavailable")).toBeInTheDocument();
    expect(screen.queryByTestId("progress-background-toggle")).toBeNull();
  });

  it("shows the thinking dropdown inside the same list, live in the accent label", () => {
    render(
      <ProgressSteps
        replyState="live"
        steps={[
          step("t", "Thinking", {
            kind: "think",
            thought: "Weighing options.",
          }),
        ]}
      />,
    );
    const list = screen.getByRole("list", { name: "Steps" });
    expect(list).toContainElement(screen.getByTestId("thinking-toggle"));
    expect(
      screen.getByRole("button", { name: "Thinking it through" }),
    ).toBeInTheDocument();
    expect(list.querySelector(".maya-think__label--live")).not.toBeNull();
  });
});

describe("worklog.css rules", () => {
  const css = readFileSync(resolve(__dirname, "worklog.css"), "utf8");

  it("keeps the status line static and rows motion-safe", () => {
    expect(css).not.toMatch(/animation:[^;]*infinite/);
    const reduced = css.slice(css.lastIndexOf("prefers-reduced-motion"));
    expect(reduced).toMatch(/\.maya-steps__item\s*\{[^}]*animation:\s*none/);
    expect(css).toMatch(
      /\.maya-wl__status\s*\{[^}]*border-top:\s*1px solid var\(--maya-border\)/,
    );
  });

  it("uses tokens only, no raw colours", () => {
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b|rgba?\(|hsla?\(/);
  });
});
