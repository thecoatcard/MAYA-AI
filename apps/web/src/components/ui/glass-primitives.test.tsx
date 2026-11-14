import { fireEvent, render, screen } from "@testing-library/react";
import React, { createRef } from "react";
import { describe, expect, it, vi } from "vitest";

import {
  GlassBadge,
  GlassButton,
  GlassCard,
  GlassCardContent,
  GlassCardDescription,
  GlassCardFooter,
  GlassCardHeader,
  GlassCardTitle,
  GlassInput,
  GlassModal,
  GlassModalContent,
  GlassModalDescription,
  GlassModalFooter,
  GlassModalHeader,
  GlassModalTitle,
  GlassTextarea,
} from "./index";

describe("GlassCard", () => {
  it("renders default variant with frosted backdrop and 1px border", () => {
    render(<GlassCard data-testid="card">Card Content</GlassCard>);
    const card = screen.getByTestId("card");
    expect(card).toBeInTheDocument();
    expect(card).toHaveTextContent("Card Content");
    expect(card.className).not.toContain("backdrop-blur");
    expect(card.className).toContain("border-maya-border");
  });

  it("supports variants: teal, raised, ghost, glow", () => {
    const { rerender } = render(
      <GlassCard data-testid="card" variant="teal">
        Teal Card
      </GlassCard>,
    );
    expect(screen.getByTestId("card").className).toContain(
      "border-maya-accent",
    );

    rerender(
      <GlassCard data-testid="card" variant="raised">
        Raised Card
      </GlassCard>,
    );
    expect(screen.getByTestId("card").className).toContain("bg-maya-raised");

    rerender(
      <GlassCard data-testid="card" variant="ghost">
        Ghost Card
      </GlassCard>,
    );
    expect(screen.getByTestId("card").className).toContain("bg-transparent");

    rerender(
      <GlassCard data-testid="card" variant="glow">
        Glow Card
      </GlassCard>,
    );
    expect(screen.getByTestId("card").className).toContain(
      "border-maya-accent",
    );
  });

  it("applies padding variants correctly", () => {
    const { container, rerender } = render(
      <GlassCard padding="none">No Padding</GlassCard>,
    );
    expect(container.firstChild).toHaveTextContent("No Padding");

    rerender(<GlassCard padding="sm">Small</GlassCard>);
    expect(screen.getByText("Small").className).toContain("p-3");

    rerender(<GlassCard padding="lg">Large</GlassCard>);
    expect(screen.getByText("Large").className).toContain("p-6");
  });

  it("applies hover effects: lift, glow, lift-glow", () => {
    const { rerender } = render(
      <GlassCard data-testid="card" hoverEffect="lift">
        Lift
      </GlassCard>,
    );
    expect(screen.getByTestId("card").className).toContain(
      "hover:border-maya-border-strong",
    );
    expect(screen.getByTestId("card").className).not.toContain("translate");

    rerender(
      <GlassCard data-testid="card" hoverEffect="glow">
        Glow
      </GlassCard>,
    );
    expect(screen.getByTestId("card").className).toContain(
      "hover:border-maya-accent",
    );

    rerender(
      <GlassCard data-testid="card" hoverEffect="lift-glow">
        Lift & Glow
      </GlassCard>,
    );
    expect(screen.getByTestId("card").className).toContain(
      "hover:border-maya-accent",
    );
  });

  it("renders with header and footer slots", () => {
    render(
      <GlassCard
        header={<span>Card Header</span>}
        footer={<span>Card Footer</span>}
      >
        Card Body
      </GlassCard>,
    );
    expect(screen.getByText("Card Header")).toBeInTheDocument();
    expect(screen.getByText("Card Body")).toBeInTheDocument();
    expect(screen.getByText("Card Footer")).toBeInTheDocument();
  });

  it("renders composite subcomponents: Header, Title, Description, Content, Footer", () => {
    render(
      <GlassCard>
        <GlassCardHeader>
          <GlassCardTitle>Task Execution</GlassCardTitle>
          <GlassCardDescription>
            Deterministic pipeline state
          </GlassCardDescription>
        </GlassCardHeader>
        <GlassCardContent>Main content body</GlassCardContent>
        <GlassCardFooter>
          <button type="button">Action</button>
        </GlassCardFooter>
      </GlassCard>,
    );

    expect(
      screen.getByRole("heading", { name: "Task Execution" }),
    ).toBeInTheDocument();
    expect(
      screen.getByText("Deterministic pipeline state"),
    ).toBeInTheDocument();
    expect(screen.getByText("Main content body")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Action" })).toBeInTheDocument();
  });

  it("forwards ref to div element", () => {
    const ref = createRef<HTMLDivElement>();
    render(<GlassCard ref={ref}>Ref Card</GlassCard>);
    expect(ref.current).toBeInstanceOf(HTMLDivElement);
  });
});

describe("GlassButton", () => {
  it("renders primary, secondary, outline, ghost, and danger variants", () => {
    const { rerender } = render(
      <GlassButton variant="primary">Primary</GlassButton>,
    );
    expect(screen.getByRole("button", { name: "Primary" }).className).toContain(
      "bg-maya-accent",
    );

    rerender(<GlassButton variant="secondary">Secondary</GlassButton>);
    expect(
      screen.getByRole("button", { name: "Secondary" }).className,
    ).toContain("bg-maya-raised");

    rerender(<GlassButton variant="outline">Outline</GlassButton>);
    expect(screen.getByRole("button", { name: "Outline" }).className).toContain(
      "border-maya-control-border",
    );

    rerender(<GlassButton variant="ghost">Ghost</GlassButton>);
    expect(screen.getByRole("button", { name: "Ghost" }).className).toContain(
      "border-transparent",
    );

    rerender(<GlassButton variant="danger">Danger</GlassButton>);
    expect(screen.getByRole("button", { name: "Danger" }).className).toContain(
      "bg-maya-danger-soft",
    );
  });

  it("renders sizes: sm, md, lg", () => {
    const { rerender } = render(<GlassButton size="sm">Small</GlassButton>);
    expect(screen.getByRole("button", { name: "Small" }).className).toContain(
      "h-8",
    );

    rerender(<GlassButton size="md">Medium</GlassButton>);
    expect(screen.getByRole("button", { name: "Medium" }).className).toContain(
      "h-10",
    );

    rerender(<GlassButton size="lg">Large</GlassButton>);
    expect(screen.getByRole("button", { name: "Large" }).className).toContain(
      "h-12",
    );
  });

  it("handles clicks and triggers ripple effect", () => {
    const handleClick = vi.fn();
    render(<GlassButton onClick={handleClick}>Click Me</GlassButton>);
    const button = screen.getByRole("button", { name: "Click Me" });
    fireEvent.click(button);
    expect(handleClick).toHaveBeenCalledTimes(1);
  });

  it("disables ripple when ripple={false}", () => {
    const handleClick = vi.fn();
    render(
      <GlassButton ripple={false} onClick={handleClick}>
        No Ripple
      </GlassButton>,
    );
    const button = screen.getByRole("button", { name: "No Ripple" });
    fireEvent.click(button);
    expect(handleClick).toHaveBeenCalledTimes(1);
    expect(button.querySelector(".animate-ping")).toBeNull();
  });

  it("handles disabled state", () => {
    const handleClick = vi.fn();
    render(
      <GlassButton disabled onClick={handleClick}>
        Disabled
      </GlassButton>,
    );
    const button = screen.getByRole("button", { name: "Disabled" });
    expect(button).toBeDisabled();
    fireEvent.click(button);
    expect(handleClick).not.toHaveBeenCalled();
  });

  it("renders loading state with spinner and disables button", () => {
    const handleClick = vi.fn();
    render(
      <GlassButton loading loadingText="Processing..." onClick={handleClick}>
        Submit
      </GlassButton>,
    );
    const button = screen.getByRole("button");
    expect(button).toBeDisabled();
    expect(button).toHaveAttribute("aria-busy", "true");
    expect(screen.getByText("Processing...")).toBeInTheDocument();
    expect(button.querySelector("svg")?.getAttribute("class")).toContain(
      "motion-reduce:animate-none",
    );
    fireEvent.click(button);
    expect(handleClick).not.toHaveBeenCalled();
  });

  it("renders left and right icons", () => {
    render(
      <GlassButton
        leftIcon={<span data-testid="left-icon">Left</span>}
        rightIcon={<span data-testid="right-icon">Right</span>}
      >
        With Icons
      </GlassButton>,
    );
    expect(screen.getByTestId("left-icon")).toBeInTheDocument();
    expect(screen.getByTestId("right-icon")).toBeInTheDocument();
  });

  it("forwards ref to button element", () => {
    const ref = createRef<HTMLButtonElement>();
    render(<GlassButton ref={ref}>Ref Button</GlassButton>);
    expect(ref.current).toBeInstanceOf(HTMLButtonElement);
  });
});

describe("GlassBadge", () => {
  it("renders color variants: teal, emerald, amber, rose, blue, zinc, purple", () => {
    const { rerender } = render(<GlassBadge variant="teal">Teal</GlassBadge>);
    expect(screen.getByText("Teal").className).toContain("text-maya-accent");

    rerender(<GlassBadge variant="emerald">Emerald</GlassBadge>);
    expect(screen.getByText("Emerald").className).toContain(
      "text-maya-success",
    );

    rerender(<GlassBadge variant="amber">Amber</GlassBadge>);
    expect(screen.getByText("Amber").className).toContain("text-maya-warning");

    rerender(<GlassBadge variant="rose">Rose</GlassBadge>);
    expect(screen.getByText("Rose").className).toContain("text-maya-danger");

    rerender(<GlassBadge variant="blue">Blue</GlassBadge>);
    expect(screen.getByText("Blue").className).toContain("text-maya-info");

    rerender(<GlassBadge variant="zinc">Zinc</GlassBadge>);
    expect(screen.getByText("Zinc").className).toContain("text-maya-muted");

    rerender(<GlassBadge variant="purple">Purple</GlassBadge>);
    expect(screen.getByText("Purple").className).toContain("text-maya-text");
  });

  it("renders dot indicator and supports pulse", () => {
    const { container, rerender } = render(
      <GlassBadge variant="teal" dot>
        Active
      </GlassBadge>,
    );
    const dot = container.querySelector('[aria-hidden="true"]');
    expect(dot).toBeInTheDocument();
    expect(dot?.className).toContain("bg-maya-accent");

    rerender(
      <GlassBadge variant="teal" dot dotPulse>
        Active
      </GlassBadge>,
    );
    const pulsingDot = container.querySelector('[aria-hidden="true"]');
    expect(pulsingDot?.className).toContain("animate-pulse");
  });

  it("applies monospace font styling when mono=true", () => {
    render(<GlassBadge mono>0x7F9B</GlassBadge>);
    const badge = screen.getByText("0x7F9B");
    expect(badge.className).toContain("font-mono");
  });

  it("renders sizes: sm, md, lg", () => {
    const { rerender } = render(<GlassBadge size="sm">Small</GlassBadge>);
    expect(screen.getByText("Small").className).toContain("text-maya-caption");

    rerender(<GlassBadge size="md">Medium</GlassBadge>);
    expect(screen.getByText("Medium").className).toContain("text-maya-caption");

    rerender(<GlassBadge size="lg">Large</GlassBadge>);
    expect(screen.getByText("Large").className).toContain("md:text-sm");
  });

  it("renders icon in badge", () => {
    render(
      <GlassBadge icon={<span data-testid="badge-icon">★</span>}>
        Featured
      </GlassBadge>,
    );
    expect(screen.getByTestId("badge-icon")).toBeInTheDocument();
  });

  it("forwards ref to span element", () => {
    const ref = createRef<HTMLSpanElement>();
    render(<GlassBadge ref={ref}>Ref Badge</GlassBadge>);
    expect(ref.current).toBeInstanceOf(HTMLSpanElement);
  });
});

describe("GlassInput & GlassTextarea", () => {
  it("renders input with label and helper text", () => {
    render(
      <GlassInput
        id="test-input"
        label="Repository URL"
        helperText="Git SSH or HTTPS clone URL"
        placeholder="https://github.com/..."
      />,
    );
    expect(screen.getByLabelText("Repository URL")).toBeInTheDocument();
    expect(screen.getByText("Git SSH or HTTPS clone URL")).toBeInTheDocument();
    expect(
      screen.getByPlaceholderText("https://github.com/..."),
    ).toBeInTheDocument();
  });

  it("displays error state with alert role and aria-invalid", () => {
    render(
      <GlassInput
        id="err-input"
        label="API Key"
        error="Invalid token format"
      />,
    );
    const input = screen.getByLabelText("API Key");
    expect(input).toHaveAttribute("aria-invalid", "true");
    const alert = screen.getByRole("alert");
    expect(alert).toHaveTextContent("Invalid token format");
  });

  it("handles left and right icons", () => {
    render(
      <GlassInput
        leftIcon={<span data-testid="left-icon">Search</span>}
        rightIcon={<span data-testid="right-icon">Clear</span>}
      />,
    );
    expect(screen.getByTestId("left-icon")).toBeInTheDocument();
    expect(screen.getByTestId("right-icon")).toBeInTheDocument();
  });

  it("handles clearable button and calls onClear", () => {
    const handleClear = vi.fn();
    render(
      <GlassInput
        value="current text"
        clearable
        onClear={handleClear}
        onChange={() => {}}
      />,
    );
    const clearBtn = screen.getByRole("button", { name: "Clear input" });
    fireEvent.click(clearBtn);
    expect(handleClear).toHaveBeenCalledTimes(1);
  });

  it("disables input when disabled={true}", () => {
    render(<GlassInput id="disabled-input" label="Disabled Input" disabled />);
    expect(screen.getByLabelText("Disabled Input")).toBeDisabled();
  });

  it("renders GlassTextarea with multiline support and error", () => {
    render(
      <GlassTextarea
        id="test-desc"
        label="Prompt"
        rows={5}
        error="Prompt is required"
      />,
    );
    const textarea = screen.getByLabelText("Prompt");
    expect(textarea).toBeInTheDocument();
    expect(textarea).toHaveAttribute("aria-invalid", "true");
    expect(screen.getByRole("alert")).toHaveTextContent("Prompt is required");
  });

  it("forwards refs to input and textarea elements", () => {
    const inputRef = createRef<HTMLInputElement>();
    const textareaRef = createRef<HTMLTextAreaElement>();
    render(
      <>
        <GlassInput ref={inputRef} />
        <GlassTextarea ref={textareaRef} />
      </>,
    );
    expect(inputRef.current).toBeInstanceOf(HTMLInputElement);
    expect(textareaRef.current).toBeInstanceOf(HTMLTextAreaElement);
  });
});

describe("GlassModal", () => {
  it("renders nothing when isOpen is false", () => {
    render(
      <GlassModal isOpen={false} onClose={() => {}}>
        Modal Content
      </GlassModal>,
    );
    expect(screen.queryByRole("dialog")).not.toBeInTheDocument();
  });

  it("renders accessible dialog with backdrop and title when isOpen is true", () => {
    render(
      <GlassModal
        isOpen={true}
        onClose={() => {}}
        title="Confirm Execution"
        description="Verify the SHA-256 digest"
      >
        <p>Modal Body</p>
      </GlassModal>,
    );

    const dialog = screen.getByRole("dialog");
    expect(dialog).toBeInTheDocument();
    expect(dialog).toHaveAttribute("aria-modal", "true");
    expect(screen.getByText("Confirm Execution")).toBeInTheDocument();
    expect(screen.getByText("Verify the SHA-256 digest")).toBeInTheDocument();
    expect(screen.getByText("Modal Body")).toBeInTheDocument();
    expect(dialog.className).not.toContain("backdrop-blur");
  });

  it("invokes onClose when clicking close button", () => {
    const handleClose = vi.fn();
    render(
      <GlassModal isOpen={true} onClose={handleClose} title="Modal Title">
        Content
      </GlassModal>,
    );
    const closeBtn = screen.getByRole("button", { name: "Close modal" });
    fireEvent.click(closeBtn);
    expect(handleClose).toHaveBeenCalledTimes(1);
  });

  it("invokes onClose when pressing Escape key", () => {
    const handleClose = vi.fn();
    render(
      <GlassModal isOpen={true} onClose={handleClose}>
        Content
      </GlassModal>,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(handleClose).toHaveBeenCalledTimes(1);
  });

  it("does not close on Escape if closeOnEsc is false", () => {
    const handleClose = vi.fn();
    render(
      <GlassModal isOpen={true} onClose={handleClose} closeOnEsc={false}>
        Content
      </GlassModal>,
    );
    fireEvent.keyDown(window, { key: "Escape" });
    expect(handleClose).not.toHaveBeenCalled();
  });

  it("invokes onClose when clicking outer backdrop", () => {
    const handleClose = vi.fn();
    render(
      <GlassModal isOpen={true} onClose={handleClose}>
        Content
      </GlassModal>,
    );
    const backdrop = screen.getByRole("dialog");
    fireEvent.click(backdrop);
    expect(handleClose).toHaveBeenCalledTimes(1);
  });

  it("does not close on backdrop click when closeOnBackdropClick is false", () => {
    const handleClose = vi.fn();
    render(
      <GlassModal
        isOpen={true}
        onClose={handleClose}
        closeOnBackdropClick={false}
      >
        Content
      </GlassModal>,
    );
    const backdrop = screen.getByRole("dialog");
    fireEvent.click(backdrop);
    expect(handleClose).not.toHaveBeenCalled();
  });

  it("renders composite subcomponents: Header, Title, Description, Content, Footer", () => {
    render(
      <GlassModal isOpen={true} onClose={() => {}}>
        <GlassModalHeader>
          <GlassModalTitle>Step-Up TOTP</GlassModalTitle>
          <GlassModalDescription>High security action</GlassModalDescription>
        </GlassModalHeader>
        <GlassModalContent>
          <input placeholder="Enter TOTP" />
        </GlassModalContent>
        <GlassModalFooter>
          <button type="button">Submit</button>
        </GlassModalFooter>
      </GlassModal>,
    );

    expect(
      screen.getByRole("heading", { name: "Step-Up TOTP" }),
    ).toBeInTheDocument();
    expect(screen.getByText("High security action")).toBeInTheDocument();
    expect(screen.getByPlaceholderText("Enter TOTP")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Submit" })).toBeInTheDocument();
  });

  it("supports focus trapping navigation with Tab and Shift+Tab", () => {
    render(
      <GlassModal isOpen={true} onClose={() => {}}>
        <button type="button">First</button>
        <button type="button">Second</button>
      </GlassModal>,
    );

    const firstBtn = screen.getByRole("button", { name: "First" });
    const secondBtn = screen.getByRole("button", { name: "Second" });
    const dialog = screen.getByRole("dialog");

    // Focus last button and press Tab without Shift -> wraps to first
    secondBtn.focus();
    expect(document.activeElement).toBe(secondBtn);
    fireEvent.keyDown(window, { key: "Tab", shiftKey: false });

    // Focus first button and press Shift+Tab -> wraps to last
    firstBtn.focus();
    expect(document.activeElement).toBe(firstBtn);
    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });

    // Focus container and press Shift+Tab -> wraps to last
    const container = dialog.querySelector<HTMLElement>(".maya-sheet")!;
    container.focus();
    expect(document.activeElement).toBe(container);
    fireEvent.keyDown(window, { key: "Tab", shiftKey: true });
    expect(document.activeElement).toBe(secondBtn);
  });

  it("does not steal focus if a child element is already focused", async () => {
    function ChildFocusingModal() {
      const cancelRef = React.useRef<HTMLButtonElement>(null);
      React.useEffect(() => {
        cancelRef.current?.focus();
      }, []);
      return (
        <GlassModal isOpen={true} onClose={() => {}}>
          <button type="button">First</button>
          <button type="button" ref={cancelRef}>
            Cancel
          </button>
        </GlassModal>
      );
    }

    render(<ChildFocusingModal />);
    const cancelBtn = screen.getByRole("button", { name: "Cancel" });
    expect(document.activeElement).toBe(cancelBtn);

    // Wait for animation frame
    await new Promise((resolve) => requestAnimationFrame(resolve));
    expect(document.activeElement).toBe(cancelBtn);
  });
});
