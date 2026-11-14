import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import { DesignSystemShowcase } from "./DesignSystemShowcase";

describe("DesignSystemShowcase", () => {
  it("renders every section and every primitive state", () => {
    const { container } = render(<DesignSystemShowcase />);
    for (const heading of [
      "Colour tokens",
      "Type scale, spacing, radii, layers",
      "Buttons",
      "Badges and status pills (icon plus text)",
      "Loading, empty, error, notices",
      "Long content (nothing may widen the page)",
      "Motion kit (static under reduced motion)",
    ]) {
      expect(
        screen.getByRole("heading", { name: heading }),
      ).toBeInTheDocument();
    }
    expect(
      screen.getAllByRole("button", { name: /Loading/ }).length,
    ).toBeGreaterThan(0);
    expect(screen.getByText("No tasks yet")).toBeInTheDocument();
    expect(screen.getByText("Could not load tasks")).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Try again" }),
    ).toBeInTheDocument();
    expect(container.querySelectorAll(".motion-orb")).toHaveLength(4);
    expect(container.querySelectorAll("[data-status]")).toHaveLength(5);
    expect(container.querySelector(".motion-none")).not.toBeNull();
    expect(
      screen.getByRole("button", { name: "Switch to light theme" }),
    ).toBeInTheDocument();
  });

  it("keeps long content inside scroll or wrap containers", () => {
    render(<DesignSystemShowcase />);
    expect(
      screen.getByRole("region", { name: "Sample payload" }).className,
    ).toContain("overflow-auto");
    expect(
      screen.getByRole("region", { name: "Sample table" }).className,
    ).toContain("overflow-x-auto");
    expect(
      screen.getByRole("region", { name: "Pane body" }).className,
    ).toContain("overflow-auto");
  });
});
