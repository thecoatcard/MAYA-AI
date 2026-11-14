import {
  act,
  fireEvent,
  render,
  renderHook,
  screen,
} from "@testing-library/react";
import { Plus } from "lucide-react";
import React, { useRef } from "react";
import { afterEach, describe, expect, it, vi } from "vitest";

import {
  Badge,
  Button,
  CodeBlockShell,
  EmptyState,
  ErrorState,
  IconButton,
  Kbd,
  Notice,
  Pane,
  Popover,
  Row,
  ScrollArea,
  Skeleton,
  Stack,
  StatusPill,
  TableScroll,
  ToastRegion,
  Tooltip,
  Truncate,
  WrapText,
  computePlacement,
  cx,
  useToasts,
} from "./index";

afterEach(() => {
  vi.useRealTimers();
});

describe("cx", () => {
  it("joins truthy parts only", () => {
    expect(cx("a", false, null, undefined, "b")).toBe("a b");
  });
});

describe("Pane", () => {
  it("has header, body and footer slots and the body is the only scroll region", () => {
    render(
      <Pane header="Head" footer="Foot" bodyLabel="Messages">
        body text
      </Pane>,
    );
    const body = screen.getByRole("region", { name: "Messages" });
    expect(body).toHaveTextContent("body text");
    expect(body.className).toContain("overflow-auto");
    expect(body.className).toContain("min-h-0");
    expect(body.className).toContain("min-w-0");
    expect(body).toHaveAttribute("tabindex", "0");
    const pane = screen.getByText("Head").closest(".maya-pane") as HTMLElement;
    expect(pane.className).toContain("min-h-0");
    expect(pane.className).not.toContain("overflow-auto");
    expect(screen.getByText("Foot").className).toContain("shrink-0");
    expect(screen.getByText("Head").className).toContain("shrink-0");
  });

  it("omits empty slots and the region role without a label", () => {
    const { container } = render(<Pane>only body</Pane>);
    expect(container.querySelector(".maya-pane-header")).toBeNull();
    expect(container.querySelector(".maya-pane-footer")).toBeNull();
    expect(screen.queryByRole("region")).toBeNull();
  });
});

describe("Stack and Row", () => {
  it("default to min-w-0 so children can shrink", () => {
    render(
      <>
        <Stack data-testid="s" gap={3}>
          x
        </Stack>
        <Row data-testid="r" wrap justify="between">
          y
        </Row>
      </>,
    );
    expect(screen.getByTestId("s").className).toContain("min-w-0");
    expect(screen.getByTestId("s").className).toContain("flex-col");
    expect(screen.getByTestId("s").className).toContain("gap-3");
    const row = screen.getByTestId("r");
    expect(row.className).toContain("min-w-0");
    expect(row.className).toContain("flex-row");
    expect(row.className).toContain("flex-wrap");
    expect(row.className).toContain("items-center");
  });
});

