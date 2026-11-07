import { afterEach, describe, expect, it, vi } from "vitest";

import {
  getArtifactDownloadUrl,
  getArtifactMetadata,
  listArtifacts,
} from "./artifacts-client";
import { setAccessToken } from "./auth-session";

describe("artifacts-client", () => {
  afterEach(() => {
    setAccessToken(null);
    vi.unstubAllGlobals();
  });

  it("produces encoded download URL", () => {
    const url = getArtifactDownloadUrl("art-123/test file.pdf");
    expect(url).toBe("/api/v1/artifacts/art-123%2Ftest%20file.pdf");
  });

  it("lists artifacts with authorization header", async () => {
    setAccessToken("test-token", 3600);
    const mockArtifacts = [
      {
        id: "art-1",
        workspace_id: "ws-1",
        task_id: "t-1",
        user_id: "u-1",
        file_name: "report.pdf",
        file_type: "pdf",
        size_bytes: 1024,
        sha256: "abc123sha",
        verification_status: "verified",
        verification_report: null,
        created_at: "2026-11-07T10:00:00Z",
      },
    ];

    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify(mockArtifacts), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await listArtifacts("t-1");
    expect(result).toHaveLength(1);
    expect(result[0].file_name).toBe("report.pdf");
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/artifacts?task_id=t-1",
      expect.objectContaining({
        method: "GET",
        headers: expect.any(Headers),
      }),
    );
  });

  it("retrieves artifact metadata", async () => {
    setAccessToken("test-token", 3600);
    const mockMeta = {
      id: "art-1",
      workspace_id: "ws-1",
      task_id: "t-1",
      user_id: "u-1",
      file_name: "report.pdf",
      file_type: "pdf",
      size_bytes: 1024,
      sha256: "abc123sha",
      verification_status: "verified",
      verification_report: { verified: true },
      created_at: "2026-11-07T10:00:00Z",
    };

    const fetchMock = vi.fn(
      async () =>
        new Response(JSON.stringify(mockMeta), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const result = await getArtifactMetadata("art-1");
    expect(result.sha256).toBe("abc123sha");
    expect(result.verification_status).toBe("verified");
  });
});
