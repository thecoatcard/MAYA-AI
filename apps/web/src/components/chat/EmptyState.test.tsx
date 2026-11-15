import { act, fireEvent, render, screen } from "@testing-library/react";
import { readFileSync } from "node:fs";
import { dirname, resolve } from "node:path";
import { fileURLToPath } from "node:url";
import React from "react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ConversationContext,
  type ConversationRuntimeValue,
} from "./ConversationContext";
import { EMPTY_SUGGESTIONS } from "./ConversationView";
import { LIVING_DOT_COUNT, makeDots } from "./LivingBackdrop";
import type { ThreadItem } from "./threadItems";

vi.mock("../../hooks/useTaskEvents", () => ({
  useTaskEvents: () => ({ taskDetail: null, error: null }),
}));

import { ConversationView } from "./ConversationView";

const here = dirname(fileURLToPath(import.meta.url));

function runtime(items: ThreadItem[] = [], state = "idle") {
  return {
    chat: {
      status: "idle",
      isBusy: false,
      error: null,
      retrying: null,
      canRetry: false,
      canAskAgain: false,
      stop: vi.fn(),
      sessionId: null,
    },
    voice: {
      state,
      transcripts: [],
      unavailableReason: null,
      error: null,
      interruptPlayback: vi.fn(),
    },
    voiceLive: state === "listening",
    items,
    toggleVoice: vi.fn(),
    sendText: vi.fn(async () => undefined),
    runAsTask: vi.fn(async () => undefined),
    trackTask: vi.fn(),
    refreshApprovals: vi.fn(async () => undefined),
    historyError: null,
    historyLoading: false,
    title: "New conversation",
  } as unknown as ConversationRuntimeValue;
}

function mount(value: ConversationRuntimeValue) {
  return render(
    <ConversationContext.Provider value={value}>
      <ConversationView />
    </ConversationContext.Provider>,
  );
}

const makeCtx = () => ({
  setTransform: vi.fn(),
  clearRect: vi.fn(),
  save: vi.fn(),
  restore: vi.fn(),
  translate: vi.fn(),
  rotate: vi.fn(),
  beginPath: vi.fn(),
  ellipse: vi.fn(),
  stroke: vi.fn(),
  arc: vi.fn(),
  fill: vi.fn(),
  createRadialGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
  createLinearGradient: vi.fn(() => ({ addColorStop: vi.fn() })),
});
let ctx = makeCtx();

let reduced = false;
let rafCalls: number;
let cancelCalls: number;

beforeEach(() => {
  reduced = false;
  rafCalls = 0;
  cancelCalls = 0;
  ctx = makeCtx();
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue(
    ctx as unknown as CanvasRenderingContext2D,
  );
  vi.stubGlobal(
    "matchMedia",
    (query: string) =>
      ({
        matches: reduced && query.includes("reduced-motion"),
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }) as unknown as MediaQueryList,
  );
  vi.stubGlobal("requestAnimationFrame", () => {
    rafCalls += 1;
    return rafCalls;
  });
  vi.stubGlobal("cancelAnimationFrame", () => {
    cancelCalls += 1;
  });
});

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