describe("Truncate and WrapText", () => {
  it("truncates to one line and keeps the full text in the title", () => {
    render(<Truncate>a very long title</Truncate>);
    const el = screen.getByText("a very long title");
    expect(el).toHaveAttribute("title", "a very long title");
    expect(el.className).toContain("truncate");
    expect(el.className).toContain("min-w-0");
  });

  it("clamps to several lines", () => {
    render(
      <Truncate lines={3} title="full">
        text
      </Truncate>,
    );
    const el = screen.getByText("text");
    expect(el.style.getPropertyValue("-webkit-line-clamp")).toBe("3");
    expect(el.className.split(" ")).not.toContain("truncate");
    expect(el).toHaveAttribute("title", "full");
  });

  it("wraps unbroken strings anywhere", () => {
    render(<WrapText>https://example.com/aaaaaaaaaaaaaaaaaaaaaaaa</WrapText>);
    expect(screen.getByText(/example\.com/).className).toContain(
      "[overflow-wrap:anywhere]",
    );
  });
});

describe("ScrollArea, CodeBlockShell, TableScroll", () => {
  it("are named, focusable and scroll inside their own box", () => {
    render(
      <>
        <ScrollArea label="List">items</ScrollArea>
        <CodeBlockShell label="Payload">{"{ }"}</CodeBlockShell>
        <TableScroll label="Rows">
          <table />
        </TableScroll>
      </>,
    );
    const list = screen.getByRole("region", { name: "List" });
    expect(list.className).toContain("overflow-y-auto");
    expect(list.className).toContain("min-h-0");
    const code = screen.getByRole("region", { name: "Payload" });
    expect(code.tagName).toBe("PRE");
    expect(code.className).toContain("overflow-auto");
    expect(code.className).toContain("whitespace-pre");
    expect(code.className).toContain("max-w-full");
    expect(code.style.maxHeight).toBe("15rem");
    const table = screen.getByRole("region", { name: "Rows" });
    expect(table.className).toContain("overflow-x-auto");
    for (const el of [list, code, table]) {
      expect(el).toHaveAttribute("tabindex", "0");
    }
  });
});

describe("Button", () => {
  it("defaults to type button with hit-height classes for fine and coarse pointers", () => {
    render(<Button>Save</Button>);
    const b = screen.getByRole("button", { name: "Save" });
    expect(b).toHaveAttribute("type", "button");
    expect(b.className).toContain("min-h-9");
    expect(b.className).toContain("pointer-coarse:min-h-11");
  });

  it("supports every variant and size", () => {
    render(
      <>
        <Button variant="primary">p</Button>
        <Button variant="ghost" size="sm">
          g
        </Button>
        <Button variant="danger" size="lg">
          d
        </Button>
      </>,
    );
    expect(screen.getByText("p").closest("button")?.className).toContain(
      "bg-maya-accent",
    );
    expect(screen.getByText("g").closest("button")?.className).toContain(
      "min-h-8",
    );
    expect(screen.getByText("d").closest("button")?.className).toContain(
      "text-maya-danger",
    );
  });

  it("loading blocks clicks, announces busy and keeps the label's space", () => {
    const onClick = vi.fn();
    render(
      <Button loading onClick={onClick}>
        Send
      </Button>,
    );
    const b = screen.getByRole("button");
    expect(b).toBeDisabled();
    expect(b).toHaveAttribute("aria-busy", "true");
    expect(b.querySelector("svg")?.getAttribute("class")).toContain(
      "motion-reduce:animate-none",
    );
    fireEvent.click(b);
    expect(onClick).not.toHaveBeenCalled();
    expect(screen.getByText("Send").parentElement?.className).toContain(
      "invisible",
    );
  });

  it("disabled does not fire and fires normally otherwise", () => {
    const onClick = vi.fn();
    const { rerender } = render(<Button onClick={onClick}>Go</Button>);
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
    rerender(
      <Button disabled onClick={onClick}>
        Go
      </Button>,
    );
    fireEvent.click(screen.getByRole("button"));
    expect(onClick).toHaveBeenCalledTimes(1);
  });

  it("IconButton takes its name from the required label", () => {
    render(<IconButton label="Add item" icon={<Plus size={16} />} />);
    const b = screen.getByRole("button", { name: "Add item" });
    expect(b).toHaveAttribute("title", "Add item");
    expect(b.className).toContain("min-w-9");
  });
});

describe("Badge and StatusPill", () => {
  it("always render an icon and text", () => {
    const { container } = render(<Badge tone="danger">Blocked</Badge>);
    expect(container.querySelector("svg")).toHaveAttribute(
      "aria-hidden",
      "true",
    );
    const textEl = screen.getByText("Blocked");
    expect(textEl).toBeInTheDocument();
    expect(textEl).toHaveAttribute("title", "Blocked");
    const badgeEl = textEl.closest(".maya-badge");
    expect(badgeEl?.className).not.toContain("leading-4");
  });

  it("StatusPill shows icon plus words for each task state", () => {
    const states = {
      pending: "Pending",
      running: "In progress",
      awaiting_approval: "Awaiting approval",
      completed: "Completed",
      failed: "Failed",
    } as const;
    for (const [status, label] of Object.entries(states)) {
      const { container, unmount } = render(
        <StatusPill status={status as keyof typeof states} />,
      );
      expect(screen.getByText(label)).toBeInTheDocument();
      expect(container.querySelector("svg")).not.toBeNull();
      unmount();
    }
  });
});

describe("Skeleton, EmptyState, ErrorState", () => {
  it("Skeleton is hidden from assistive technology and shimmers", () => {
    const { container } = render(<Skeleton lines={3} />);
    expect(container.firstElementChild).toHaveAttribute("aria-hidden", "true");
    expect(container.querySelectorAll(".motion-shimmer")).toHaveLength(3);
  });

  it("EmptyState shows a title, text and a next-step action", () => {
    render(
      <EmptyState
        title="No tasks yet"
        description="Ask MAYA to start one."
        action={<Button>New task</Button>}
      />,
    );
    expect(screen.getByText("No tasks yet")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "New task" }),
    ).toBeInTheDocument();
  });

  it("ErrorState is an alert with a retry that works", () => {
    const onRetry = vi.fn();
    render(
      <ErrorState
        title="Could not load"
        message="Check the backend."
        onRetry={onRetry}
      />,
    );
    expect(screen.getByRole("alert")).toHaveTextContent("Could not load");
    fireEvent.click(screen.getByRole("button", { name: "Try again" }));
    expect(onRetry).toHaveBeenCalled();
  });

  it("ErrorState accepts a custom retry slot", () => {
    render(<ErrorState title="x" retry={<a href="/help">Help</a>} />);
    expect(screen.getByRole("link", { name: "Help" })).toBeInTheDocument();
    expect(screen.queryByRole("button", { name: "Try again" })).toBeNull();
  });
});

