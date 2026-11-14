import { act, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { join } from "node:path";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import {
  LONG_PRESS_MS,
  MenuButton,
  closeAllContextMenus,
  computeMenuPosition,
  useContextMenu,
  type ContextMenuEntry,
} from "./ContextMenu";

function Surface({
  items,
  label = "Test menu",
  allowEditable = false,
  id = "surface",
}: {
  items: readonly ContextMenuEntry[] | null;
  label?: string;
  allowEditable?: boolean;
  id?: string;
}): React.JSX.Element {
  const m = useContextMenu({ getItems: () => items, label, allowEditable });
  return (
    <div>
      <div data-testid={id} tabIndex={0} {...m.triggerProps}>
        surface
        <input aria-label="field" />
        <MenuButton label={`more ${id}`} buttonProps={m.buttonProps}>
          ...
        </MenuButton>
      </div>
      {m.menu}
    </div>
  );
}

const noop = (): void => undefined;
const basic = (onA = noop): ContextMenuEntry[] => [
  { id: "a", label: "Alpha", onSelect: onA },
  { id: "b", label: "Bravo", onSelect: noop },
  { id: "c", label: "Charlie", disabled: true, disabledReason: "Not now" },
  { kind: "separator" },
  { id: "d", label: "Delta", danger: true, onSelect: noop },
];

describe("computeMenuPosition", () => {
  const view = { width: 400, height: 300 };
  it("opens at the pointer when it fits", () => {
    expect(
      computeMenuPosition({ x: 20, y: 30 }, { width: 100, height: 80 }, view),
    ).toEqual({ left: 20, top: 30 });
  });
  it("flips to the other side of the pointer near the edges", () => {
    expect(
      computeMenuPosition({ x: 380, y: 290 }, { width: 100, height: 80 }, view),
    ).toEqual({ left: 280, top: 210 });
  });
  it("clamps inside the viewport", () => {
    const p = computeMenuPosition(
      { x: 5, y: 5 },
      { width: 100, height: 80 },
      view,
    );
    expect(p.left).toBeGreaterThanOrEqual(8);
    expect(p.top).toBeGreaterThanOrEqual(8);
  });
});

describe("ContextMenu", () => {
  it("opens on contextmenu with roles, focuses the first item and prevents the native menu", () => {
    render(<Surface items={basic()} />);
    const prevented = !fireEvent.contextMenu(screen.getByTestId("surface"), {
      clientX: 10,
      clientY: 10,
    });
    expect(prevented).toBe(true);
    expect(screen.getByRole("menu", { name: "Test menu" })).toBeInTheDocument();
    expect(screen.getAllByRole("menuitem")).toHaveLength(4);
    expect(screen.getByRole("separator")).toBeInTheDocument();
    expect(screen.getByRole("menuitem", { name: /Alpha/ })).toHaveFocus();
    expect(screen.getByRole("menuitem", { name: /Charlie/ })).toHaveAttribute(
      "aria-disabled",
      "true",
    );
    expect(screen.getByText("Not now")).toBeInTheDocument();
  });

  it("is rendered in a portal on document.body", () => {
    const { container } = render(<Surface items={basic()} />);
    fireEvent.contextMenu(screen.getByTestId("surface"));
    expect(container.querySelector('[role="menu"]')).toBeNull();
    expect(document.body.querySelector('[role="menu"]')).not.toBeNull();
  });

  it("keeps the native menu on text inputs", () => {
    render(<Surface items={basic()} />);
    const notPrevented = fireEvent.contextMenu(screen.getByLabelText("field"));
    expect(notPrevented).toBe(true);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("takes over text inputs only when the surface opts in", () => {
    render(<Surface items={basic()} allowEditable />);
    fireEvent.contextMenu(screen.getByLabelText("field"));
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("does not suppress the native menu when there is nothing to show", () => {
    render(<Surface items={null} />);
    expect(fireEvent.contextMenu(screen.getByTestId("surface"))).toBe(true);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("navigates with arrows, Home, End and typeahead, skipping disabled items", () => {
    render(<Surface items={basic()} />);
    fireEvent.contextMenu(screen.getByTestId("surface"));
    const menu = screen.getByRole("menu");
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(screen.getByRole("menuitem", { name: /Bravo/ })).toHaveFocus();
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(screen.getByRole("menuitem", { name: /Delta/ })).toHaveFocus();
    fireEvent.keyDown(menu, { key: "ArrowDown" });
    expect(screen.getByRole("menuitem", { name: /Alpha/ })).toHaveFocus();
    fireEvent.keyDown(menu, { key: "End" });
    expect(screen.getByRole("menuitem", { name: /Delta/ })).toHaveFocus();
    fireEvent.keyDown(menu, { key: "Home" });
    expect(screen.getByRole("menuitem", { name: /Alpha/ })).toHaveFocus();
    fireEvent.keyDown(menu, { key: "d" });
    expect(screen.getByRole("menuitem", { name: /Delta/ })).toHaveFocus();
  });

  it("Enter and Space activate, close the menu and return focus to the trigger", () => {
    const onA = vi.fn();
    render(<Surface items={basic(onA)} />);
    const surface = screen.getByTestId("surface");
    surface.focus();
    fireEvent.contextMenu(surface);
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Enter" });
    expect(onA).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
    expect(surface).toHaveFocus();
  });

  it("does not activate a disabled item", () => {
    const onC = vi.fn();
    render(
      <Surface
        items={[
          { id: "a", label: "Alpha", onSelect: noop },
          { id: "c", label: "Charlie", disabled: true, onSelect: onC },
        ]}
      />,
    );
    fireEvent.contextMenu(screen.getByTestId("surface"));
    fireEvent.click(screen.getByRole("menuitem", { name: /Charlie/ }));
    expect(onC).not.toHaveBeenCalled();
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("Escape closes and returns focus to the trigger", () => {
    render(<Surface items={basic()} />);
    const surface = screen.getByTestId("surface");
    surface.focus();
    fireEvent.contextMenu(surface);
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    expect(screen.queryByRole("menu")).toBeNull();
    expect(surface).toHaveFocus();
  });

  it("opens from the Menu key and Shift+F10 on the focused element", () => {
    render(<Surface items={basic()} />);
    const surface = screen.getByTestId("surface");
    fireEvent.keyDown(surface, { key: "ContextMenu" });
    expect(screen.getByRole("menu")).toBeInTheDocument();
    fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });
    fireEvent.keyDown(surface, { key: "F10", shiftKey: true });
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("the visible button opens the same menu and carries aria-haspopup", () => {
    render(<Surface items={basic()} />);
    const button = screen.getByRole("button", { name: "more surface" });
    expect(button).toHaveAttribute("aria-haspopup", "menu");
    expect(button).toHaveAttribute("aria-expanded", "false");
    fireEvent.click(button);
    expect(screen.getByRole("menu")).toBeInTheDocument();
    expect(button).toHaveAttribute("aria-expanded", "true");
  });

  it("closes on a press outside, on scroll, on resize and on window blur", () => {
    render(<Surface items={basic()} />);
    const surface = screen.getByTestId("surface");
    const open = (): void => {
      fireEvent.contextMenu(surface);
      expect(screen.getByRole("menu")).toBeInTheDocument();
    };
    open();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("menu")).toBeNull();
    open();
    fireEvent.scroll(document);
    expect(screen.queryByRole("menu")).toBeNull();
    open();
    fireEvent(window, new Event("resize"));
    expect(screen.queryByRole("menu")).toBeNull();
    open();
    fireEvent(window, new Event("blur"));
    expect(screen.queryByRole("menu")).toBeNull();
    open();
    act(() => closeAllContextMenus());
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("a press inside the menu does not close it", () => {
    render(<Surface items={basic()} />);
    fireEvent.contextMenu(screen.getByTestId("surface"));
    fireEvent.pointerDown(screen.getByRole("menu"));
    expect(screen.getByRole("menu")).toBeInTheDocument();
  });

  it("only one menu is open at a time", () => {
    render(
      <>
        <Surface items={basic()} label="First" id="one" />
        <Surface items={basic()} label="Second" id="two" />
      </>,
    );
    fireEvent.contextMenu(screen.getByTestId("one"));
    fireEvent.contextMenu(screen.getByTestId("two"));
    expect(screen.getAllByRole("menu")).toHaveLength(1);
    expect(screen.getByRole("menu", { name: "Second" })).toBeInTheDocument();
  });

  it("opens a one-level submenu with ArrowRight and closes it with ArrowLeft", () => {
    const onPdf = vi.fn();
    render(
      <Surface
        items={[
          {
            id: "file",
            label: "Make a file",
            children: [
              { id: "pdf", label: "PDF", onSelect: onPdf },
              { id: "doc", label: "Word", onSelect: noop },
            ],
          },
          { id: "x", label: "Other", onSelect: noop },
        ]}
      />,
    );
    fireEvent.contextMenu(screen.getByTestId("surface"));
    const parent = screen.getByRole("menuitem", { name: /Make a file/ });
    expect(parent).toHaveAttribute("aria-haspopup", "menu");
    fireEvent.keyDown(parent, { key: "ArrowRight" });
    expect(screen.getAllByRole("menu")).toHaveLength(2);
    expect(screen.getByRole("menuitem", { name: "PDF" })).toHaveFocus();
    fireEvent.keyDown(screen.getByRole("menuitem", { name: "PDF" }), {
      key: "ArrowLeft",
    });
    expect(screen.getAllByRole("menu")).toHaveLength(1);
    expect(parent).toHaveFocus();
    fireEvent.keyDown(parent, { key: "ArrowRight" });
    fireEvent.keyDown(screen.getByRole("menuitem", { name: "PDF" }), {
      key: "Enter",
    });
    expect(onPdf).toHaveBeenCalledTimes(1);
    expect(screen.queryByRole("menu")).toBeNull();
  });

  it("renders checkbox items with their state and labelled groups", () => {
    render(
      <Surface
        items={[
          {
            kind: "group",
            label: "View",
            items: [{ id: "w", label: "Wrap", checked: true, onSelect: noop }],
          },
        ]}
      />,
    );
    fireEvent.contextMenu(screen.getByTestId("surface"));
    expect(
      screen.getByRole("menuitemcheckbox", { name: /Wrap/ }),
    ).toHaveAttribute("aria-checked", "true");
    expect(screen.getByRole("group", { name: "View" })).toBeInTheDocument();
  });

  it("opens after a long press on touch and not when the finger moves", () => {
    vi.useFakeTimers();
    try {
      render(<Surface items={basic()} />);
      const surface = screen.getByTestId("surface");
      fireEvent.pointerDown(surface, {
        pointerType: "touch",
        clientX: 5,
        clientY: 5,
      });
      act(() => {
        vi.advanceTimersByTime(LONG_PRESS_MS + 10);
      });
      expect(screen.getByRole("menu")).toBeInTheDocument();
      fireEvent.keyDown(screen.getByRole("menu"), { key: "Escape" });

      fireEvent.pointerDown(surface, {
        pointerType: "touch",
        clientX: 5,
        clientY: 5,
      });
      fireEvent.pointerMove(surface, {
        pointerType: "touch",
        clientX: 60,
        clientY: 5,
      });
      act(() => {
        vi.advanceTimersByTime(LONG_PRESS_MS + 10);
      });
      expect(screen.queryByRole("menu")).toBeNull();
    } finally {
      vi.useRealTimers();
    }
  });

  it("does not fetch or build anything until opened", () => {
    const getItems = vi.fn(() => basic());
    function Lazy(): React.JSX.Element {
      const m = useContextMenu({ getItems, label: "Lazy" });
      return (
        <div data-testid="lazy" {...m.triggerProps}>
          {m.menu}
        </div>
      );
    }
    render(<Lazy />);
    expect(getItems).not.toHaveBeenCalled();
    fireEvent.contextMenu(screen.getByTestId("lazy"));
    expect(getItems).toHaveBeenCalled();
  });
});

describe("context menu styling", () => {
  const css = readFileSync(
    join(process.cwd(), "src", "app", "ui-surfaces.css"),
    "utf8",
  );
  it("animates with scale and opacity within the 90 ms instant token and switches off for reduced motion", () => {
    expect(css).toMatch(
      /\.maya-ctx\s*\{[^}]*animation:\s*maya-ctx-in var\(--maya-dur-instant\)/,
    );
    expect(css).toMatch(
      /@keyframes maya-ctx-in\s*\{[^@]*opacity: 0;[^@]*transform: scale\(0\.96\)/,
    );
    expect(css).toMatch(
      /@media \(prefers-reduced-motion: reduce\)\s*\{\s*\.maya-ctx\s*\{\s*animation: none/,
    );
  });
  it("uses tokens, no raw colours", () => {
    const block = css.slice(css.indexOf("/* ---- context menu"));
    const section = css.slice(css.indexOf("context menu */"));
    expect(block + section).not.toMatch(/#[0-9a-f]{3,8}\b|rgb\(/i);
  });
});
