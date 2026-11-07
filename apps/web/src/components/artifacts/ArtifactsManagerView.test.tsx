import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ArtifactsManagerView } from "./ArtifactsManagerView";
import * as artifactsClient from "../../lib/artifacts-client";

vi.mock("../../lib/artifacts-client", () => ({
  listArtifacts: vi.fn(),
  downloadArtifactFile: vi.fn(),
  getArtifactDownloadUrl: vi.fn(
    (id: string) => `/api/v1/artifacts/${encodeURIComponent(id)}`,
  ),
  getArtifactMetadata: vi.fn(),
}));

describe("ArtifactsManagerView", () => {
  beforeEach(() => {
    vi.clearAllMocks();
  });

  it("renders artifacts repository title and deliverables", async () => {
    const mockArtifacts: artifactsClient.ArtifactMetadata[] = [
      {
        id: "art-101",
        workspace_id: "ws-1",
        task_id: "task-001",
        user_id: "u-1",
        file_name: "test_financial_plan.xlsx",
        file_type: "xlsx",
        size_bytes: 40960,
        sha256: "fedcba9876543210fedcba9876543210",
        verification_status: "verified",
        verification_report: { row_count: 100 },
        created_at: "2026-11-07T10:00:00Z",
      },
    ];

    vi.mocked(artifactsClient.listArtifacts).mockResolvedValueOnce(
      mockArtifacts,
    );

    render(<ArtifactsManagerView />);

    await waitFor(() => {
      expect(
        screen.getByText("Deliverables & Artifacts Repository"),
      ).toBeInTheDocument();
      expect(
        screen.getAllByText("test_financial_plan.xlsx").length,
      ).toBeGreaterThanOrEqual(1);
    });

    const downloadLinks = screen.getAllByRole("link", { name: /download/i });
    expect(downloadLinks[0]).toHaveAttribute(
      "href",
      "/api/v1/artifacts/art-101",
    );
  });

  it("opens verification audit report modal", async () => {
    const mockArtifacts: artifactsClient.ArtifactMetadata[] = [
      {
        id: "art-102",
        workspace_id: "ws-1",
        task_id: "task-002",
        user_id: "u-1",
        file_name: "audit_report.pdf",
        file_type: "pdf",
        size_bytes: 51200,
        sha256: "11223344556677889900aabbccddeeff",
        verification_status: "verified",
        verification_report: { pages: 4 },
        created_at: "2026-11-07T11:00:00Z",
      },
    ];

    vi.mocked(artifactsClient.listArtifacts).mockResolvedValueOnce(
      mockArtifacts,
    );

    render(<ArtifactsManagerView />);

    await waitFor(() => {
      expect(
        screen.getAllByText("audit_report.pdf").length,
      ).toBeGreaterThanOrEqual(1);
    });

    const auditBtns = screen.getAllByRole("button", { name: "Audit" });
    fireEvent.click(auditBtns[0]);

    expect(
      screen.getByText(/Verification Audit Report — audit_report\.pdf/i),
    ).toBeInTheDocument();
  });

  it("triggers authenticated download when download link is clicked", async () => {
    const mockArtifacts: artifactsClient.ArtifactMetadata[] = [
      {
        id: "art-103",
        workspace_id: "ws-1",
        task_id: "task-003",
        user_id: "u-1",
        file_name: "financial_forecast.xlsx",
        file_type: "xlsx",
        size_bytes: 20480,
        sha256: "aabbccddeeff00112233445566778899",
        verification_status: "verified",
        verification_report: null,
        created_at: "2026-11-07T12:00:00Z",
      },
    ];

    vi.mocked(artifactsClient.listArtifacts).mockResolvedValueOnce(
      mockArtifacts,
    );

    render(<ArtifactsManagerView />);

    await waitFor(() => {
      expect(
        screen.getAllByText("financial_forecast.xlsx").length,
      ).toBeGreaterThanOrEqual(1);
    });

    const downloadLinks = screen.getAllByRole("link", { name: /download/i });
    fireEvent.click(downloadLinks[0]);

    expect(artifactsClient.downloadArtifactFile).toHaveBeenCalledWith(
      "art-103",
      "financial_forecast.xlsx",
    );
  });
});
