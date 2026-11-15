import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { useFileDrop } from "./useFileDrop";

function Zone({
  onFiles,
  enabled,
}: {
  onFiles: (f: File[]) => void;
  enabled?: boolean;
}) {
  const { dragging, bindings } = useFileDrop(onFiles, enabled);
  return (
    <div data-testid="zone" data-dragging={dragging} {...bindings}>
      <span data-testid="child" />
    </div>
  );
}
const fileDrag = { dataTransfer: { types: ["Files"], files: [] as File[] } };

describe("useFileDrop", () => {
  it("reacts only to file drags and hands the dropped files over", () => {
    const onFiles = vi.fn();
    render(<Zone onFiles={onFiles} />);
    const zone = screen.getByTestId("zone");
    fireEvent.dragEnter(zone, { dataTransfer: { types: ["text/plain"] } });
    expect(zone.getAttribute("data-dragging")).toBe("false");
    fireEvent.dragEnter(zone, fileDrag);
    expect(zone.getAttribute("data-dragging")).toBe("true");
    const file = new File(["x"], "a.txt");
    fireEvent.drop(zone, { dataTransfer: { types: ["Files"], files: [file] } });
    expect(zone.getAttribute("data-dragging")).toBe("false");
    expect(onFiles).toHaveBeenCalledWith([file]);
  });

  it("does not flicker when the drag moves over a child", () => {
    render(<Zone onFiles={vi.fn()} />);
    const zone = screen.getByTestId("zone");
    fireEvent.dragEnter(zone, fileDrag);
    fireEvent.dragEnter(screen.getByTestId("child"), fileDrag);
    fireEvent.dragLeave(zone, fileDrag);
    expect(zone.getAttribute("data-dragging")).toBe("true");
    fireEvent.dragLeave(screen.getByTestId("child"), fileDrag);
    expect(zone.getAttribute("data-dragging")).toBe("false");
  });

  it("ignores everything when disabled", () => {
    const onFiles = vi.fn();
    render(<Zone onFiles={onFiles} enabled={false} />);
    const zone = screen.getByTestId("zone");
    fireEvent.dragEnter(zone, fileDrag);
    fireEvent.drop(zone, {
      dataTransfer: { types: ["Files"], files: [new File(["x"], "a")] },
    });
    expect(zone.getAttribute("data-dragging")).toBe("false");
    expect(onFiles).not.toHaveBeenCalled();
  });
});
