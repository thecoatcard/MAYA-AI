"use client";

import React, { useState } from "react";

import type { AskAnswerItem } from "../../lib/ask-user";
import type { PendingTaskInput } from "../../lib/task-input";
import { answerTaskInput } from "../../lib/tasks-client";
import { AskUserCard } from "../chat/AskUserCard";

export interface TaskInputCardProps {
  readonly taskId: string;
  readonly pending: PendingTaskInput;
  /** Called after the answer was accepted: refetch the task. */
  readonly onAnswered?: () => void;
}

/**
 * The question of a task whose step paused for a detail (for example the Notion page), shown on
 * the task card with the real options. Answering resumes the same step. It is a question, never
 * an approval: nothing is approved here.
 */
export function TaskInputCard({
  taskId,
  pending,
  onAnswered,
}: TaskInputCardProps): React.JSX.Element {
  const [submitting, setSubmitting] = useState(false);
  const [sent, setSent] = useState(false);
  const [error, setError] = useState<string | null>(null);

  async function submit(answers: readonly AskAnswerItem[]): Promise<void> {
    setSubmitting(true);
    setError(null);
    try {
      await answerTaskInput(taskId, pending.subtaskId, answers);
      setSent(true);
      onAnswered?.();
    } catch (err) {
      setError(
        err instanceof Error && err.message !== ""
          ? err.message
          : "The answer could not be sent. Try again.",
      );
    } finally {
      setSubmitting(false);
    }
  }

  if (sent) {
    return (
      <p role="status" className="m-0 text-maya-small text-maya-muted">
        Your answer was sent. The task is continuing.
      </p>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-2" data-testid="task-input">
      <AskUserCard
        request={pending.request}
        state="awaiting_input"
        submitting={submitting}
        autoFocus={false}
        onSkip={() => undefined}
        onSubmit={(answers) => void submit(answers)}
      />
      {error !== null && (
        <p role="alert" className="m-0 text-maya-small text-maya-danger">
          {error}
        </p>
      )}
    </div>
  );
}
