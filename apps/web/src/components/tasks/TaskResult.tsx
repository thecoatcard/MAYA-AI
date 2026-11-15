"use client";

import React from "react";

import type { TaskDetailResponse } from "../../lib/tasks-client";

const URL_IN_TEXT = /(https:\/\/[^\s<>"')]+)/g;
const MAX_LINES = 4;

/**
 * The result line of a finished tool step, for example "Created the Notion page ... VERIFIED by
 * reading it back" with the page link. It is the step's real output text, nothing is invented;
 * only https links are made clickable.
 */
export function resultTextOf(
  task: Pick<TaskDetailResponse, "subtasks"> | null,
): string | null {
  if (task === null) return null;
  for (const step of [...task.subtasks].reverse()) {
    if (step.status.toLowerCase() !== "completed") continue;
    const out = step.output_result;
    if (out === null || typeof out !== "object") continue;
    const result = out as { tool?: unknown; output?: unknown };
    if (typeof result.tool !== "string" || typeof result.output !== "string") {
      continue;
    }
    const lines = result.output
      .split("\n")
      .map((line) => line.trim())
      .filter((line) => line !== "")
      .slice(0, MAX_LINES);
    if (lines.length > 0) return lines.join("\n");
  }
  return null;
}

export function TaskResult({
  task,
}: {
  readonly task: Pick<TaskDetailResponse, "subtasks"> | null;
}): React.JSX.Element | null {
  const text = resultTextOf(task);
  if (text === null) return null;
  return (
    <p
      dir="auto"
      className="m-0 min-w-0 whitespace-pre-line text-maya-small text-maya-text [overflow-wrap:anywhere]"
      data-testid="task-result"
    >
      {text.split(URL_IN_TEXT).map((part, i) =>
        i % 2 === 1 ? (
          <a
            key={i}
            href={part}
            target="_blank"
            rel="noopener noreferrer"
            className="underline"
          >
            {part}
          </a>
        ) : (
          <React.Fragment key={i}>{part}</React.Fragment>
        ),
      )}
    </p>
  );
}
