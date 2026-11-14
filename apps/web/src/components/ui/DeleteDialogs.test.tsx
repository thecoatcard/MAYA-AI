import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { ConfirmDeleteDialog } from "./ConfirmDeleteDialog";
import { SelectionBar } from "./SelectionBar";

describe("ConfirmDeleteDialog", () => {
  const base = {
    title: "Delete this task?",
    removes: ["The task and its steps."],
    keeps: ["The audit log."],
    confirmLabel: "Delete task",
    onConfirm: vi.fn(),
    onClose: vi.fn(),
  };

  it("renders through a portal on document.body, outside a vibrancy ancestor, with Cancel before Delete", () => {
    const { container } = render(
      <aside className="maya-vib-side" data-testid="host">
        <ConfirmDeleteDialog {...base} />
      </aside>,
    );
    const dialog = screen.getByRole("dialog", { name: "Delete this task?" });
    const scrim = dialog.closest(".maya-sheet-scrim")!;
    expect(container.contains(dialog)).toBe(false);
    expect(scrim.parentElement).toBe(document.body);
    expect(scrim).toHaveClass("fixed", "inset-0");
    const buttons = screen.getAllByRole("button").map((b) => b.textContent);
    expect(buttons.indexOf("Cancel")).toBeLessThan(
      buttons.findIndex((t) => t?.includes("Delete task")),
    );
  });

  it("is a labelled dialog that says what is removed, what is kept, and that there is no undo", () => {
    render(<ConfirmDeleteDialog {...base} />);
    expect(
      screen.getByRole("dialog", { name: "Delete this task?" }),
    ).toBeInTheDocument();
    expect(screen.getByTestId("delete-removes")).toHaveTextContent(
      "The task and its steps.",
    );
    expect(screen.getByTestId("delete-keeps")).toHaveTextContent(
      "The audit log.",
    );
    expect(screen.getByText("This cannot be undone.")).toBeInTheDocument();
  });

  it("starts focus on Cancel, closes on Escape and confirms only on the delete button", () => {
    const onClose = vi.fn();
    const onConfirm = vi.fn();
    render(
      <ConfirmDeleteDialog {...base} onClose={onClose} onConfirm={onConfirm} />,
    );
    expect(screen.getByRole("button", { name: "Cancel" })).toHaveFocus();
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalled();
    expect(onConfirm).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Delete task/ }));
    expect(onConfirm).toHaveBeenCalledTimes(1);
  });

  it("shows an error as an alert and ignores Escape while deleting", () => {
    const onClose = vi.fn();
    render(
      <ConfirmDeleteDialog
        {...base}
        onClose={onClose}
        busy
        error="The task could not be deleted. Try again."
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("could not be deleted");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).not.toHaveBeenCalled();
  });
});

describe("SelectionBar", () => {
  it("renders nothing without a selection", () => {
    const { container } = render(
      <SelectionBar
        count={0}
        noun="tasks"
        onDelete={vi.fn()}
        onClear={vi.fn()}
      />,
    );
    expect(container).toBeEmptyDOMElement();
  });

  it("says how many are selected and offers Delete and Clear selection", () => {
    const onDelete = vi.fn();
    const onClear = vi.fn();
    render(
      <SelectionBar
        count={3}
        noun="tasks"
        onDelete={onDelete}
        onClear={onClear}
      />,
    );
    expect(
      screen.getByRole("region", { name: "Selected tasks" }),
    ).toHaveTextContent("3 selected");
    fireEvent.click(screen.getByRole("button", { name: "Delete" }));
    fireEvent.click(screen.getByRole("button", { name: "Clear selection" }));
    expect(onDelete).toHaveBeenCalled();
    expect(onClear).toHaveBeenCalled();
  });
});
