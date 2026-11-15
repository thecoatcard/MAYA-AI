import { XCircle } from "lucide-react";
import React from "react";

import { WrapText } from "../ui";
import { explainFailure } from "./taskStatus";

export interface FailureReasonProps {
  /** Raw error text from the task or step. */
  readonly error: string | null | undefined;
  /** Optional extra line, for example "Retried 2 times". */
  readonly note?: string;
  readonly className?: string;
  readonly summaryTestId?: string;
  readonly technicalTestId?: string;
}

/**
 * A failure as people read it: one plain sentence first, then the technical
 * text from the server inside a collapsible region that wraps (never cut off,
 * never a horizontal scrollbar).
 */
export function FailureReason({
  error,
  note,
  className,
  summaryTestId,
  technicalTestId,
}: FailureReasonProps): React.JSX.Element {
  const { summary, technical } = explainFailure(error);
  return (
    <div
      className={`min-w-0 rounded-maya-lg border border-maya-danger bg-maya-danger-soft p-3 text-maya-small text-maya-text ${className ?? ""}`.trim()}
    >
      <p className="m-0 flex min-w-0 items-start gap-2 font-medium">
        <XCircle
          aria-hidden="true"
          size={16}
          className="mt-0.5 shrink-0 text-maya-danger"
        />
        <span
          className="min-w-0 [overflow-wrap:anywhere]"
          data-testid={summaryTestId}
        >
          {summary}
        </span>
      </p>
      {note !== undefined && (
        <p className="m-0 mt-1 text-maya-caption text-maya-secondary">{note}</p>
      )}
      {technical !== null && (
        <details className="mt-2 min-w-0">
          <summary className="cursor-pointer text-maya-caption text-maya-secondary">
            Technical details
          </summary>
          <WrapText
            as="p"
            dir="auto"
            className="m-0 mt-1 block rounded-maya-md border border-maya-border bg-maya-code p-2 font-mono text-maya-mono text-maya-text"
            data-testid={technicalTestId}
          >
            {technical}
          </WrapText>
        </details>
      )}
    </div>
  );
}
