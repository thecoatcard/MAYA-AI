"use client";

import React, { useEffect, useRef, useState } from "react";

import {
  listApprovals,
  type ApprovalRequest,
} from "../../lib/approvals-client";
import { CodeBlockShell } from "../ui";
import { NotionWritePreview } from "./NotionWritePreview";

/**
 * Shown on a task whose Notion write was approved by the owner's Settings option instead of a
 * click: says so plainly, keeps the exact payload visible, and links to the setting to undo it.
 * Renders nothing when the task had no auto-approval or the list could not be read.
 */
export function AutoApprovedNote({
  taskId,
  phase,
}: {
  readonly taskId: string;
  readonly phase: string;
}): React.JSX.Element | null {
  const [items, setItems] = useState<readonly ApprovalRequest[]>([]);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    // Read once the task finished, next to its result; a running task does no extra request.
    if (phase !== "completed" && phase !== "failed") {
      return () => {
        mounted.current = false;
      };
    }
    void (async () => {
      try {
        const all = await listApprovals(undefined, 100);
        if (mounted.current && Array.isArray(all)) {
          setItems(
            all.filter((a) => a.task_id === taskId && a.auto_approved === true),
          );
        }
      } catch {
        // Informational only: no note is shown when the list cannot be read.
      }
    })();
    return () => {
      mounted.current = false;
    };
  }, [taskId, phase]);

  if (items.length === 0) return null;
  return (
    <div
      className="flex min-w-0 flex-col gap-2"
      data-testid="auto-approved-note"
    >
      {items.map((approval) => (
        <section
          key={approval.id}
          aria-label="Auto-approved by your setting"
          className="motion-none flex min-w-0 flex-col gap-2 rounded-maya-lg border border-maya-border bg-maya-raised p-3 text-maya-small"
        >
          <p className="m-0 text-maya-text">
            <strong className="font-medium">
              Auto-approved by your setting.
            </strong>{" "}
            <a
              href="/settings#settings-connections"
              className="text-maya-secondary underline hover:text-maya-text"
            >
              Undo setting
            </a>
          </p>
          <NotionWritePreview args={approval.tool_args ?? {}} />
          <CodeBlockShell
            label="Exact tool arguments"
            maxHeight="14rem"
            dir="auto"
          >
            {JSON.stringify(approval.tool_args ?? {}, null, 2)}
          </CodeBlockShell>
        </section>
      ))}
    </div>
  );
}
