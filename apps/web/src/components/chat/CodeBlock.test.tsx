import React from "react";
import { act, fireEvent, render, screen } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { CodeBlock } from "./CodeBlock";

const RLO = String.fromCharCode(0x202e);
const LRI = String.fromCharCode(0x2066);
const PDI = String.fromCharCode(0x2069);

function stubClipboard(writeText: (text: string) => Promise<void>) {
  Object.defineProperty(navigator, "clipboard", {
    value: { writeText },
    configurable: true,
  });
}

beforeEach(() => {
  vi.useFakeTimers();
});

afterEach(() => {
  vi.useRealTimers();
  Reflect.deleteProperty(navigator, "clipboard");
});

describe("CodeBlock", () => {
  it("shows the language label and the code", () => {
    const { container } = render(
      <CodeBlock code={"const a = 1;"} language="ts" />,
    );
    expect(container.querySelector(".maya-code__lang")?.textContent).toBe("ts");
    expect(container.querySelector("pre code")?.textContent).toBe(
      "const a = 1;",
    );
    expect(screen.getByRole("figure", { name: "ts code" })).toBeInTheDocument();
  });

  it("highlights known languages and leaves unknown ones plain", () => {
    const known = render(<CodeBlock code="const a = 1;" language="ts" />);
    expect(
      known.container.querySelector(".maya-tok--keyword")?.textContent,
    ).toBe("const");
    known.unmount();
    const unknown = render(<CodeBlock code="const a = 1;" language="rust" />);
    expect(unknown.container.querySelector(".maya-tok")).toBeNull();
    expect(unknown.container.querySelector("pre code")?.textContent).toBe(
      "const a = 1;",
    );
    unknown.unmount();
    const none = render(<CodeBlock code="plain" />);
    expect(none.container.querySelector(".maya-code__lang")?.textContent).toBe(
      "text",
    );
  });

  it("never turns code into markup", () => {
    const code = '<img src=x onerror="alert(1)"><script>alert(1)</script>';
    const { container } = render(<CodeBlock code={code} language="html" />);
    expect(container.querySelector("img, script")).toBeNull();
    expect(container.querySelector("pre code")?.textContent).toBe(code);
  });

  it("makes the code region keyboard focusable so long lines can be scrolled", () => {
    const { container } = render(<CodeBlock code="x" />);
    expect(container.querySelector("pre")?.getAttribute("tabindex")).toBe("0");
  });

  it("copies the code and announces it, then returns to the idle state", async () => {
    const writeText = vi.fn(async () => undefined);
    stubClipboard(writeText);
    render(<CodeBlock code={"npm test\n"} language="bash" />);
    const button = screen.getByRole("button", { name: "Copy code" });
    await act(async () => {
      fireEvent.click(button);
    });
    expect(writeText).toHaveBeenCalledWith("npm test\n");
    expect(screen.getByRole("button", { name: "Code copied" })).toHaveAttribute(
      "data-state",
      "copied",
    );
    expect(screen.getByRole("status")).toHaveTextContent(
      "Code copied to clipboard.",
    );
    await act(async () => {
      vi.advanceTimersByTime(2100);
    });
    expect(
      screen.getByRole("button", { name: "Copy code" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("");
  });

  it("reports a failed copy instead of pretending it worked", async () => {
    stubClipboard(async () => {
      throw new Error("denied");
    });
    render(<CodeBlock code="secret" language="bash" />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
    });
    expect(
      screen.getByRole("button", { name: "Copy failed" }),
    ).toBeInTheDocument();
    expect(screen.getByRole("status")).toHaveTextContent("Could not copy");
  });

  it("reports a failed copy when the clipboard API is missing", async () => {
    render(<CodeBlock code="x" />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
    });
    expect(
      screen.getByRole("button", { name: "Copy failed" }),
    ).toBeInTheDocument();
  });

  it("does not update state after unmount (timer is cleared)", async () => {
    stubClipboard(async () => undefined);
    const { unmount } = render(<CodeBlock code="x" />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
    });
    unmount();
    expect(vi.getTimerCount()).toBe(0);
  });

  it("starts no timer and sets no state when the copy finishes after unmount", async () => {
    const errorSpy = vi
      .spyOn(console, "error")
      .mockImplementation(() => undefined);
    let finish!: () => void;
    stubClipboard(
      () =>
        new Promise<void>((resolve) => {
          finish = resolve;
        }),
    );
    const { unmount } = render(<CodeBlock code="x" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
    unmount();
    await act(async () => {
      finish();
    });
    // The block is gone: no feedback timer may be left behind.
    expect(vi.getTimerCount()).toBe(0);
    expect(errorSpy).not.toHaveBeenCalled();
    errorSpy.mockRestore();
  });

  it("starts no timer when the copy fails after unmount", async () => {
    let fail!: () => void;
    stubClipboard(
      () =>
        new Promise<void>((_resolve, reject) => {
          fail = () => reject(new Error("denied"));
        }),
    );
    const { unmount } = render(<CodeBlock code="x" />);
    fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
    unmount();
    await act(async () => {
      fail();
    });
    expect(vi.getTimerCount()).toBe(0);
  });

  it("still gives feedback when it stays mounted", async () => {
    stubClipboard(async () => undefined);
    render(<CodeBlock code="x" />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
    });
    expect(vi.getTimerCount()).toBe(1);
    expect(screen.getByRole("button", { name: "Code copied" })).toBeVisible();
  });

  it("names the focusable code region (the shared CodeBlockShell)", () => {
    render(<CodeBlock code="x" language="ts" />);
    const region = screen.getByRole("region", { name: "Code listing, ts" });
    expect(region.tagName).toBe("PRE");
    expect(region).toHaveAttribute("tabindex", "0");
    // No inline style: the height cap is a CSS rule, so rendered replies carry no style attribute.
    expect(region).not.toHaveAttribute("style");
  });

  it("falls back to a plain label for the region when there is no language", () => {
    render(<CodeBlock code="x" />);
    expect(
      screen.getByRole("region", { name: "Code listing, text" }),
    ).toBeInTheDocument();
  });

  it("scrolls long lines by default and wraps them when Wrap is pressed", () => {
    render(<CodeBlock code={"x".repeat(400)} language="ts" />);
    const region = screen.getByRole("region", { name: "Code listing, ts" });
    const wrap = screen.getByRole("button", { name: "Wrap" });
    expect(region).toHaveAttribute("data-wrap", "false");
    expect(wrap).toHaveAttribute("aria-pressed", "false");
    fireEvent.click(wrap);
    expect(region).toHaveAttribute("data-wrap", "true");
    expect(wrap).toHaveAttribute("aria-pressed", "true");
    fireEvent.click(wrap);
    expect(region).toHaveAttribute("data-wrap", "false");
  });

  it("keeps the code reading left to right whatever the page direction", () => {
    render(<CodeBlock code="x" language="ts" />);
    expect(
      screen.getByRole("region", { name: "Code listing, ts" }),
    ).toHaveAttribute("dir", "ltr");
  });
});

