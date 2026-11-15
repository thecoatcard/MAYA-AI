import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import { AuthPageLayout } from "./AuthPageLayout";

describe("AuthPageLayout", () => {
  it("renders children in the card without marketing nav or footer", () => {
    render(
      <AuthPageLayout>
        <div data-testid="auth-card-content">Auth Test Form</div>
      </AuthPageLayout>,
    );

    expect(screen.getByTestId("auth-card-content")).toBeInTheDocument();
    expect(screen.getByText("Auth Test Form")).toBeInTheDocument();
    expect(screen.queryByRole("link", { name: "MAYA AI home" })).toBeNull();
    expect(
      screen.queryByText(/Architectural integrity, deterministic safety/i),
    ).toBeNull();
  });
});
