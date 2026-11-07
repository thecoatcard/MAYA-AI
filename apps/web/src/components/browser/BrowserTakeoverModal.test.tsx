import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";
import { BrowserTakeoverModal } from "./BrowserTakeoverModal";

describe("BrowserTakeoverModal", () => {
  const defaultProps = {
    isOpen: true,
    onClose: vi.fn(),
    sessionId: "sess-12345-67890",
    url: "https://example.com/protected-page",
    botType: "cloudflare_turnstile",
    screenshotBase64: null,
    onResume: vi.fn().mockResolvedValue(undefined),
    onCancel: vi.fn().mockResolvedValue(undefined),
  };

  it("renders challenge details and anti-bypass policy warning", () => {
    render(<BrowserTakeoverModal {...defaultProps} />);

    expect(
      screen.getByText(/Human Takeover Required — Bot Challenge Detected/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText(/Anti-Bypass Policy & Security Invariant/i),
    ).toBeInTheDocument();
    expect(
      screen.getByText("https://example.com/protected-page"),
    ).toBeInTheDocument();
    expect(screen.getByText("CLOUDFLARE TURNSTILE")).toBeInTheDocument();
  });

  it("calls onResume when user clicks resume button", async () => {
    const onResume = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(
      <BrowserTakeoverModal
        {...defaultProps}
        onResume={onResume}
        onClose={onClose}
      />,
    );

    const resumeBtn = screen.getByRole("button", {
      name: /I Have Solved the Challenge — Resume/i,
    });
    fireEvent.click(resumeBtn);

    expect(onResume).toHaveBeenCalledWith("sess-12345-67890");
  });

  it("calls onCancel when user clicks cancel button", async () => {
    const onCancel = vi.fn().mockResolvedValue(undefined);
    const onClose = vi.fn();
    render(
      <BrowserTakeoverModal
        {...defaultProps}
        onCancel={onCancel}
        onClose={onClose}
      />,
    );

    const cancelBtn = screen.getByRole("button", {
      name: /Cancel Automation/i,
    });
    fireEvent.click(cancelBtn);

    expect(onCancel).toHaveBeenCalledWith("sess-12345-67890");
  });
});