describe("CodeBlock direction controls", () => {
  const trojan = `if (isAdmin) {${RLO} /* check */ } ${LRI}hidden${PDI}`;

  it("shows direction controls as visible markers instead of obeying them", () => {
    const { container } = render(<CodeBlock code={trojan} language="ts" />);
    const text = container.querySelector("pre code")?.textContent ?? "";
    expect(text).toContain("<U+202E>");
    expect(text).toContain("<U+2066>");
    expect(text).toContain("<U+2069>");
    expect(text).not.toContain(RLO);
    expect(text).not.toContain(LRI);
    expect(text).not.toContain(PDI);
  });

  it("copies the marked text, never the hidden characters", async () => {
    const writeText = vi.fn(async () => undefined);
    stubClipboard(writeText);
    render(<CodeBlock code={`a${RLO}b`} />);
    await act(async () => {
      fireEvent.click(screen.getByRole("button", { name: "Copy code" }));
    });
    expect(writeText).toHaveBeenCalledWith("a<U+202E>b");
  });

  it("leaves ordinary code, including non-ASCII letters, unchanged", () => {
    const code = "const name = 'caf" + String.fromCharCode(0xe9) + "';";
    const { container } = render(<CodeBlock code={code} language="ts" />);
    expect(container.querySelector("pre code")?.textContent).toBe(code);
  });
});

describe("CodeBlock context menu", () => {
  it("offers Copy code, Save as file and a Wrap toggle", () => {
    render(<CodeBlock code={"let a = 1;"} language="js" />);
    fireEvent.contextMenu(screen.getByRole("figure"));
    expect(screen.getByRole("menuitem", { name: /Copy code/ })).toBeTruthy();
    expect(screen.getByRole("menuitem", { name: /Save as file/ })).toBeTruthy();
    const wrap = screen.getByRole("menuitemcheckbox", { name: /Wrap lines/ });
    expect(wrap).toHaveAttribute("aria-checked", "false");
    fireEvent.click(wrap);
    expect(screen.getByRole("button", { name: /Wrap/ })).toHaveAttribute(
      "aria-pressed",
      "true",
    );
  });
});
