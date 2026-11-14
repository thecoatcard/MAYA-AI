import React, { useState } from "react";
import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ChatInput, type ChatInputProps } from "./ChatInput";

function Harness(props: Partial<ChatInputProps> & { initial?: string } = {}) {
  const { initial = "", ...rest } = props;
  const [value, setValue] = useState(initial);
  return (
    <ChatInput
      value={value}
      onValueChange={setValue}
      onSubmit={vi.fn()}
      onStop={vi.fn()}
      busy={false}
      {...rest}
    />
  );
}

function textbox(): HTMLTextAreaElement {
  return screen.getByRole("textbox", {
    name: "Message",
  }) as HTMLTextAreaElement;
}

describe("ChatInput keys", () => {
  it("submits on Enter and sends the current text", () => {
    const onSubmit = vi.fn();
    render(<Harness initial="Hello there" onSubmit={onSubmit} />);
    fireEvent.keyDown(textbox(), { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith("Hello there");
  });

  it("does not submit on Shift+Enter, so it can insert a newline", () => {
    const onSubmit = vi.fn();
    render(<Harness initial="Hello" onSubmit={onSubmit} />);
    const notPrevented = fireEvent.keyDown(textbox(), {
      key: "Enter",
      shiftKey: true,
    });
    expect(onSubmit).not.toHaveBeenCalled();
    expect(notPrevented).toBe(true);
  });

  it("prevents the default newline when Enter submits", () => {
    render(<Harness initial="Hello" />);
    expect(fireEvent.keyDown(textbox(), { key: "Enter" })).toBe(false);
  });

  it("does not submit while an IME composition is active", () => {
    const onSubmit = vi.fn();
    render(<Harness initial="にほん" onSubmit={onSubmit} />);
    fireEvent.compositionStart(textbox());
    fireEvent.keyDown(textbox(), { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();
    fireEvent.compositionEnd(textbox());
    fireEvent.keyDown(textbox(), { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledTimes(1);
  });

  it("ignores the Enter that confirms an IME candidate (isComposing and keyCode 229)", () => {
    const onSubmit = vi.fn();
    render(<Harness initial="text" onSubmit={onSubmit} />);
    fireEvent.keyDown(textbox(), { key: "Enter", isComposing: true });
    fireEvent.keyDown(textbox(), { key: "Enter", keyCode: 229 });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("does not submit an empty or whitespace-only message", () => {
    const onSubmit = vi.fn();
    const empty = render(<Harness initial="" onSubmit={onSubmit} />);
    fireEvent.keyDown(textbox(), { key: "Enter" });
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
    empty.unmount();
    render(<Harness initial="   " onSubmit={onSubmit} />);
    fireEvent.keyDown(textbox(), { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  });

  it("sends with the button too", () => {
    const onSubmit = vi.fn();
    render(<Harness initial="Hi" onSubmit={onSubmit} />);
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(onSubmit).toHaveBeenCalledWith("Hi");
  });

  it("keeps typing in the textbox and reports each change", () => {
    render(<Harness />);
    fireEvent.change(textbox(), { target: { value: "abc" } });
    expect(textbox().value).toBe("abc");
  });
});

describe("ChatInput while a reply is being written", () => {
  it("turns the same button into Stop and never submits on Enter", () => {
    const onSubmit = vi.fn();
    const onStop = vi.fn();
    render(
      <Harness
        initial="next question"
        busy
        onSubmit={onSubmit}
        onStop={onStop}
      />,
    );
    expect(screen.queryByRole("button", { name: "Send message" })).toBeNull();
    fireEvent.keyDown(textbox(), { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();
    const stop = screen.getByRole("button", { name: "Stop generating" });
    expect(stop).toBeEnabled();
    expect(stop).toHaveAttribute("data-mode", "stop");
    fireEvent.click(stop);
    expect(onStop).toHaveBeenCalledTimes(1);
  });

  it("keeps the textbox editable so the next message can be drafted", () => {
    render(<Harness busy />);
    expect(textbox()).toBeEnabled();
  });

  it("returns focus to the textbox after Stop and after Send", () => {
    const { rerender } = render(<Harness initial="x" busy />);
    fireEvent.click(screen.getByRole("button", { name: "Stop generating" }));
    expect(document.activeElement).toBe(textbox());
    (document.activeElement as HTMLElement).blur();
    rerender(<Harness initial="x" busy={false} />);
    fireEvent.click(screen.getByRole("button", { name: "Send message" }));
    expect(document.activeElement).toBe(textbox());
  });
});

describe("ChatInput disabled", () => {
  it("disables the textbox and sending", () => {
    const onSubmit = vi.fn();
    render(<Harness initial="x" disabled onSubmit={onSubmit} />);
    expect(textbox()).toBeDisabled();
    expect(screen.getByRole("button", { name: "Send message" })).toBeDisabled();
  });
});

describe("ChatInput length", () => {
  it("shows the character count without a fixed limit", () => {
    render(<Harness initial="hello" />);
    expect(screen.getByText("5 characters")).toBeInTheDocument();
    expect(screen.queryByText(/ \/ /)).toBeNull();
  });

  it("counts characters, not UTF-16 units", () => {
    render(<Harness initial="😀😀" />);
    expect(screen.getByText("2 characters")).toBeInTheDocument();
  });

  it("has no warning or block for long text", () => {
    const onSubmit = vi.fn();
    const { container } = render(
      <Harness initial={"a".repeat(50_000)} onSubmit={onSubmit} />,
    );
    expect(screen.queryByText(/limit/i)).toBeNull();
    expect(container.querySelector(".maya-composer__count svg")).toBeNull();
    expect(textbox()).toHaveAttribute("aria-invalid", "false");
    expect(screen.getByRole("button", { name: "Send message" })).toBeEnabled();
  });

  it("sends long text in full", () => {
    const onSubmit = vi.fn();
    const long = "b".repeat(50_000);
    render(<Harness onSubmit={onSubmit} />);
    fireEvent.change(textbox(), { target: { value: long } });
    fireEvent.keyDown(textbox(), { key: "Enter" });
    expect(onSubmit).toHaveBeenCalledTimes(1);
    expect(onSubmit).toHaveBeenCalledWith(long);
  });

  it("does not truncate pasted text", () => {
    render(<Harness />);
    const long = "b".repeat(50_000);
    fireEvent.change(textbox(), { target: { value: long } });
    expect(textbox().value).toHaveLength(long.length);
  });
});

describe("ChatInput validation", () => {
  it("blocks characters the server would reject", () => {
    const onSubmit = vi.fn();
    render(<Harness initial={"bad\u0000text"} onSubmit={onSubmit} />);
    expect(
      screen.getByText("Contains characters that cannot be sent."),
    ).toBeInTheDocument();
    fireEvent.keyDown(textbox(), { key: "Enter" });
    expect(onSubmit).not.toHaveBeenCalled();
  });

  it("describes the textbox with the hint and counter", () => {
    render(<Harness initial="hi" />);
    const describedBy = textbox().getAttribute("aria-describedby");
    expect(describedBy).toBeTruthy();
    const description = document.getElementById(describedBy ?? "");
    expect(description?.textContent).toContain("Enter to send");
    expect(description?.textContent).toContain("2 characters");
  });
});
