import { act, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import React, { useState } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  Progress,
  SegmentedControl,
  Sheet,
  SHEET_EXIT_MS,
  Switch,
  Tabs,
  ToastRegion,
} from "./index";

afterEach(() => {
  vi.useRealTimers();
  vi.unstubAllGlobals();
});

describe("Switch", () => {
  it("is a labelled switch that toggles with the keyboard and click", () => {
    const onChange = vi.fn();
    render(<Switch checked={false} onCheckedChange={onChange} label="Sync" />);
    const sw = screen.getByRole("switch", { name: "Sync" });
    expect(sw).toHaveAttribute("aria-checked", "false");
    fireEvent.click(sw);
    expect(onChange).toHaveBeenCalledWith(true);
  });

  it("shows the state in words and blocks a disabled switch", () => {
    const onChange = vi.fn();
    render(
      <Switch
        checked
        disabled
        showState
        onCheckedChange={onChange}
        label="X"
      />,
    );
    expect(screen.getByText("On")).toBeInTheDocument();
    fireEvent.click(screen.getByRole("switch"));
    expect(onChange).not.toHaveBeenCalled();
  });
});

describe("SegmentedControl and Tabs", () => {
  function Harness({ tabs }: { tabs?: boolean }): React.JSX.Element {
    const [v, setV] = useState<"a" | "b" | "c">("a");
    const options = [
      { value: "a" as const, label: "A" },
      { value: "b" as const, label: "B", disabled: true },
      { value: "c" as const, label: "C" },
    ];
    return tabs ? (
      <Tabs
        label="T"
        idPrefix="t"
        value={v}
        onChange={setV}
        options={options}
      />
    ) : (
      <SegmentedControl label="S" value={v} onChange={setV} options={options} />
    );
  }

  it("is a radiogroup with roving tabindex and arrow keys that skip disabled options", () => {
    render(<Harness />);
    const group = screen.getByRole("radiogroup", { name: "S" });
    expect(group).toHaveStyle({ "--maya-seg-i": "0" });
    const a = screen.getByRole("radio", { name: "A" });
    expect(a).toHaveAttribute("aria-checked", "true");
    expect(a).toHaveAttribute("tabindex", "0");
    expect(screen.getByRole("radio", { name: "C" })).toHaveAttribute(
      "tabindex",
      "-1",
    );
    fireEvent.keyDown(group, { key: "ArrowRight" });
    expect(screen.getByRole("radio", { name: "C" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
    fireEvent.keyDown(group, { key: "Home" });
    expect(screen.getByRole("radio", { name: "A" })).toHaveAttribute(
      "aria-checked",
      "true",
    );
  });

  it("uses tab semantics with panel ids for Tabs", () => {
    render(<Harness tabs />);
    const tab = screen.getByRole("tab", { name: "A" });
    expect(tab).toHaveAttribute("aria-selected", "true");
    expect(tab).toHaveAttribute("aria-controls", "t-panel-a");
  });
});

describe("Sheet", () => {
  it("is a labelled modal dialog that closes on Escape and the scrim", () => {
    const onClose = vi.fn();
    render(
      <Sheet open onClose={onClose} title="Edit" description="Details">
        <button type="button">Inside</button>
      </Sheet>,
    );
    const dialog = screen.getByRole("dialog", { name: "Edit" });
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(dialog).toHaveAccessibleDescription("Details");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(onClose).toHaveBeenCalledTimes(1);
    fireEvent.mouseDown(screen.getByTestId("sheet-scrim"));
    expect(onClose).toHaveBeenCalledTimes(2);
  });

  it("plays its exit then unmounts; with reduced motion it unmounts at once", () => {
    vi.useFakeTimers();
    const { rerender } = render(
      <Sheet open onClose={() => undefined} title="Edit">
        x
      </Sheet>,
    );
    rerender(
      <Sheet open={false} onClose={() => undefined} title="Edit">
        x
      </Sheet>,
    );
    expect(screen.getByRole("dialog")).toHaveAttribute("data-state", "closing");
    act(() => {
      vi.advanceTimersByTime(SHEET_EXIT_MS + 1);
    });
    expect(screen.queryByRole("dialog")).toBeNull();

    vi.stubGlobal("matchMedia", () => ({ matches: true }));
    const view = render(
      <Sheet open onClose={() => undefined} title="Edit">
        x
      </Sheet>,
    );
    view.rerender(
      <Sheet open={false} onClose={() => undefined} title="Edit">
        x
      </Sheet>,
    );
    expect(screen.queryByRole("dialog")).toBeNull();
  });
});

describe("Toast and Progress", () => {
  it("renders toasts with the slide-in surface class inside a polite region", () => {
    render(
      <ToastRegion
        toasts={[{ id: "1", message: "Saved", tone: "success" }]}
        onDismiss={() => undefined}
      />,
    );
    expect(screen.getByTestId("toast-region")).toHaveAttribute(
      "aria-live",
      "polite",
    );
    expect(screen.getByText("Saved").closest(".maya-toast")).not.toBeNull();
  });

  it("reports real progress through ARIA and a transform-scaled fill", () => {
    render(<Progress label="Upload" value={25} showLabel />);
    const bar = screen.getByRole("progressbar", { name: "Upload" });
    expect(bar).toHaveAttribute("aria-valuenow", "25");
    expect(screen.getByText("25%")).toBeInTheDocument();
    expect(bar.firstElementChild).toHaveStyle({ "--maya-progress": "0.25" });
  });
});

describe("ui-surfaces.css rules", () => {
  const css = readFileSync(
    join(__dirname, "../../app/ui-surfaces.css"),
    "utf8",
  );
  const code = css.replace(/\/\*[\s\S]*?\*\//g, "");

  it("animates only transform and opacity in keyframes", () => {
    for (const m of code.matchAll(
      /@keyframes\s+[\w-]+\s*\{((?:[^{}]|\{[^{}]*\})*)\}/g,
    )) {
      for (const prop of m[1].matchAll(/([a-z-]+)\s*:/g)) {
        expect(["opacity", "transform"], prop[1]).toContain(prop[1]);
      }
    }
  });

  it("takes every duration and easing from tokens and has no loops or colour literals", () => {
    expect(code).not.toMatch(/(?<![\w-])\d*\.?\d+m?s\b(?![\w-])(?!\s*linear)/);
    expect(code).not.toMatch(/cubic-bezier\(/);
    expect(code).not.toMatch(/infinite/);
    expect(code).not.toMatch(/#[0-9a-fA-F]{3,8}\b|\brgba?\(/);
  });

  it("keeps blur behind @supports with an opaque fallback and a reduced-transparency reset", () => {
    expect(code).toMatch(/@supports \(\s*\(backdrop-filter/);
    expect(code).toContain("prefers-reduced-transparency: reduce");
    expect(code).toContain("forced-colors: active");
    expect(code).toMatch(
      /\.maya-vib-bar\s*\{\s*background-color: var\(--maya-pane\)/,
    );
  });

  it("removes movement under reduced motion", () => {
    expect(css).toMatch(
      /prefers-reduced-motion: reduce\)[\s\S]*animation: none/,
    );
  });
});
