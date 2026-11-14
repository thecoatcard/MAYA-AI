import { fireEvent, render, screen } from "@testing-library/react";
import React, { useState } from "react";
import { describe, expect, it, vi } from "vitest";

import type { AttachmentItem } from "../../hooks/useAttachments";
import { ChatInput, type ChatInputProps } from "./ChatInput";

function Harness(props: Partial<ChatInputProps> = {}) {
  const [value, setValue] = useState("");
  return (
    <ChatInput
      value={value}
      onValueChange={setValue}
      onSubmit={vi.fn()}
      onStop={vi.fn()}
      busy={false}
      {...props}
    />
  );
}

const item = (over: Partial<AttachmentItem> = {}): AttachmentItem => ({
  key: "k1",
  id: "u1",
  name: "report.pdf",
  size: 2048,
  kind: "pdf",
  status: "ready",
  progress: 100,
  owned: true,
  ...over,
});

describe("ChatInput attachments", () => {
  it("has no attach control unless the parent turns it on", () => {
    render(<Harness />);
    expect(screen.queryByRole("button", { name: "Attach files" })).toBeNull();
  });

  it("opens the file picker from the labelled button and passes the chosen files", () => {
    const onAttach = vi.fn();
    render(<Harness onAttachFiles={onAttach} />);
    const input = screen.getByTestId("attach-input") as HTMLInputElement;
    const click = vi.spyOn(input, "click");
    fireEvent.click(screen.getByRole("button", { name: "Attach files" }));
    expect(click).toHaveBeenCalled();
    const file = new File(["x"], "a.txt");
    fireEvent.change(input, { target: { files: [file] } });
    expect(onAttach).toHaveBeenCalledWith([file]);
  });

  it("does not open the picker while attaching is off, and says why", () => {
    const onAttach = vi.fn();
    render(
      <Harness
        onAttachFiles={onAttach}
        attachDisabledReason="Not during a voice session."
      />,
    );
    const input = screen.getByTestId("attach-input") as HTMLInputElement;
    const click = vi.spyOn(input, "click");
    const button = screen.getByRole("button", { name: "Attach files" });
    expect(button.getAttribute("title")).toBe("Not during a voice session.");
    fireEvent.click(button);
    expect(click).not.toHaveBeenCalled();
    fireEvent.paste(screen.getByRole("textbox", { name: "Message" }), {
      clipboardData: { files: [new File(["x"], "a.png")], getData: () => "" },
    });
    expect(onAttach).not.toHaveBeenCalled();
  });

  it("attaches pasted files, but leaves pasted text alone", () => {
    const onAttach = vi.fn();
    render(<Harness onAttachFiles={onAttach} />);
    const box = screen.getByRole("textbox", { name: "Message" });
    const file = new File(["x"], "shot.png");
    fireEvent.paste(box, {
      clipboardData: { files: [file], getData: () => "" },
    });
    expect(onAttach).toHaveBeenCalledWith([file]);
    onAttach.mockClear();
    fireEvent.paste(box, {
      clipboardData: { files: [file], getData: () => "a table" },
    });
    expect(onAttach).not.toHaveBeenCalled();
  });

  it("shows a chip per file with its own error, and removes one by name", () => {
    const onRemove = vi.fn();
    render(
      <Harness
        onAttachFiles={vi.fn()}
        onRemoveAttachment={onRemove}
        attachments={[
          item(),
          item({
            key: "k2",
            id: null,
            name: "setup.exe",
            status: "error",
            error: "Programs and archives cannot be attached.",
          }),
        ]}
      />,
    );
    expect(screen.getAllByTestId("attachment-chip")).toHaveLength(2);
    expect(
      screen.getByText("Programs and archives cannot be attached."),
    ).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Remove report.pdf" }));
    expect(onRemove).toHaveBeenCalledWith("k1");
  });

  it("blocks sending while a file uploads and says so", () => {
    render(
      <Harness
        onAttachFiles={vi.fn()}
        attachments={[item({ status: "uploading", progress: 40, id: null })]}
      />,
    );
    fireEvent.change(screen.getByRole("textbox", { name: "Message" }), {
      target: { value: "hi" },
    });
    expect(
      (
        screen.getByRole("button", {
          name: "Send message",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(
      screen.getByText(/Waiting for the files to finish uploading/),
    ).toBeTruthy();
    expect(
      screen.getByRole("progressbar", { name: "Uploading report.pdf" }),
    ).toBeTruthy();
  });

  it("shows the limit notice and lets it be dismissed", () => {
    const dismiss = vi.fn();
    render(
      <Harness
        onAttachFiles={vi.fn()}
        attachNotice="Only 10 files fit in one message."
        onDismissAttachNotice={dismiss}
      />,
    );
    expect(screen.getByText(/Only 10 files fit/)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(dismiss).toHaveBeenCalled();
  });
});
