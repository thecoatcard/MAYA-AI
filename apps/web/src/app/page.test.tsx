import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Home from "./page";

describe("MAYA landing page", () => {
  it("shows the honest available and planned capabilities", () => {
    render(<Home />);
    expect(
      screen.getByRole("heading", {
        name: "A digital employee that asks before it acts.",
      }),
    ).toBeInTheDocument();
    expect(screen.getAllByText("Available")).toHaveLength(2);
    expect(screen.getAllByText("Planned")).toHaveLength(2);
    expect(screen.getByText("Agent tasks with approvals")).toBeInTheDocument();
    expect(screen.getByText("Voice workspace")).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(
      screen.queryByText(/testimonials|active users|saved hours/i),
    ).toBeNull();
  });

  it("provides sign-in and create-account actions", () => {
    render(<Home />);
    expect(screen.getAllByRole("link", { name: "Sign in" })[0]).toHaveAttribute(
      "href",
      "/login",
    );
    expect(
      screen.getAllByRole("link", { name: "Create account" })[0],
    ).toHaveAttribute("href", "/register");
  });
});