describe("empty conversation", () => {
  it("renders the heading, hint, decorative canvas and four suggestions", () => {
    mount(runtime());
    expect(
      screen.getByRole("heading", { name: "Start a conversation" }),
    ).toBeTruthy();
    const canvas = screen.getByTestId("living-backdrop");
    expect(canvas.getAttribute("aria-hidden")).toBe("true");
    const chipButtons = screen
      .getAllByRole("button")
      .filter((b) => b.className.includes("chip"));
    expect(chipButtons).toHaveLength(4);
    expect(EMPTY_SUGGESTIONS).toHaveLength(4);
    const expectedLabels = [
      "Make a 10-page PDF",
      "Research and summarise",
      "Write notes in Notion",
      "Check my mail",
    ];
    for (const label of expectedLabels) {
      expect(screen.getByRole("button", { name: label })).toBeTruthy();
    }
    const chipsList = screen.getByRole("list", { name: "Suggestions" });
    expect(chipsList.className).toContain("motion-stagger");
    expect(rafCalls).toBe(1);
  });

  it("unmounts the backdrop and cancels the loop when messages arrive", () => {
    const view = mount(runtime());
    expect(screen.queryByTestId("living-backdrop")).not.toBeNull();
    const message: ThreadItem = {
      kind: "message",
      id: "m1",
      ts: 1,
      message: { id: "m1", role: "user", text: "hi", state: "done" },
    } as unknown as ThreadItem;
    view.rerender(
      <ConversationContext.Provider value={runtime([message])}>
        <ConversationView />
      </ConversationContext.Provider>,
    );
    expect(screen.queryByTestId("living-backdrop")).toBeNull();
    expect(cancelCalls).toBeGreaterThan(0);
  });

  it("draws one static frame and never starts the loop under reduced motion", () => {
    reduced = true;
    mount(runtime());
    expect(rafCalls).toBe(0);
    expect(ctx.clearRect).toHaveBeenCalledTimes(1);
  });

  it("pauses the loop when the tab is hidden and resumes when visible", () => {
    mount(runtime());
    expect(rafCalls).toBe(1);
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: true,
    });
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(cancelCalls).toBe(1);
    Object.defineProperty(document, "hidden", {
      configurable: true,
      value: false,
    });
    act(() => {
      document.dispatchEvent(new Event("visibilitychange"));
    });
    expect(rafCalls).toBe(2);
  });

  it("a suggestion fills the draft, focuses the composer and does not send", async () => {
    vi.useFakeTimers();
    const value = runtime();
    mount(value);
    fireEvent.click(screen.getByRole("button", { name: "Check my mail" }));
    act(() => {
      vi.runAllTimers();
    });
    vi.useRealTimers();
    const box = screen.getByRole("textbox", {
      name: "Message",
    }) as HTMLTextAreaElement;
    expect(box.value).toBe("Check my mail for ");
    expect(document.activeElement).toBe(box);
    expect(value.sendText).not.toHaveBeenCalled();
    expect(value.runAsTask).not.toHaveBeenCalled();
    // Typing has started: the animation is gone, the suggestions stay.
    expect(screen.queryByTestId("living-backdrop")).toBeNull();
    expect(screen.getByRole("button", { name: "Check my mail" })).toBeTruthy();
  });

  it("drops the backdrop while voice is live", () => {
    mount(runtime([], "listening"));
    expect(screen.queryByTestId("living-backdrop")).toBeNull();
  });

  it("bounds the particle field", () => {
    expect(makeDots(10_000)).toHaveLength(LIVING_DOT_COUNT);
    expect(LIVING_DOT_COUNT).toBeLessThanOrEqual(40);
    for (const dot of makeDots()) {
      // One lap takes at least a minute: the drift is slow.
      expect((Math.PI * 2) / Math.abs(dot.speed)).toBeGreaterThanOrEqual(60);
    }
  });

  it("responds to pointer movement and resets when pointer leaves window", () => {
    mount(runtime());
    act(() => {
      window.dispatchEvent(
        new MouseEvent("pointermove", { clientX: 200, clientY: 150 }),
      );
      document.dispatchEvent(new MouseEvent("pointerleave"));
    });
    expect(screen.getByTestId("living-backdrop")).toBeTruthy();
  });
});

describe("EmptyState.css rules", () => {
  const css = readFileSync(resolve(here, "EmptyState.css"), "utf8").replace(
    /\/\*[\s\S]*?\*\//g,
    "",
  );
  const globals = readFileSync(resolve(here, "../../app/globals.css"), "utf8");

  it("has no CSS animation loop, keyframes, raw colour or raw duration", () => {
    expect(css).not.toMatch(/infinite|@keyframes|animation/);
    expect(css).not.toMatch(/#[0-9a-fA-F]{3,8}\b|\brgba?\(|\bhsla?\(/);
    expect(css).not.toMatch(/\d\s?m?s\b(?![\w-])/);
    expect(css).not.toMatch(/cubic-bezier\(/);
  });

  it("only reads tokens that exist and is pointer-transparent decoration", () => {
    const used = new Set(
      Array.from(css.matchAll(/var\((--maya-[a-z0-9-]+)/g)).map((m) => m[1]),
    );
    expect([...used].filter((n) => !globals.includes(`${n}:`))).toEqual([]);
    expect(css).toMatch(/\.maya-living\s*\{[^}]*pointer-events:\s*none/);
  });

  it("the canvas sits behind the text and the stagger kit has a reduced-motion override", () => {
    expect(css).toMatch(/\.maya-living\s*\{[^}]*z-index:\s*-1/);
    expect(
      globals +
        readFileSync(resolve(here, "../../app/ui-surfaces.css"), "utf8"),
    ).toContain(".motion-stagger > *");
  });
});
