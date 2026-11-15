import { act, renderHook, waitFor } from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../lib/api-client";
import * as filesClient from "../lib/files-client";
import { useAttachments } from "./useAttachments";

const stored = (name: string, id = "u-" + name) => ({
  id,
  name,
  size: 3,
  kind: "text",
  created_at: "2026-11-10T10:00:00Z",
});

describe("useAttachments", () => {
  beforeEach(() => vi.restoreAllMocks());

  it("uploads on select and take() returns the ready ids and clears the chips", async () => {
    vi.spyOn(filesClient, "uploadFiles").mockImplementation(async (files) => [
      stored(files[0].name),
    ]);
    const { result } = renderHook(() => useAttachments());
    act(() => result.current.addFiles([new File(["x"], "a.txt")]));
    expect(result.current.items[0].status).toBe("uploading");
    await waitFor(() => expect(result.current.items[0].status).toBe("ready"));
    let refs: ReturnType<typeof result.current.take> = [];
    act(() => {
      refs = result.current.take();
    });
    expect(refs).toEqual([{ id: "u-a.txt", name: "a.txt", kind: "text" }]);
    expect(result.current.count).toBe(0);
  });

  it("keeps a failed file as an error chip and the others stay", async () => {
    vi.spyOn(filesClient, "uploadFiles").mockImplementation(async (files) => {
      if (files[0].name === "bad.txt")
        throw new ApiError(413, "payload_too_large");
      return [stored(files[0].name)];
    });
    const { result } = renderHook(() => useAttachments());
    act(() =>
      result.current.addFiles([
        new File(["x"], "bad.txt"),
        new File(["x"], "good.txt"),
        new File(["x"], "tool.exe"),
      ]),
    );
    await waitFor(() => expect(result.current.uploading).toBe(false));
    const byName = Object.fromEntries(
      result.current.items.map((i) => [i.name, i]),
    );
    expect(byName["bad.txt"].status).toBe("error");
    expect(byName["bad.txt"].error).toMatch(/25 MB/);
    expect(byName["good.txt"].status).toBe("ready");
    expect(byName["tool.exe"].error).toMatch(/Programs and archives/);
    let refs: ReturnType<typeof result.current.take> = [];
    act(() => {
      refs = result.current.take();
    });
    expect(refs.map((r) => r.name)).toEqual(["good.txt"]);
  });

  it("caps at 10 files with a plain notice", async () => {
    vi.spyOn(filesClient, "uploadFiles").mockImplementation(async (files) => [
      stored(files[0].name),
    ]);
    const { result } = renderHook(() => useAttachments());
    act(() =>
      result.current.addFiles(
        Array.from({ length: 12 }, (_, i) => new File(["x"], `f${i}.txt`)),
      ),
    );
    expect(result.current.count).toBe(10);
    expect(result.current.notice).toMatch(
      /Only 10 files.*2 files were not added/,
    );
    await waitFor(() => expect(result.current.uploading).toBe(false));
  });

  it("remove deletes an owned upload; an existing one is only detached", async () => {
    vi.spyOn(filesClient, "uploadFiles").mockResolvedValue([stored("a.txt")]);
    const del = vi.spyOn(filesClient, "deleteUpload").mockResolvedValue();
    const { result } = renderHook(() => useAttachments());
    act(() => result.current.addFiles([new File(["x"], "a.txt")]));
    await waitFor(() => expect(result.current.items[0]?.status).toBe("ready"));
    act(() => result.current.remove(result.current.items[0].key));
    expect(del).toHaveBeenCalledWith("u-a.txt");
    act(() => result.current.addExisting(stored("old.txt", "old1")));
    act(() => result.current.addExisting(stored("old.txt", "old1")));
    expect(result.current.count).toBe(1);
    act(() => result.current.remove(result.current.items[0].key));
    expect(del).toHaveBeenCalledTimes(1);
  });
});
