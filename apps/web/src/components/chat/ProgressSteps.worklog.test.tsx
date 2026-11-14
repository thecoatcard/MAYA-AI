import { fireEvent, render, screen, within } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { resolve } from "node:path";
import { describe, expect, it } from "vitest";

import type { ChatProgressStep, ChatUsage } from "../../lib/api-client";
import { ProgressSteps } from "./ProgressSteps";

function step(
  id: string,
  label: string,
  overrides: Partial<ChatProgressStep> = {},
): ChatProgressStep {
  return {
    runId: "run-1",
    stepId: id,
    parentId: null,
    kind: "search",
    label,
    detail: null,
    state: "done",
    index: null,
    total: null,
    elapsedMs: null,
    thought: null,
    ...overrides,
  };
}

const note = (id: string, text: string) =>
  step(id, "Note", { kind: "note", note: text });

const usage: ChatUsage = {
  prompt_tokens: 1000,
  candidates_tokens: 300,
  total_tokens: 1300,
  cost_usd: null,
  is_estimate: false,
  turns: 3,
  elapsed_ms: 202000,
  cached_tokens: 250,
};

const WEB = "Searching the web";

describe("ProgressSteps work log", () => {
  it("shows narration and grouped rows in the order they happened", () => {
    render(
      <ProgressSteps
        replyState="live"
        steps={[
          note("n1", "Let me look this up."),
          step("1", WEB),
          step("2", WEB),
          step("3", WEB),
          note("n2", "Now I will save it."),
          step("4", "Saving to memory", { kind: "save" }),
        ]}
      />,
    );
    const items = screen
      .getByRole("list", { name: "Steps" })
      .querySelectorAll(":scope > li");
    expect(items).toHaveLength(4);
    expect(items[0]).toHaveTextContent("Let me look this up.");
    expect(items[1]).toHaveTextContent("Searched the web (3)");
    expect(items[2]).toHaveTextContent("Now I will save it.");
    expect(items[3]).toHaveTextContent("Saving to memory");
    // The narration appears once: a note is not also a step row.
    expect(screen.getAllByText("Let me look this up.")).toHaveLength(1);
  });

  it("expands a group to its steps and back", () => {
    render(
      <ProgressSteps
        replyState="live"
        steps={[step("1", WEB), step("2", "Reading a web page")]}
      />,
    );
    const toggle = screen.getByTestId("progress-group-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryAllByTestId("progress-step")).toHaveLength(0);
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByTestId("progress-step")).toHaveLength(2);
    fireEvent.click(toggle);
    expect(screen.queryAllByTestId("progress-step")).toHaveLength(0);
  });

  it("flags a group with a failed step and opens it so the step is reachable", () => {
    render(
      <ProgressSteps
        replyState="complete"
        steps={[step("1", WEB), step("2", WEB, { state: "failed" })]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Worked.*1 failed/ }));
    const group = screen.getByTestId("progress-group");
    expect(group).toHaveAttribute("data-failed", "true");
    expect(within(group).getByText("1 failed")).toBeInTheDocument();
    expect(within(group).getAllByTestId("progress-step")).toHaveLength(2);
    expect(within(group).getByText("Failed")).toBeInTheDocument();
  });

  it("does not wrap a single step in a group", () => {
    render(<ProgressSteps replyState="live" steps={[step("1", WEB)]} />);
    expect(screen.queryByTestId("progress-group")).toBeNull();
    expect(screen.getAllByTestId("progress-step")).toHaveLength(1);
  });

  it("header reads Worked for 3 min 22 s · 2 steps, folds when done, and opens with the chevron button", () => {
    render(
      <ProgressSteps
        replyState="complete"
        usage={usage}
        steps={[note("n", "Looking."), step("1", WEB), step("2", WEB)]}
      />,
    );
    const toggle = screen.getByRole("button", {
      name: "Worked for 3 min 22 s · 2 steps",
    });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("progress-note")).toBeNull();
    fireEvent.click(toggle);
    expect(screen.getByTestId("progress-note")).toHaveTextContent("Looking.");
  });

  it("is open and says Working while live, with no footer", () => {
    render(
      <ProgressSteps
        replyState="live"
        usage={usage}
        steps={[note("n", "Looking.")]}
      />,
    );
    expect(
      screen.getByRole("button", { name: "Working on it" }),
    ).toHaveAttribute("aria-expanded", "true");
    expect(screen.queryByTestId("progress-footer")).toBeNull();
  });

  it("keeps Stopped and Interrupted wording", () => {
    const { rerender } = render(
      <ProgressSteps replyState="stopped" steps={[step("1", WEB)]} />,
    );
    expect(screen.getByRole("button", { name: "Stopped" })).toBeInTheDocument();
    rerender(
      <ProgressSteps replyState="interrupted" steps={[step("1", WEB)]} />,
    );
    expect(
      screen.getByRole("button", { name: "Interrupted" }),
    ).toBeInTheDocument();
  });

  it("footer shows only real figures", () => {
    const { rerender } = render(
      <ProgressSteps
        replyState="complete"
        usage={usage}
        steps={[step("1", WEB), step("2", WEB)]}
      />,
    );
    // The footer sits inside the section now: the folded line carries elapsed and steps.
    fireEvent.click(screen.getByRole("button", { name: /Worked/ }));
    expect(screen.getByTestId("progress-footer")).toHaveTextContent(
      "3 min 22 s · 2 steps · 3 turns · 1.5 tok/s · 25% cached",
    );
    rerender(
      <ProgressSteps
        replyState="complete"
        usage={{ ...usage, turns: null, cached_tokens: 0, is_estimate: true }}
        steps={[step("1", WEB), step("2", WEB)]}
      />,
    );
    const text = screen.getByTestId("progress-footer").textContent ?? "";
    expect(text).toBe("3 min 22 s · 2 steps");
    rerender(
      <ProgressSteps
        replyState="complete"
        steps={[step("1", WEB, { elapsedMs: null })]}
      />,
    );
    expect(screen.getByTestId("progress-footer")).toHaveTextContent("1 step");
    expect(screen.getByTestId("progress-footer").textContent).not.toMatch(
      /turn|tok\/s|cached/,
    );
  });

  it("is operable from the keyboard: both toggles are native buttons that take focus", () => {
    // jsdom does not turn Enter or Space into a click, which a real <button> does in a browser,
    // so this checks what makes that work: native buttons, in tab order, with aria-expanded.
    render(
      <ProgressSteps
        replyState="complete"
        steps={[step("1", WEB), step("2", WEB)]}
      />,
    );
    const head = screen.getByRole("button", { name: /Worked/ });
    expect(head.tagName).toBe("BUTTON");
    expect(head).not.toHaveAttribute("tabindex", "-1");
    head.focus();
    expect(head).toHaveFocus();
    fireEvent.click(head);
    const row = screen.getByTestId("progress-group-toggle");
    expect(row.tagName).toBe("BUTTON");
    expect(row).not.toHaveAttribute("tabindex", "-1");
    row.focus();
    expect(row).toHaveFocus();
    expect(row).toHaveAttribute("aria-controls");
  });

  it("has a static equivalent for every animation under reduced motion", () => {
    const css = readFileSync(resolve(__dirname, "worklog.css"), "utf8");
    const reduced = css.slice(css.indexOf("prefers-reduced-motion"));
    expect(css).not.toMatch(/animation:[^;]*infinite/);
    expect(reduced).toMatch(/\.maya-wl__list,[\s\S]*animation:\s*none/);
    expect(reduced).toMatch(/\.maya-wl__chevron[\s\S]*transition:\s*none/);
    // Only transform and opacity are animated.
    expect(css).not.toMatch(
      /transition:[^;]*\b(height|width|margin|top|left)\b/,
    );
  });
});
