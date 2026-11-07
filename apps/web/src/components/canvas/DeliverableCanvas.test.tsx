import React from "react";
import { render, screen } from "@testing-library/react";
import { describe, expect, it, vi } from "vitest";

import { DeliverableCanvas } from "./DeliverableCanvas";
import type { ArtifactMetadata } from "../../lib/artifacts-client";

describe("DeliverableCanvas", () => {
  const onClose = vi.fn();

  it("does not render when closed", () => {
    const { container } = render(
      <DeliverableCanvas deliverables={[]} isOpen={false} onClose={onClose} />,
    );
    expect(container.firstChild).toBeNull();
  });

  it("renders empty state when no deliverables exist", () => {
    render(
      <DeliverableCanvas deliverables={[]} isOpen={true} onClose={onClose} />,
    );
    expect(screen.getByText("No Deliverables Yet")).toBeInTheDocument();
  });

  it("renders deliverables list and displays active deliverable", () => {
    const mockDeliverables: ArtifactMetadata[] = [
      {
        id: "art-1",
        workspace_id: "ws-1",
        task_id: "t-1",
        user_id: "u-1",
        file_name: "pricing_report.pdf",
        file_type: "pdf",
        size_bytes: 2048,
        sha256: "1234567890abcdef1234567890abcdef",
        verification_status: "verified",
        verification_report: { pages: 3 },
        created_at: "2026-11-07T10:00:00Z",
      },
      {
        id: "art-2",
        workspace_id: "ws-1",
        task_id: "t-1",
        user_id: "u-1",
        file_name: "data.xlsx",
        file_type: "xlsx",
        size_bytes: 4096,
        sha256: "abcdef1234567890abcdef1234567890",
        verification_status: "verified",
        verification_report: { row_count: 50 },
        created_at: "2026-11-07T10:05:00Z",
      },
    ];

    render(
      <DeliverableCanvas
        deliverables={mockDeliverables}
        isOpen={true}
        onClose={onClose}
      />,
    );

    expect(
      screen.getAllByText("pricing_report.pdf").length,
    ).toBeGreaterThanOrEqual(1);
    expect(screen.getByText("data.xlsx")).toBeInTheDocument();
    expect(screen.getAllByText(/verified/i).length).toBeGreaterThanOrEqual(1);
    expect(screen.getByRole("link", { name: "Download" })).toHaveAttribute(
      "href",
      "/api/v1/artifacts/art-1",
    );
  });
});
