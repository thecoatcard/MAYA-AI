import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import { AuthPageLayout } from "./AuthPageLayout";

describe("AuthPageLayout", () => {
  it("renders PublicNav, children inside frosted glass container, and PublicFooter", () => {
    render(
      <AuthPageLayout>
        <div data-testid="auth-card-content">Auth Test Form</div>
      </AuthPageLayout>,
    );

    // Brand navigation is rendered
    expect(
      screen.getByRole("link", { name: "MAYA AI home" }),
    ).toBeInTheDocument();

    // Children content is rendered
    expect(screen.getByTestId("auth-card-content")).toBeInTheDocument();
    expect(screen.getByText("Auth Test Form")).toBeInTheDocument();

    // Footer is rendered
    expect(
      screen.getByText(/Architectural integrity, deterministic safety/i),
    ).toBeInTheDocument();
  });
});
