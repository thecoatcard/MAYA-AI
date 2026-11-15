import React from "react";
import { render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { DecisionHealthRow, decisionSummary } from "./DecisionHealthRow";
import * as client from "../../lib/decisions-client";

vi.mock("../../lib/decisions-client", () => ({ getDecisionStatus: vi.fn() }));

describe("DecisionHealthRow", () => {
  beforeEach(() => vi.clearAllMocks());

  it("summarises off, ok and failed states without secrets", () => {
    expect(
      decisionSummary({ enabled: false, model: "m", circuit: "closed" }).label,
    ).toBe("Off");
    const ok = decisionSummary({
      enabled: true,
      model: "gemini-decision-1",
      circuit: "closed",
      startup_check: { state: "ok", reason: "answered", checked_at: "t" },
    });
    expect(ok.label).toBe("OK");
    expect(ok.detail).toContain("Last check: t");
    expect(
      decisionSummary({
        enabled: true,
        model: "m",
        circuit: "open",
        startup_check: { state: "failed", reason: "no answer" },
      }).label,
    ).toBe("Failed");
  });

  it("shows the status from the endpoint", async () => {
    vi.mocked(client.getDecisionStatus).mockResolvedValueOnce({
      enabled: true,
      model: "gemini-decision-1",
      circuit: "closed",
      startup_check: { state: "failed", reason: "ConnectError" },
    });
    render(<DecisionHealthRow />);
    await waitFor(() =>
      expect(screen.getByTestId("decision-health")).toHaveTextContent(
        "Failed. ConnectError",
      ),
    );
  });

  it("says so when the status cannot be read", async () => {
    vi.mocked(client.getDecisionStatus).mockRejectedValueOnce(new Error("x"));
    render(<DecisionHealthRow />);
    await waitFor(() =>
      expect(screen.getByTestId("decision-health")).toHaveTextContent(
        "Status unavailable",
      ),
    );
  });
});
