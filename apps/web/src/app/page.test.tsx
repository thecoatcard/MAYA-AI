import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it } from "vitest";

import Home from "./page";

describe("foundation page", () => {
  it("shows the foundation without presenting planned features as available", () => {
    render(<Home />);
    expect(
      screen.getByRole("heading", {
        name: "A foundation for what comes next.",
      }),
    ).toBeInTheDocument();
    expect(screen.getByText("Foundation only")).toBeInTheDocument();
    expect(screen.getAllByText("Planned")).toHaveLength(3);
    expect(screen.queryByRole("textbox")).not.toBeInTheDocument();
    expect(
      screen.queryByRole("button", { name: /sign in|send|record/i }),
    ).not.toBeInTheDocument();
  });

  it("provides navigation to existing sections", () => {
    render(<Home />);
    expect(screen.getByRole("link", { name: "Overview" })).toHaveAttribute(
      "href",
      "/",
    );
    expect(screen.getByRole("link", { name: "Roadmap" })).toHaveAttribute(
      "href",
      "#roadmap",
    );
    expect(document.getElementById("roadmap")).toBeInTheDocument();
  });
});
