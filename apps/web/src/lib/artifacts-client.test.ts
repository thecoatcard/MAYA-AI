import { afterEach, describe, expect, it, vi } from "vitest";

import {
  downloadArtifactFile,
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

  it("produces encoded download URL without token when unauthenticated", () => {
    const url = getArtifactDownloadUrl("art-123/test file.pdf");
    expect(url).toBe("/api/v1/artifacts/art-123%2Ftest%20file.pdf");
  });

  it("produces download URL with token query parameter when authenticated", () => {
    setAccessToken("test-secret-token", 3600);
    const url = getArtifactDownloadUrl("art-123");
    expect(url).toBe("/api/v1/artifacts/art-123?token=test-secret-token");
  });

  it("produces download URL with token and inline=true for browser viewing", () => {
    setAccessToken("test-secret-token", 3600);
    const url = getArtifactDownloadUrl("art-123", true);
    expect(url).toBe(
      "/api/v1/artifacts/art-123?token=test-secret-token&inline=true",
    );
  });

  it("downloads artifact file as blob with bearer authentication", async () => {
    setAccessToken("test-token", 3600);
    const mockBlob = new Blob(["fake pdf content"], {
      type: "application/pdf",
    });

    const fetchMock = vi.fn(
      async () =>
        new Response(mockBlob, {
          status: 200,
          headers: { "Content-Type": "application/pdf" },
        }),
    );
    vi.stubGlobal("fetch", fetchMock);

    const createObjectURLMock = vi.fn(() => "blob:http://localhost/mock-uuid");
    const revokeObjectURLMock = vi.fn();
    window.URL.createObjectURL = createObjectURLMock;
    window.URL.revokeObjectURL = revokeObjectURLMock;

    await downloadArtifactFile("art-123", "report.pdf");

    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/artifacts/art-123",
      expect.objectContaining({
        method: "GET",
        headers: expect.any(Headers),
      }),
    );
    expect(createObjectURLMock).toHaveBeenCalledWith(expect.any(Blob));
    expect(revokeObjectURLMock).toHaveBeenCalledWith(
      "blob:http://localhost/mock-uuid",
    );
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
