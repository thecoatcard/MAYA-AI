import { describe, expect, it } from "vitest";

import type { StoredMessage } from "./api-client";
import { readTaskResult, resultsForTask } from "./task-result";

function msg(metadata: unknown, role: "assistant" | "user" = "assistant") {
  return { id: "m", role, content: "x", metadata } as StoredMessage;
}

describe("readTaskResult", () => {
  it("reads the marker the server stores on a task result message", () => {
    expect(
      readTaskResult(
        msg({
          task_result: {
            task_id: "t1",
            status: "partial",
            artifact_ids: ["a1", 3],
          },
        }),
      ),
    ).toEqual({ taskId: "t1", status: "partial", artifactIds: ["a1"] });
  });

  it("ignores anything else", () => {
    expect(readTaskResult(msg(null))).toBeNull();
    expect(readTaskResult(msg({ task_result: null }))).toBeNull();
    expect(
      readTaskResult(msg({ task_result: { task_id: "t", status: "weird" } })),
    ).toBeNull();
    expect(
      readTaskResult(
        msg({ task_result: { task_id: "t", status: "done" } }, "user"),
      ),
    ).toBeNull();
  });

  it("finds the results of one task", () => {
    const a = msg({ task_result: { task_id: "t1", status: "done" } });
    const b = msg({ task_result: { task_id: "t2", status: "done" } });
    expect(resultsForTask([a, b], "t2")).toEqual([b]);
  });
});
