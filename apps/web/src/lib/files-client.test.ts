import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError, NetworkError } from "./api-client";
import { setAccessToken } from "./auth-session";

const refreshMock = vi.fn<() => Promise<{ accessToken: string } | null>>();
vi.mock("./auth-session", async (importOriginal) => {
  const actual = await importOriginal<typeof import("./auth-session")>();
  return { ...actual, refreshSession: () => refreshMock() };
});

import {
  MAX_UPLOAD_FILE_BYTES,
  checkUploadFile,
  clearFolder,
  deleteUpload,
  describeFilesError,
  downloadUpload,
  formatBytes,
  getFolder,
  listFolderTree,
  listUploads,
  setFolder,
  uploadFiles,
} from "./files-client";

function jsonResponse(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

class FakeXhr {
  static instances: FakeXhr[] = [];
  static plan: Array<{ status: number; body: string } | "error"> = [];
  headers: Record<string, string> = {};
  upload: { onprogress: ((e: unknown) => void) | null } = { onprogress: null };
  onload: (() => void) | null = null;
  onerror: (() => void) | null = null;
  ontimeout: (() => void) | null = null;
  onabort: (() => void) | null = null;
  status = 0;
  responseText = "";
  method = "";
  url = "";
  sent: FormData | null = null;
  constructor() {
    FakeXhr.instances.push(this);
  }
  open(method: string, url: string) {
    this.method = method;
    this.url = url;
  }
  setRequestHeader(k: string, v: string) {
    this.headers[k] = v;
  }
  send(form: FormData) {
    this.sent = form;
    const next = FakeXhr.plan.shift() ?? { status: 201, body: "[]" };
    queueMicrotask(() => {
      this.upload.onprogress?.({
        lengthComputable: true,
        loaded: 5,
        total: 10,
      });
      if (next === "error") {
        this.onerror?.();
        return;
      }
      this.status = next.status;
      this.responseText = next.body;
      this.onload?.();
    });
  }
  abort() {
    this.onabort?.();
  }
}

describe("files-client", () => {
  beforeEach(() => {
    setAccessToken("tok", 3600);
    refreshMock.mockReset();
    refreshMock.mockResolvedValue(null);
    FakeXhr.instances = [];
    FakeXhr.plan = [];
    vi.stubGlobal("XMLHttpRequest", FakeXhr);
  });
  afterEach(() => {
    setAccessToken(null);
    vi.unstubAllGlobals();
  });

  it("getFolder reads the connected folder with a bearer token", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        path: "C:\\Work",
        status: "ok",
        writable: true,
        name: "Work",
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    await expect(getFolder()).resolves.toEqual({
      path: "C:\\Work",
      status: "ok",
      writable: true,
      name: "Work",
    });
    const [url, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(url).toBe("/api/v1/files/folder");
    expect(new Headers(init.headers).get("Authorization")).toBe("Bearer tok");
  });

  it("getFolder rejects an unknown status as an unexpected response", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => jsonResponse({ path: null, status: "weird" })),
    );
    await expect(getFolder()).rejects.toMatchObject({
      code: "unexpected_response",
    });
  });

  it("setFolder sends the path and surfaces the 422 reason", async () => {
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(
        jsonResponse({
          path: "D:\\Docs",
          status: "ok",
          writable: true,
          name: "Docs",
        }),
      )
      .mockResolvedValueOnce(
        jsonResponse(
          { error: "validation_error", message: "That is a system folder." },
          422,
        ),
      );
    vi.stubGlobal("fetch", fetchMock);
    await setFolder("D:\\Docs");
    const [, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(init.method).toBe("PUT");
    expect(JSON.parse(String(init.body))).toEqual({ path: "D:\\Docs" });
    const failure = await setFolder("C:\\Windows").catch((e: unknown) => e);
    expect(failure).toBeInstanceOf(ApiError);
    expect(describeFilesError(failure)).toBe("That is a system folder.");
  });

  it("clearFolder issues DELETE and accepts 204", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await expect(clearFolder()).resolves.toBeUndefined();
    const [, init] = fetchMock.mock.calls[0] as unknown as [
      string,
      RequestInit,
    ];
    expect(init.method).toBe("DELETE");
  });

  it("retries once after a 401 with the refreshed token", async () => {
    refreshMock.mockImplementation(async () => {
      setAccessToken("fresh", 3600);
      return { accessToken: "fresh" };
    });
    const fetchMock = vi
      .fn()
      .mockResolvedValueOnce(jsonResponse({ error: "invalid_token" }, 401))
      .mockResolvedValueOnce(
        jsonResponse({
          path: null,
          status: "none",
          writable: false,
          name: null,
        }),
      );
    vi.stubGlobal("fetch", fetchMock);
    await expect(getFolder()).resolves.toMatchObject({ status: "none" });
    expect(fetchMock).toHaveBeenCalledTimes(2);
    const [, retry] = fetchMock.mock.calls[1] as unknown as [
      string,
      RequestInit,
    ];
    expect(new Headers(retry.headers).get("Authorization")).toBe(
      "Bearer fresh",
    );
  });

  it("maps a dropped connection to NetworkError", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(async () => Promise.reject(new TypeError("offline"))),
    );
    const error = await getFolder().catch((e: unknown) => e);
    expect(error).toBeInstanceOf(NetworkError);
    expect(describeFilesError(error)).toMatch(/connection was lost/i);
  });

  it("listFolderTree builds the query and keeps only well-formed entries", async () => {
    const fetchMock = vi.fn(async () =>
      jsonResponse({
        path: "site",
        truncated: true,
        entries: [
          {
            name: "index.html",
            type: "file",
            size: 12,
            modified_at: "2026-11-10T10:00:00Z",
          },
          {
            name: "assets",
            type: "dir",
            size: null,
            modified_at: "2026-11-10T10:00:00Z",
          },
          { nope: 1 },
        ],
      }),
    );
    vi.stubGlobal("fetch", fetchMock);
    const tree = await listFolderTree("site", 50);
    expect((fetchMock.mock.calls[0] as unknown[])[0]).toBe(
      "/api/v1/files/folder/tree?path=site&limit=50",
    );
    expect(tree.truncated).toBe(true);
    expect(tree.entries.map((e) => e.name)).toEqual(["index.html", "assets"]);
  });

  it("listUploads accepts a bare array and a page object", async () => {
    const item = {
      id: "u1",
      name: "a.txt",
      size: 3,
      kind: "text",
      created_at: "2026-11-10T10:00:00Z",
    };
    vi.stubGlobal(
      "fetch",
      vi
        .fn()
        .mockResolvedValueOnce(jsonResponse([item]))
        .mockResolvedValueOnce(
          jsonResponse({ items: [item], next_cursor: "c2" }),
        ),
    );
    await expect(listUploads()).resolves.toEqual({
      items: [item],
      next_cursor: null,
    });
    await expect(listUploads({ limit: 5, cursor: "c1" })).resolves.toEqual({
      items: [item],
      next_cursor: "c2",
    });
  });

  it("deleteUpload encodes the id", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 204 }));
    vi.stubGlobal("fetch", fetchMock);
    await deleteUpload("a/b");
    expect((fetchMock.mock.calls[0] as unknown[])[0]).toBe(
      "/api/v1/files/uploads/a%2Fb",
    );
  });

  it("downloadUpload fetches an authenticated blob and saves it", async () => {
    const fetchMock = vi.fn(
      async () => new Response(new Blob(["hello"]), { status: 200 }),
    );
    vi.stubGlobal("fetch", fetchMock);
    window.URL.createObjectURL = vi.fn(() => "blob:x");
    window.URL.revokeObjectURL = vi.fn();
    const click = vi
      .spyOn(HTMLAnchorElement.prototype, "click")
      .mockImplementation(() => undefined);
    await downloadUpload("u1", "a.txt");
    expect((fetchMock.mock.calls[0] as unknown[])[0]).toBe(
      "/api/v1/files/uploads/u1/download",
    );
    expect(click).toHaveBeenCalledTimes(1);
    expect(window.URL.revokeObjectURL).toHaveBeenCalledWith("blob:x");
    click.mockRestore();
  });

  it("uploadFiles posts multipart, reports progress and returns the stored files", async () => {
    const item = {
      id: "u1",
      name: "a.txt",
      size: 3,
      kind: "text",
      created_at: "2026-11-10T10:00:00Z",
    };
    FakeXhr.plan = [{ status: 201, body: JSON.stringify([item]) }];
    const progress = vi.fn();
    const file = new File(["abc"], "a.txt", { type: "text/plain" });
    await expect(
      uploadFiles([file], { onProgress: progress }),
    ).resolves.toEqual([item]);
    const xhr = FakeXhr.instances[0];
    expect(xhr.method).toBe("POST");
    expect(xhr.url).toBe("/api/v1/files/uploads");
    expect(xhr.headers.Authorization).toBe("Bearer tok");
    expect((xhr.sent as FormData).getAll("files")).toHaveLength(1);
    expect(progress).toHaveBeenCalledWith(5, 10);
  });

  it("uploadFiles maps 413 and 415 to plain text and keeps the status", async () => {
    FakeXhr.plan = [
      { status: 413, body: JSON.stringify({ error: "payload_too_large" }) },
      {
        status: 415,
        body: JSON.stringify({ error: "unsupported_media_type" }),
      },
    ];
    const file = new File(["abc"], "a.txt");
    const big = await uploadFiles([file]).catch((e: unknown) => e);
    expect(big).toMatchObject({ status: 413 });
    expect(describeFilesError(big)).toMatch(/25 MB/);
    const bad = await uploadFiles([file]).catch((e: unknown) => e);
    expect(describeFilesError(bad)).toMatch(/cannot be attached/);
  });

  it("uploadFiles retries once after a 401", async () => {
    refreshMock.mockImplementation(async () => {
      setAccessToken("fresh", 3600);
      return { accessToken: "fresh" };
    });
    FakeXhr.plan = [
      { status: 401, body: JSON.stringify({ error: "invalid_token" }) },
      { status: 201, body: "[]" },
    ];
    await uploadFiles([new File(["x"], "a.txt")]);
    expect(FakeXhr.instances).toHaveLength(2);
    expect(FakeXhr.instances[1].headers.Authorization).toBe("Bearer fresh");
  });

  it("uploadFiles reports a network failure and rejects a bad count before sending", async () => {
    FakeXhr.plan = ["error"];
    await expect(
      uploadFiles([new File(["x"], "a.txt")]),
    ).rejects.toBeInstanceOf(NetworkError);
    await expect(uploadFiles([])).rejects.toMatchObject({ status: 422 });
    const eleven = Array.from(
      { length: 11 },
      (_, i) => new File(["x"], `f${i}.txt`),
    );
    await expect(uploadFiles(eleven)).rejects.toMatchObject({ status: 422 });
    expect(FakeXhr.instances).toHaveLength(1);
  });

  it("uploadFiles abort rejects with AbortError", async () => {
    const controller = new AbortController();
    controller.abort();
    await expect(
      uploadFiles([new File(["x"], "a.txt")], { signal: controller.signal }),
    ).rejects.toMatchObject({ name: "AbortError" });
  });

  it("checkUploadFile mirrors the contract limits", () => {
    expect(checkUploadFile({ name: "notes.MD", size: 10 })).toBeNull();
    expect(checkUploadFile({ name: "setup.exe", size: 10 })).toMatch(
      /Programs and archives/,
    );
    expect(checkUploadFile({ name: "pack.zip", size: 10 })).toMatch(
      /Programs and archives/,
    );
    expect(checkUploadFile({ name: "movie.mp4", size: 10 })).toMatch(
      /not supported/,
    );
    expect(checkUploadFile({ name: "noext", size: 10 })).toMatch(
      /not supported/,
    );
    expect(
      checkUploadFile({ name: "big.pdf", size: MAX_UPLOAD_FILE_BYTES + 1 }),
    ).toMatch(/25 MB/);
    expect(
      checkUploadFile({ name: "ok.pdf", size: MAX_UPLOAD_FILE_BYTES }),
    ).toBeNull();
  });

  it("describeFilesError gives plain text per status", () => {
    expect(describeFilesError(new ApiError(404, "not_found"))).toMatch(
      /no longer exists/,
    );
    expect(describeFilesError(new ApiError(500, "x"))).toMatch(
      /server could not/,
    );
    expect(describeFilesError(new ApiError(422, "validation_error"))).toMatch(
      /not accepted/,
    );
    expect(describeFilesError(new Error("boom"))).toBe(
      "That could not be done. Try again.",
    );
  });

  it("formatBytes", () => {
    expect(formatBytes(12)).toBe("12 B");
    expect(formatBytes(2048)).toBe("2.0 KB");
    expect(formatBytes(50 * 1024)).toBe("50 KB");
    expect(formatBytes(3 * 1024 * 1024)).toBe("3.0 MB");
  });
});
