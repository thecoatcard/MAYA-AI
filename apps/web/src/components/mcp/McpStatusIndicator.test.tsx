import { fireEvent, render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { McpStatusIndicator } from "./McpStatusIndicator";

describe("McpStatusIndicator", () => {
  it("renders healthy status with tool and server count", () => {
    render(
      <McpStatusIndicator
        healthStatus="healthy"
        activeServersCount={3}
        totalToolsCount={15}
      />,
    );

    const statusBtn = screen.getByRole("status");
    expect(statusBtn).toBeDefined();
    expect(statusBtn.getAttribute("aria-label")).toBe("MCP Gateway: Healthy");
    expect(screen.getByText("3 srv")).toBeDefined();
    expect(screen.getByText("15 tools")).toBeDefined();
  });

  it("renders degraded status when health is degraded", () => {
    render(
      <McpStatusIndicator
        healthStatus="degraded"
        activeServersCount={2}
        totalToolsCount={8}
      />,
    );

    const statusBtn = screen.getByRole("status");
    expect(statusBtn.getAttribute("aria-label")).toBe("MCP Gateway: Degraded");
  });

  it("calls onClick when clicked", () => {
    const handleClick = vi.fn();
    render(<McpStatusIndicator onClick={handleClick} />);

    fireEvent.click(screen.getByRole("status"));
    expect(handleClick).toHaveBeenCalledTimes(1);
  });
});
