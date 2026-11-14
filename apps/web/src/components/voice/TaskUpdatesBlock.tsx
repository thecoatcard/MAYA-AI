import React from "react";

import {
  lineLabel,
  reviewLabel,
  type TaskUpdateBlock,
} from "../../lib/voice-task-updates";

/**
 * Spoken task updates: one compact block per task. The newest line is always visible; the full
 * list opens on demand. Lines update in place (what MAYA said, a recorded review), so it never
 * adds a chat message per update.
 */
export function TaskUpdatesBlock({
  block,
}: {
  readonly block: TaskUpdateBlock;
}): React.JSX.Element | null {
  const newest = block.lines[block.lines.length - 1];
  if (newest === undefined) return null;
  const older = block.lines.length - 1;
  return (
    <details
      className="m-0 min-w-0 text-maya-small text-maya-secondary"
      data-testid="task-updates-block"
      data-task-id={block.taskId ?? "catch-up"}
    >
      <summary
        className="min-w-0 cursor-pointer truncate"
        data-testid="task-updates-newest"
      >
        {block.taskId === null ? "" : "Task updates: "}
        {lineLabel(newest)}
        {older > 0 ? ` (${older} earlier)` : ""}
      </summary>
      <ul className="m-0 list-none space-y-1 p-0 pt-1">
        {block.lines.map((line) => {
          const review = reviewLabel(line);
          return (
            <li key={line.id} className="min-w-0 [overflow-wrap:anywhere]">
              <span>{lineLabel(line)}</span>
              {review !== null && (
                <span
                  className={
                    line.review?.verdict === "problem"
                      ? "ml-2 text-maya-danger"
                      : "ml-2 text-maya-muted"
                  }
                  data-testid="task-update-review"
                >
                  {review}
                </span>
              )}
            </li>
          );
        })}
      </ul>
    </details>
  );
}
