import { fireEvent, render, screen, within } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import type { ChatProgressStep } from "../../lib/api-client";
import { ProgressSteps } from "./ProgressSteps";

function step(
  id: string,
  overrides: Partial<ChatProgressStep> = {},
): ChatProgressStep {
  return {
    runId: "run-1",
    stepId: id,
    parentId: null,
    kind: "step",
    label: `Label ${id}`,
    detail: null,
    state: "running",
    index: null,
    total: null,
    elapsedMs: null,
    thought: null,
    ...overrides,
  };
}

const LONG_THOUGHT = `${"Weighing the options carefully. ".repeat(8)}END`;

// Header wording changed on purpose (phase header): a finished reply reads "Worked for 12 s · 2
// steps" (plus "· 1 failed" when a step failed), no longer "Completed in 12 s". While live the
// header is the current phase in plain words and one status line below the list ticks the seconds.
describe("ProgressSteps", () => {
  it("renders nothing without steps", () => {
    const { container } = render(
      <ProgressSteps steps={[]} replyState="live" />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("nests children under their parent", () => {
    const { container } = render(
      <ProgressSteps
        replyState="live"
        steps={[
          step("root", { label: "Build the page" }),
          step("child", { label: "Write the header", parentId: "root" }),
        ]}
      />,
    );
    expect(screen.getAllByTestId("progress-step")).toHaveLength(2);
    const nested = container.querySelector(".maya-steps__children");
    expect(nested).not.toBeNull();
    expect(
      within(nested as HTMLElement).getByText("Write the header"),
    ).toBeInTheDocument();
  });

  it("shows a spinner with reduced-motion handling and the Working label for a running step", () => {
    const { container } = render(
      <ProgressSteps replyState="live" steps={[step("a")]} />,
    );
    // The running row announces "Working" for assistive tech only; the visible header is the
    // phase ("Working on it" for an unknown step kind) and the status line says it with seconds.
    expect(screen.getAllByText("Working")).toHaveLength(1);
    expect(
      screen.getByRole("button", { name: "Working on it" }),
    ).toBeInTheDocument();
    const spinner = container.querySelector(".maya-steps__icon--running");
    expect(spinner?.getAttribute("class")).toContain("animate-spin");
    expect(spinner?.getAttribute("class")).toContain(
      "motion-reduce:animate-none",
    );
  });

  it("shows index of total and elapsed time when present", () => {
    render(
      <ProgressSteps
        replyState="live"
        steps={[
          step("a", { state: "done", index: 4, total: 10, elapsedMs: 2300 }),
          step("b", { state: "done" }),
        ]}
      />,
    );
    const [first, second] = screen.getAllByTestId("progress-step");
    expect(within(first).getByText("4 of 10")).toBeInTheDocument();
    expect(within(first).getByText("2.3 s")).toBeInTheDocument();
    expect(within(second).queryByText(/ of /)).toBeNull();
  });

  it("shows a failed label with its detail, and a skipped label", () => {
    render(
      <ProgressSteps
        replyState="complete"
        steps={[
          step("a", { state: "failed", detail: "Email server refused" }),
          step("b", { state: "skipped", label: "Send the reminder" }),
        ]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Worked/ }));
    expect(screen.getByText("Failed")).toBeInTheDocument();
    expect(screen.getByText("Email server refused")).toBeInTheDocument();
    expect(screen.getByText("Skipped")).toBeInTheDocument();
  });

  it("calls a step paused for approval Waiting for approval, not Skipped", () => {
    render(
      <ProgressSteps
        replyState="complete"
        steps={[
          step("a", {
            state: "skipped",
            label: "Pm agent",
            detail: "Waiting for your approval.",
          }),
        ]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Worked/ }));
    expect(screen.getByText("Waiting for approval")).toBeInTheDocument();
    expect(screen.queryByText("Skipped")).toBeNull();
  });

  // The thinking summary is a dropdown now (it replaced the truncated "Show more" preview): it is
  // open and shows ALL of the text while the model thinks, and folds shut once the step is done.
  it("shows the whole thinking summary, open, while the model is thinking", () => {
    render(
      <ProgressSteps
        replyState="live"
        steps={[step("t", { kind: "think", thought: LONG_THOUGHT })]}
      />,
    );
    const toggle = screen.getByTestId("thinking-toggle");
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(toggle.textContent).toContain("Thinking");
    expect(screen.getByTestId("thinking-body")).toHaveAttribute(
      "aria-label",
      "Reasoning summary",
    );
    expect(screen.getByText(/END$/)).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
  });

  it("grows the thinking text as more of it arrives", () => {
    const { rerender } = render(
      <ProgressSteps
        replyState="live"
        steps={[step("t", { kind: "think", thought: "First idea." })]}
      />,
    );
    expect(screen.getByText("First idea.")).toBeInTheDocument();
    rerender(
      <ProgressSteps
        replyState="live"
        steps={[
          step("t", { kind: "think", thought: "First idea. Then a second." }),
        ]}
      />,
    );
    expect(screen.getByText("First idea. Then a second.")).toBeInTheDocument();
    expect(screen.getByTestId("thinking-toggle")).toHaveAttribute(
      "aria-expanded",
      "true",
    );
  });

  it("folds the thinking shut when the step is done and reopens on click", () => {
    render(
      <ProgressSteps
        replyState="live"
        steps={[
          step("t", {
            kind: "think",
            state: "done",
            elapsedMs: 4200,
            thought: "Compared two plans.",
          }),
        ]}
      />,
    );
    const toggle = screen.getByTestId("thinking-toggle");
    expect(toggle.textContent).toContain("Thought for 4.2 s");
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryByTestId("thinking-body")).toBeNull();
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getByText("Compared two plans.")).toBeInTheDocument();
    fireEvent.click(toggle);
    expect(screen.queryByTestId("thinking-body")).toBeNull();
  });

  it("says plainly that this is a summary of the reasoning", () => {
    render(
      <ProgressSteps
        replyState="live"
        steps={[step("t", { kind: "think", thought: "Weighing options." })]}
      />,
    );
    expect(
      screen.getByText(/Summary of the model's reasoning, as it arrives/),
    ).toBeInTheDocument();
  });

  it("has no dropdown to open while a thinking step has no text yet", () => {
    render(
      <ProgressSteps
        replyState="live"
        steps={[step("t", { kind: "think", thought: null })]}
      />,
    );
    const toggle = screen.getByTestId("thinking-toggle");
    expect(toggle).toBeDisabled();
    expect(screen.queryByTestId("thinking-body")).toBeNull();
  });

  it("collapses to a one-line summary when done and expands on click", () => {
    render(
      <ProgressSteps
        replyState="complete"
        steps={[
          step("a", { state: "done", elapsedMs: 9000 }),
          step("b", { state: "done", elapsedMs: 3000 }),
        ]}
      />,
    );
    const toggle = screen.getByRole("button", {
      name: "Worked for 12 s · 2 steps",
    });
    expect(toggle).toHaveAttribute("aria-expanded", "false");
    expect(screen.queryAllByTestId("progress-step")).toHaveLength(0);
    fireEvent.click(toggle);
    expect(toggle).toHaveAttribute("aria-expanded", "true");
    expect(screen.getAllByTestId("progress-step")).toHaveLength(2);
    expect(toggle.getAttribute("aria-controls")).toBeTruthy();
  });

  it("collapses automatically when a live reply ends", () => {
    const steps = [step("a", { state: "done" })];
    const { rerender } = render(
      <ProgressSteps replyState="live" steps={steps} />,
    );
    expect(screen.getAllByTestId("progress-step")).toHaveLength(1);
    rerender(<ProgressSteps replyState="complete" steps={steps} />);
    expect(screen.queryAllByTestId("progress-step")).toHaveLength(0);
    expect(
      screen.getByRole("button", { name: "Worked · 1 step" }),
    ).toBeInTheDocument();
  });

  it("shows a skipped step with a plain detail as an Adjusted row, not a failure", () => {
    render(
      <ProgressSteps
        replyState="complete"
        steps={[
          step("a", {
            state: "skipped",
            label: "Render PDF",
            detail: "Adjusted the length to match the request.",
          }),
          step("b", { state: "failed", detail: "Disk is full" }),
        ]}
      />,
    );
    expect(
      screen.getByRole("button", { name: /Worked.*1 failed/ }),
    ).toBeInTheDocument();
    fireEvent.click(screen.getByRole("button", { name: /Worked/ }));
    expect(screen.getByText("Adjusted")).toBeInTheDocument();
    expect(
      screen.getByText("Adjusted the length to match the request."),
    ).toBeInTheDocument();
    expect(screen.queryByText("Skipped")).toBeNull();
    expect(screen.getAllByText("Failed")).toHaveLength(1);
  });

  it("keeps a skipped step without a detail as Skipped", () => {
    render(
      <ProgressSteps
        replyState="complete"
        steps={[step("a", { state: "skipped" })]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Worked/ }));
    expect(screen.getByText("Skipped")).toBeInTheDocument();
  });

  it("limits a long detail to two lines with a Show more control", () => {
    const long = `${"The renderer needs a shorter outline. ".repeat(10)}END`;
    render(
      <ProgressSteps
        replyState="complete"
        steps={[step("a", { state: "failed", detail: long })]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Worked/ }));
    const detail = screen.getByTestId("progress-step-detail");
    expect(detail.className).toContain("line-clamp-2");
    fireEvent.click(screen.getByRole("button", { name: "Show more" }));
    expect(screen.getByTestId("progress-step-detail").className).not.toContain(
      "line-clamp-2",
    );
    expect(screen.getByRole("button", { name: "Show less" })).toBeVisible();
  });

  it("never shows an instruction to the model such as call create_document again", () => {
    render(
      <ProgressSteps
        replyState="complete"
        steps={[
          step("a", {
            state: "failed",
            detail:
              "The PDF has 2 pages, not 3.\nPlease call create_document again with a longer spec.",
          }),
        ]}
      />,
    );
    fireEvent.click(screen.getByRole("button", { name: /Worked/ }));
    expect(screen.getByText("The PDF has 2 pages, not 3.")).toBeInTheDocument();
    expect(screen.queryByText(/create_document/)).toBeNull();
    expect(screen.queryByText(/again/)).toBeNull();
  });
});
