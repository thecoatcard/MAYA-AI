import type { AskQuestion, ChatInputRequest } from "./ask-user";
import type { TaskDetailResponse } from "./tasks-client";

/** A step of the task that paused to ask the user one detail (the `needs_input` marker). */
export interface PendingTaskInput {
  readonly subtaskId: string;
  readonly request: ChatInputRequest;
}

const DEFAULT_YES = "Yes, use it by default";
const DEFAULT_NO = "No, just this time";
const QUESTION = "Which Notion page should the book go under?";

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

/**
 * The question a paused task waits on, read from the step's `needs_input` marker (the tasks API
 * already returns `input_data`), or null when no step waits for a detail. A task in
 * `awaiting_approval` with such a step is waiting for an ANSWER, not for an approval.
 */
export function pendingTaskInput(
  task: Pick<TaskDetailResponse, "subtasks"> | null,
): PendingTaskInput | null {
  for (const step of task?.subtasks ?? []) {
    const pause = step.input_data?.needs_input;
    if (!isRecord(pause) || typeof pause.ask_id !== "string") continue;
    const options = isRecord(pause.options) ? Object.keys(pause.options) : [];
    const questions: AskQuestion[] = [
      {
        question: QUESTION,
        header: "Notion page",
        options: options
          .slice(0, 6)
          .map((label) => ({ label: label.slice(0, 80), description: "" })),
        multiSelect: false,
        allowText: true,
      },
    ];
    if (pause.offer_default === true && options.length > 0) {
      questions.push({
        question: "Use this by default?",
        header: "Default",
        options: [
          { label: DEFAULT_YES, description: "" },
          { label: DEFAULT_NO, description: "" },
        ],
        multiSelect: false,
        allowText: false,
      });
    }
    return {
      subtaskId: step.id,
      request: { askId: pause.ask_id, source: "task", questions },
    };
  }
  return null;
}
