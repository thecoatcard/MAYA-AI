"use client";

import React, { useCallback, useEffect, useRef, useState } from "react";

import {
  listApprovals,
  type ApprovalRequest,
} from "../../lib/approvals-client";
import { Button } from "../ui";
import { InlineApproval } from "./InlineApproval";

export interface TaskApprovalsProps {
  readonly taskId: string;
  /** True while the task waits for approval; the requests load only then. */
  readonly waiting: boolean;
  /**
   * Changes whenever the caller re-read its task. The pending list is read
   * again then, so a decision made elsewhere disappears from here.
   */
  readonly refreshKey?: unknown;
  /** Called after a decision was recorded: refetch the task and the counters. */
  readonly onDecided?: () => void;
}

/**
 * The pending approvals of one task, each shown inline with Approve and Reject. Used by the chat
 * task card and by the task detail on the Tasks page, so every approval, whether it started in
 * chat, on the Tasks page, by voice or in the background, can be decided on its task.
 */
export function TaskApprovals({
  taskId,
  waiting,
  refreshKey,
  onDecided,
}: TaskApprovalsProps): React.JSX.Element | null {
  const [approvals, setApprovals] = useState<readonly ApprovalRequest[]>([]);
  const [loaded, setLoaded] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [decided, setDecided] = useState(false);
  const mounted = useRef(true);
  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  const load = useCallback(async (): Promise<void> => {
    try {
      const pending = await listApprovals("pending", 50);
      if (!mounted.current) return;
      setApprovals(pending.filter((a) => a.task_id === taskId));
      setError(null);
    } catch (err) {
      if (!mounted.current) return;
      setError(
        err instanceof Error && err.message !== ""
          ? err.message
          : "The approval request could not be loaded.",
      );
    } finally {
      if (mounted.current) setLoaded(true);
    }
  }, [taskId]);

  useEffect(() => {
    if (!waiting) {
      setApprovals([]);
      setLoaded(false);
      return;
    }
    void load();
  }, [waiting, refreshKey, load]);

  if (!waiting) return null;
  if (error !== null && approvals.length === 0) {
    return (
      <div role="alert" className="flex min-w-0 flex-col items-start gap-2">
        <p className="m-0 text-maya-small text-maya-danger">{error}</p>
        <Button size="sm" onClick={() => void load()}>
          Try again
        </Button>
      </div>
    );
  }
  if (!loaded) {
    return (
      <p role="status" className="m-0 text-maya-small text-maya-muted">
        Loading the approval request
      </p>
    );
  }
  if (approvals.length === 0 && decided) {
    return (
      <p role="status" className="m-0 text-maya-small text-maya-muted">
        Your decision was recorded. The task is updating.
      </p>
    );
  }
  if (approvals.length === 0) {
    return (
      <p role="status" className="m-0 text-maya-small text-maya-muted">
        No pending approval request was found for this task. It may have
        expired. Cancel the task and ask again.
      </p>
    );
  }
  return (
    <div className="flex min-w-0 flex-col gap-2">
      {approvals.map((approval) => (
        <InlineApproval
          key={approval.id}
          approval={approval}
          onDecided={() => {
            setDecided(true);
            onDecided?.();
          }}
        />
      ))}
    </div>
  );
}
