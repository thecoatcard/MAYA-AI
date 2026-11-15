import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, NetworkError } from "./api-client";
import { setAccessToken } from "./auth-session";

const refreshMock = vi.fn<() => Promise<{ accessToken: string } | null>>();
vi.mock("./auth-session", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./auth-session")>();
  return { ...actual, refreshSession: () => refreshMock() };
});

import {
  describeWorkspaceError,
  disconnectWorkspace,
  getWorkspace,
  listWorkspaces,
  registerWorkspace,
  setDefaultWorkspace,
  type ProjectWorkspace,
} from "./workspaces-client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const mockWorkspace1: ProjectWorkspace = {
  id: "ws-1",
  name: "Workspace One",
  path: "/home/user/projects/one",
  is_default: true,
  status: "ok",
  writable: true,
  created_at: "2026-11-15T10:00:00Z",
};

const mockWorkspace2: ProjectWorkspace = {
  id: "ws-2",
  name: "Workspace Two",
  path: "/home/user/projects/two",
  is_default: false,
  status: "missing",
  writable: false,
  created_at: "2026-11-15T11:00:00Z",
};

describe("workspaces-client", () => {
  const fetchMock = vi.fn();

  beforeEach(() => {
    setAccessToken("test-token", 3600);
    refreshMock.mockReset();
    refreshMock.mockResolvedValue(null);
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });

  afterEach(() => {
    setAccessToken(null);
    vi.unstubAllGlobals();
  });

  describe("listWorkspaces", () => {
    it("fetches /api/v1/workspaces with Bearer token and returns workspaces array", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse({ workspaces: [mockWorkspace1, mockWorkspace2] }),
      );

      const result = await listWorkspaces();

      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [url, init] = fetchMock.mock.calls[0]!;
      expect(url).toBe("/api/v1/workspaces");
      expect(init.method).toBe("GET");
      expect((init.headers as Headers).get("Authorization")).toBe(
        "Bearer test-token",
      );
      expect((init.headers as Headers).get("Accept")).toBe("application/json");
      expect(result).toEqual([mockWorkspace1, mockWorkspace2]);
    });

    it("returns empty array if workspaces field is absent", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse({}));

      const result = await listWorkspaces();

      expect(result).toEqual([]);
    });
  });

  describe("registerWorkspace", () => {
    it("calls POST /api/v1/workspaces with path and name", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(mockWorkspace1, 201));

      const result = await registerWorkspace(
        "/home/user/projects/one",
        "Workspace One",
      );

      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [url, init] = fetchMock.mock.calls[0]!;
      expect(url).toBe("/api/v1/workspaces");
      expect(init.method).toBe("POST");
      expect((init.headers as Headers).get("Authorization")).toBe(
        "Bearer test-token",
      );
      expect((init.headers as Headers).get("Content-Type")).toBe(
        "application/json",
      );
      expect(JSON.parse(init.body as string)).toEqual({
        path: "/home/user/projects/one",
        name: "Workspace One",
      });
      expect(result).toEqual(mockWorkspace1);
    });

    it("calls POST /api/v1/workspaces with only path when name is omitted", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(mockWorkspace2, 201));

      const result = await registerWorkspace("/home/user/projects/two");

      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [, init] = fetchMock.mock.calls[0]!;
      expect(JSON.parse(init.body as string)).toEqual({
        path: "/home/user/projects/two",
      });
      expect(result).toEqual(mockWorkspace2);
    });
  });

  describe("getWorkspace", () => {
    it("calls GET /api/v1/workspaces/{id} with encoded id", async () => {
      fetchMock.mockResolvedValueOnce(jsonResponse(mockWorkspace1));

      const result = await getWorkspace("ws-1/special");

      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [url, init] = fetchMock.mock.calls[0]!;
      expect(url).toBe("/api/v1/workspaces/ws-1%2Fspecial");
      expect(init.method).toBe("GET");
      expect(result).toEqual(mockWorkspace1);
    });
  });

  describe("disconnectWorkspace", () => {
    it("calls DELETE /api/v1/workspaces/{id} with encoded id", async () => {
      fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));

      await disconnectWorkspace("ws-1");

      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [url, init] = fetchMock.mock.calls[0]!;
      expect(url).toBe("/api/v1/workspaces/ws-1");
      expect(init.method).toBe("DELETE");
      expect((init.headers as Headers).get("Authorization")).toBe(
        "Bearer test-token",
      );
    });
  });

  describe("setDefaultWorkspace", () => {
    it("calls POST /api/v1/workspaces/{id}/default with encoded id", async () => {
      const updated = { ...mockWorkspace2, is_default: true };
      fetchMock.mockResolvedValueOnce(jsonResponse(updated));

      const result = await setDefaultWorkspace("ws-2");

      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [url, init] = fetchMock.mock.calls[0]!;
      expect(url).toBe("/api/v1/workspaces/ws-2/default");
      expect(init.method).toBe("POST");
      expect((init.headers as Headers).get("Authorization")).toBe(
        "Bearer test-token",
      );
      expect(result).toEqual(updated);
    });
  });

  describe("authentication and retry behavior", () => {
    it("refreshes session if access token is initially missing", async () => {
      setAccessToken(null);
      refreshMock.mockImplementationOnce(async () => {
        setAccessToken("fresh", 3600);
        return { accessToken: "fresh" };
      });
      fetchMock.mockResolvedValueOnce(jsonResponse({ workspaces: [] }));

      await listWorkspaces();

      expect(refreshMock).toHaveBeenCalledTimes(1);
      const [, init] = fetchMock.mock.calls[0]!;
      expect((init.headers as Headers).get("Authorization")).toBe(
        "Bearer fresh",
      );
    });

    it("retries request on 401 after refreshing token", async () => {
      fetchMock
        .mockResolvedValueOnce(
          jsonResponse({ error: "invalid_token", message: "Expired" }, 401),
        )
        .mockResolvedValueOnce(jsonResponse({ workspaces: [mockWorkspace1] }));

      refreshMock.mockImplementationOnce(async () => {
        setAccessToken("new-token", 3600);
        return { accessToken: "new-token" };
      });

      const list = await listWorkspaces();

      expect(fetchMock).toHaveBeenCalledTimes(2);
      expect(refreshMock).toHaveBeenCalledTimes(1);
      const [, retryInit] = fetchMock.mock.calls[1]!;
      expect((retryInit.headers as Headers).get("Authorization")).toBe(
        "Bearer new-token",
      );
      expect(list).toEqual([mockWorkspace1]);
    });
  });

  describe("error handling", () => {
    it("throws ApiError when response is not ok", async () => {
      fetchMock.mockResolvedValueOnce(
        jsonResponse(
          { error: "not_found", message: "Workspace 'ws-999' not found." },
          404,
        ),
      );

      await expect(getWorkspace("ws-999")).rejects.toThrow(ApiError);
    });

    it("wraps generic fetch exceptions in NetworkError", async () => {
      fetchMock.mockRejectedValueOnce(new TypeError("Failed to fetch"));

      await expect(listWorkspaces()).rejects.toThrow(NetworkError);
    });

    it("rethrows AbortError without wrapping", async () => {
      const abortError = new Error("Aborted");
      abortError.name = "AbortError";
      fetchMock.mockRejectedValueOnce(abortError);

      await expect(listWorkspaces()).rejects.toThrow(abortError);
    });
  });

  describe("describeWorkspaceError", () => {
    it("formats 401 sign-in message", () => {
      const err = new ApiError(401, "unauthorized");
      expect(describeWorkspaceError(err)).toBe(
        "You are signed out. Sign in again and retry.",
      );
    });

    it("formats 403 permission message", () => {
      const err = new ApiError(403, "forbidden");
      expect(describeWorkspaceError(err)).toBe(
        "You do not have permission to do that.",
      );
    });

    it("formats 404 message using server message if present", () => {
      const err = new ApiError(404, "not_found", {
        message: "Workspace 'xyz' not found.",
      });
      expect(describeWorkspaceError(err)).toBe("Workspace 'xyz' not found.");
    });

    it("formats 404 message default when server message absent", () => {
      const err = new ApiError(404, "not_found");
      expect(describeWorkspaceError(err)).toBe("That workspace was not found.");
    });

    it("formats 422 validation message with server message", () => {
      const err = new ApiError(422, "invalid_folder", {
        message: "Path does not exist",
      });
      expect(describeWorkspaceError(err)).toBe("Path does not exist");
    });

    it("formats 422 validation message fallback when server message absent", () => {
      const err = new ApiError(422, "invalid_folder");
      expect(describeWorkspaceError(err)).toBe(
        "Invalid workspace directory. Check the path and try again.",
      );
    });

    it("formats 429 rate limit message", () => {
      const err = new ApiError(429, "rate_limited");
      expect(describeWorkspaceError(err)).toBe(
        "Too many requests. Wait a moment and try again.",
      );
    });

    it("formats 500 server error message", () => {
      const err = new ApiError(500, "internal_error");
      expect(describeWorkspaceError(err)).toBe(
        "The server could not do that right now. Try again in a moment.",
      );
    });

    it("formats NetworkError", () => {
      const err = new NetworkError();
      expect(describeWorkspaceError(err)).toBe(
        "The connection was lost. Check your network and try again.",
      );
    });

    it("formats unknown errors with fallback", () => {
      expect(describeWorkspaceError(new Error("Unknown"))).toBe(
        "That could not be done. Try again.",
      );
    });
  });
});
