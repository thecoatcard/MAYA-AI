import { beforeEach, describe, expect, it, vi } from "vitest";

import { deleteArtifacts } from "./artifacts-client";
import * as authSession from "./auth-session";
import { deleteChatFile } from "./files-client";
import { deleteTask, deleteTasks } from "./tasks-client";

function respond(status: number, body?: unknown): Response {
  return new Response(body === undefined ? null : JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

describe("delete clients", () => {
  beforeEach(() => {
    vi.restoreAllMocks();
    vi.spyOn(authSession, "getAccessToken").mockReturnValue("t");
  });

  it("a second delete of a task (404) is treated as done", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      respond(404, { detail: "not found" }),
    );
    await expect(deleteTask("t1")).resolves.toBeUndefined();
  });

  it("other task delete errors surface (running task: 409)", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      respond(409, { detail: { error: "task_not_finished" } }),
    );
    await expect(deleteTask("t1")).rejects.toMatchObject({ status: 409 });
  });

  it("bulk delete sends at most 100 ids per request", async () => {
    const fetchSpy = vi
      .spyOn(globalThis, "fetch")
      .mockImplementation(() =>
        Promise.resolve(
          respond(200, { deleted: [], not_found: [], not_finished: [] }),
        ),
      );
    const ids = Array.from({ length: 250 }, (_, i) => `id-${i}`);
    await deleteTasks(ids);
    expect(fetchSpy).toHaveBeenCalledTimes(3);
    const sizes = fetchSpy.mock.calls.map(
      (call) => JSON.parse(String((call[1] as RequestInit).body)).ids.length,
    );
    expect(sizes).toEqual([100, 100, 50]);
    await deleteArtifacts(ids);
    expect(fetchSpy).toHaveBeenCalledTimes(6);
  });

  it("a chat file that is already gone (404) counts as deleted", async () => {
    vi.spyOn(globalThis, "fetch").mockResolvedValue(
      respond(404, { detail: { error: "file_not_found" } }),
    );
    await expect(deleteChatFile("s", "m", "f")).resolves.toBeUndefined();
  });
});