describe("Notice and toasts", () => {
  it("Notice uses status or alert roles and can be dismissed", () => {
    const onDismiss = vi.fn();
    const { rerender } = render(
      <Notice title="Saved" onDismiss={onDismiss}>
        done
      </Notice>,
    );
    expect(screen.getByRole("status")).toHaveTextContent("Saved");
    fireEvent.click(screen.getByRole("button", { name: "Dismiss" }));
    expect(onDismiss).toHaveBeenCalled();
    rerender(<Notice tone="danger">bad</Notice>);
    expect(screen.getByRole("alert")).toBeInTheDocument();
  });

  it("ToastRegion sits on the toast layer, below the header, safe-area aware", () => {
    render(
      <ToastRegion
        toasts={[{ id: "1", message: "Copied" }]}
        onDismiss={() => undefined}
      />,
    );
    const region = screen.getByTestId("toast-region");
    expect(region).toHaveAttribute("aria-live", "polite");
    expect(region.className).toContain("z-maya-toast");
    expect(region.style.top).toContain("--maya-header-height");
    expect(region.style.paddingRight).toContain("--maya-safe-right");
    expect(screen.getByText("Copied")).toBeInTheDocument();
  });

  it("useToasts auto-dismisses normal toasts and keeps errors", () => {
    vi.useFakeTimers();
    const { result } = renderHook(() => useToasts());
    act(() => {
      result.current.push({ message: "ok" });
      result.current.push({ message: "bad", tone: "danger" });
    });
    expect(result.current.toasts).toHaveLength(2);
    act(() => {
      vi.advanceTimersByTime(6100);
    });
    expect(result.current.toasts.map((t) => t.message)).toEqual(["bad"]);
    act(() => result.current.dismiss(result.current.toasts[0].id));
    expect(result.current.toasts).toHaveLength(0);
  });
});

describe("computePlacement (viewport safety)", () => {
  const viewport = { width: 1000, height: 700 };
  const floating = { width: 200, height: 100 };

  it("opens on the preferred side when there is room, centred on the anchor", () => {
    const p = computePlacement({
      anchor: { top: 300, left: 400, width: 100, height: 30 },
      floating,
      viewport,
      side: "bottom",
    });
    expect(p.side).toBe("bottom");
    expect(p.top).toBe(338);
    expect(p.left).toBe(350);
  });

  it("flips when the preferred side does not fit", () => {
    const p = computePlacement({
      anchor: { top: 650, left: 400, width: 100, height: 30 },
      floating,
      viewport,
      side: "bottom",
    });
    expect(p.side).toBe("top");
    expect(p.top).toBeLessThan(650);
  });

  it("shifts to stay inside every edge", () => {
    const left = computePlacement({
      anchor: { top: 300, left: 0, width: 20, height: 20 },
      floating,
      viewport,
    });
    expect(left.left).toBe(8);
    const right = computePlacement({
      anchor: { top: 300, left: 990, width: 10, height: 20 },
      floating,
      viewport,
    });
    expect(right.left + floating.width).toBeLessThanOrEqual(1000 - 8);
  });

  it("pins to the start edge when larger than the viewport", () => {
    const p = computePlacement({
      anchor: { top: 10, left: 10, width: 10, height: 10 },
      floating: { width: 2000, height: 2000 },
      viewport,
    });
    expect(p.left).toBe(8);
    expect(p.top).toBe(8);
  });
});

describe("Popover and Tooltip", () => {
  function Harness({ initial = true }: { initial?: boolean }) {
    const [open, setOpen] = React.useState(initial);
    const ref = useRef<HTMLButtonElement>(null);
    return (
      <>
        <button ref={ref}>anchor</button>
        <Popover
          open={open}
          onOpenChange={setOpen}
          anchorRef={ref}
          label="Menu"
        >
          content
        </Popover>
      </>
    );
  }

  it("renders in a portal on the dropdown layer and closes on Escape", () => {
    render(<Harness />);
    const pop = screen.getByRole("dialog", { name: "Menu" });
    expect(pop.className).toContain("z-maya-dropdown");
    expect(pop.className).toContain("max-w-[calc(100vw");
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("closes on an outside press but not on the anchor", () => {
    render(<Harness />);
    fireEvent.pointerDown(screen.getByText("anchor"));
    expect(screen.getByRole("dialog")).toBeInTheDocument();
    fireEvent.pointerDown(document.body);
    expect(screen.queryByRole("dialog")).toBeNull();
  });

  it("Tooltip shows on hover and focus, describes the trigger, hides on Escape", () => {
    render(
      <Tooltip content="Copies the link">
        <button>Copy</button>
      </Tooltip>,
    );
    const trigger = screen.getByRole("button", { name: "Copy" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.focus(trigger);
    const tip = screen.getByRole("tooltip");
    expect(tip).toHaveTextContent("Copies the link");
    expect(trigger).toHaveAttribute("aria-describedby", tip.id);
    fireEvent.keyDown(document, { key: "Escape" });
    expect(screen.queryByRole("tooltip")).toBeNull();
    fireEvent.mouseEnter(trigger);
    expect(screen.getByRole("tooltip")).toBeInTheDocument();
    fireEvent.mouseLeave(trigger);
    expect(screen.queryByRole("tooltip")).toBeNull();
  });
});

describe("Kbd", () => {
  it("renders a kbd element", () => {
    render(<Kbd>Ctrl</Kbd>);
    expect(screen.getByText("Ctrl").tagName).toBe("KBD");
  });
});
