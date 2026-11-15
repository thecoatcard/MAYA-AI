"use client";

import Link from "next/link";
import React, { useEffect, useState } from "react";

import { listMessages, type StoredMessage } from "../../lib/api-client";
import { readTaskResult, resultsForTask } from "../../lib/task-result";
import type { TaskDetailResponse } from "../../lib/tasks-client";
import { MarkdownRenderer } from "../chat/MarkdownRenderer";
import { TaskStatusBadge } from "./TaskStatusBadge";

const PAGE_LIMIT = 100;
const MAX_PAGES = 20;

/** Words for the final state the result message reports. */
const STATUS_LABEL: Readonly<Record<string, string>> = {
  done: "Done",
  partial: "Completed with errors",
  failed: "Failed",
  cancelled: "Cancelled",
  needs_attention: "Needs your input",
};

async function findResult(
  sessionId: string,
  taskId: string,
): Promise<StoredMessage | null> {
  const all: StoredMessage[] = [];
  let cursor: string | null = null;
  for (let pages = 0; pages < MAX_PAGES; pages += 1) {
    const page = await listMessages(sessionId, { limit: PAGE_LIMIT, cursor });
    all.push(...page.items);
    cursor = page.next_cursor;
    if (cursor === null) break;
  }
  const found = resultsForTask(all, taskId);
  return found.length > 0 ? (found[found.length - 1] ?? null) : null;
}

/**
 * What the task got, above the technical details: the same text MAYA posted in the chat when the
 * task ended (read from that message, so chat, voice and this page never disagree), the final
 * state and the files. Shown only for a finished task that has such a message.
 */
export function TaskResultSection({
  task,
  finished,
}: {
  readonly task: Pick<TaskDetailResponse, "id" | "session_id" | "partial">;
  readonly finished: boolean;
}): React.JSX.Element | null {
  const [message, setMessage] = useState<StoredMessage | null>(null);

  useEffect(() => {
    setMessage(null);
    if (!finished) return undefined;
    let cancelled = false;
    void findResult(task.session_id, task.id)
      .then((found) => {
        if (!cancelled) setMessage(found);
      })
      .catch(() => undefined);
    return () => {
      cancelled = true;
    };
  }, [finished, task.id, task.session_id]);

  if (message === null) return null;
  const mark = readTaskResult(message);
  const label = mark !== null ? (STATUS_LABEL[mark.status] ?? null) : null;
  return (
    <section
      aria-label="Result"
      className="flex min-w-0 flex-col gap-2"
      data-testid="task-result-section"
    >
      <div className="flex min-w-0 flex-wrap items-center gap-2">
        <h3 className="maya-label m-0">Result</h3>
        <TaskStatusBadge
          status={mark?.status === "failed" ? "failed" : "completed"}
          partial={mark?.status === "partial" || task.partial === true}
          {...(label !== null ? { label } : {})}
        />
      </div>
      <div className="min-w-0 text-maya-text">
        <MarkdownRenderer content={message.content} />
      </div>
      {mark !== null && mark.artifactIds.length > 0 && (
        <p className="m-0 text-maya-small text-maya-text-muted">
          {mark.artifactIds.length === 1
            ? "1 file was made. "
            : `${mark.artifactIds.length} files were made. `}
          <Link href="/artifacts" className="underline">
            Open Artifacts
          </Link>
        </p>
      )}
    </section>
  );
}
