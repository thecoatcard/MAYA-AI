import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import type { ChatInputRequest } from "../../lib/ask-user";
import { AskUserCard } from "./AskUserCard";

const request: ChatInputRequest = {
  askId: "ask_1",
  source: "chat",
  questions: [
    {
      question: "Which language?",
      header: "Language",
      options: [
        { label: "Java", description: "Strongly typed" },
        { label: "Python", description: "" },
        { label: "[docs](https://evil.example)", description: "" },
      ],
      multiSelect: false,
      allowText: true,
    },
  ],
};

const multi: ChatInputRequest = {
  askId: "ask_2",
  source: "chat",
  questions: [
    {
      question: "Pick themes",
      header: "Theme",
      options: [
        { label: "aurora", description: "" },
        { label: "plain", description: "" },
        { label: "dark", description: "" },
      ],
      multiSelect: true,
      allowText: false,
    },
    {
      question: "Folder name?",
      header: "Folder",
      options: [],
      multiSelect: false,
      allowText: true,
    },
  ],
};

function mount(
  req = request,
  extra: Partial<Parameters<typeof AskUserCard>[0]> = {},
) {
  const onSubmit = vi.fn();
  const onSkip = vi.fn();
  const view = render(
    <AskUserCard
      request={req}
      state="awaiting_input"
      onSubmit={onSubmit}
      onSkip={onSkip}
      {...extra}
    />,
  );
  return { onSubmit, onSkip, view };
}

const radios = () => screen.getAllByRole("radio");

describe("AskUserCard", () => {
  it("renders numbered radio options, the first one highlighted and focused", () => {
    mount();
    expect(screen.getByRole("radiogroup")).toBeTruthy();
    expect(radios()).toHaveLength(3);
    expect(radios()[0]?.textContent).toContain("1");
    expect(radios()[0]?.getAttribute("data-highlight")).toBe("true");
    expect(document.activeElement).toBe(radios()[0]);
    expect(screen.getByText("Language")).toBeTruthy();
    expect(
      screen.getByLabelText("Or tell MAYA what you want instead"),
    ).toBeTruthy();
    expect(screen.getByText(/Not an approval/)).toBeTruthy();
  });

  it("renders model text as plain text, never as a link", () => {
    const { view } = mount();
    expect(view.container.querySelector("a")).toBeNull();
    expect(
      screen.getAllByText("[docs](https://evil.example)").length,
    ).toBeGreaterThan(0);
  });

  it("keys 1-9 pick and Enter submits", () => {
    const { onSubmit } = mount();
    fireEvent.keyDown(radios()[0] as HTMLElement, { key: "2" });
    expect(radios()[1]?.getAttribute("aria-checked")).toBe("true");
    fireEvent.keyDown(radios()[1] as HTMLElement, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledWith([{ selected: ["Python"], text: "" }]);
  });

  it("Enter with nothing chosen takes the highlighted option; nothing is sent before a key", () => {
    const { onSubmit } = mount();
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(radios()[0] as HTMLElement, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledWith([{ selected: ["Java"], text: "" }]);
  });

  it("ArrowDown and ArrowUp move the highlight and focus", () => {
    mount();
    fireEvent.keyDown(radios()[0] as HTMLElement, { key: "ArrowDown" });
    expect(document.activeElement).toBe(radios()[1]);
    fireEvent.keyDown(radios()[1] as HTMLElement, { key: "ArrowUp" });
    expect(document.activeElement).toBe(radios()[0]);
  });

  it("Escape skips", () => {
    const { onSkip, onSubmit } = mount();
    fireEvent.keyDown(radios()[0] as HTMLElement, { key: "Escape" });
    expect(onSkip).toHaveBeenCalledTimes(1);
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("has visible Submit and Skip buttons; Submit waits for an answer", () => {
    const { onSubmit, onSkip } = mount();
    const submit = screen.getByRole("button", {
      name: /Submit/,
    }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    fireEvent.click(radios()[2] as HTMLElement);
    expect(submit.disabled).toBe(false);
    fireEvent.click(submit);
    expect(onSubmit).toHaveBeenCalledWith([
      { selected: ["[docs](https://evil.example)"], text: "" },
    ]);
    fireEvent.click(screen.getByRole("button", { name: /Skip/ }));
    expect(onSkip).toHaveBeenCalled();
  });

  it("free text: Enter submits, Shift+Enter does not", () => {
    const { onSubmit } = mount();
    const field = screen.getByLabelText("Or tell MAYA what you want instead");
    fireEvent.change(field, { target: { value: "Rust please" } });
    fireEvent.keyDown(field, { key: "Enter", shiftKey: true });
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.keyDown(field, { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledWith([
      { selected: [], text: "Rust please" },
    ]);
  });

  it("multi-select toggles several options and stacks questions with one Submit", () => {
    const { onSubmit } = mount(multi);
    expect(
      screen.getAllByRole("group", { name: /Pick themes/ }).length,
    ).toBeGreaterThan(0);
    const boxes = screen.getAllByRole("checkbox");
    fireEvent.click(boxes[0] as HTMLElement);
    fireEvent.click(boxes[2] as HTMLElement);
    fireEvent.click(boxes[2] as HTMLElement);
    fireEvent.click(boxes[1] as HTMLElement);
    fireEvent.change(screen.getByLabelText("Your answer"), {
      target: { value: " docs " },
    });
    expect(
      screen.queryByLabelText("Or tell MAYA what you want instead"),
    ).toBeNull();
    fireEvent.click(screen.getByRole("button", { name: /Submit/ }));
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith([
      { selected: ["aurora", "plain"], text: "" },
      { selected: [], text: "docs" },
    ]);
  });

  it("disables the buttons while submitting", () => {
    mount(request, { submitting: true });
    const submit = screen.getByRole("button", {
      name: /Submit/,
    }) as HTMLButtonElement;
    const skip = screen.getByRole("button", {
      name: /Skip/,
    }) as HTMLButtonElement;
    expect(submit.disabled).toBe(true);
    expect(skip.disabled).toBe(true);
  });

  it("does not steal focus from a composer with text in it", () => {
    const composer = document.createElement("textarea");
    composer.value = "typing";
    document.body.append(composer);
    composer.focus();
    mount();
    expect(document.activeElement).toBe(composer);
    composer.remove();
  });

  it("shows a compact read-only summary once answered", () => {
    render(
      <AskUserCard
        request={request}
        state="answered"
        answer={{
          askId: "ask_1",
          answers: [
            { question: "Which language?", selected: ["Java"], text: "" },
          ],
        }}
        onSubmit={vi.fn()}
        onSkip={vi.fn()}
      />,
    );
    expect(screen.getByText("Java", { exact: false })).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
    expect(screen.queryByRole("radio")).toBeNull();
  });

  it("collapses to a skipped line when dismissed", () => {
    render(
      <AskUserCard
        request={request}
        state="dismissed"
        onSubmit={vi.fn()}
        onSkip={vi.fn()}
      />,
    );
    expect(screen.getByText(/Skipped: Which language/)).toBeTruthy();
    expect(screen.queryByRole("button")).toBeNull();
  });
});
