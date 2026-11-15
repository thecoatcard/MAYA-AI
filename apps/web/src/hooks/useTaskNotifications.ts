/**
 * Tells the user about a task that finished or needs them while live voice is OFF.
 *
 * With voice on, MAYA speaks these updates herself (backend `app/voice/task_watch.py`), so this
 * hook stays quiet then. With voice off it polls the task list, compares each task with its last
 * known status and, on a change to done / failed / cancelled / waiting for approval, shows a toast
 * and raises the unseen count that the Tasks entry displays. The first poll only records the
 * current state, so opening the app never toasts old tasks. There is no desktop notification:
 * `apps/desktop` exposes no notification IPC (folder pick, reveal and open-artifact only).
 */

import { useCallback, useEffect, useRef, useState } from "react";

import type { ToastItem } from "../components/ui/Feedback";
import { listTasks, type TaskListItem } from "../lib/tasks-client";

export const TASK_NOTIFY_POLL_MS = 10_000;

type Milestone = "completed" | "failed" | "cancelled" | "approval";

const MILESTONE_BY_STATUS: Readonly<Record<string, Milestone>> = {
  completed: "completed",
  failed: "failed",
  cancelled: "cancelled",
  canceled: "cancelled",
  awaiting_approval: "approval",
};

const TITLE_MAX = 60;

function titleOf(task: TaskListItem): string {
  const raw = (task.title ?? task.instruction ?? "")
    .replace(/\s+/g, " ")
    .trim();
  if (raw === "") return "Your task";
  return raw.length > TITLE_MAX ? `${raw.slice(0, TITLE_MAX - 1)}…` : raw;
}

export function toastForTask(
  task: TaskListItem,
  milestone: Milestone,
): Omit<ToastItem, "id"> {
  const title = titleOf(task);
  switch (milestone) {
    case "completed":
      return { tone: "success", title: "Task done", message: title };
    case "failed":
      return { tone: "danger", title: "Task failed", message: title };
    case "cancelled":
      return { tone: "info", title: "Task cancelled", message: title };
    case "approval":
      return {
        tone: "warning",
        title: "Needs your approval",
        message: `${title}: use the Approve button on its card.`,
      };
  }
}

export interface UseTaskNotificationsOptions {
  readonly enabled: boolean;
  /** True while a live voice session is connected: MAYA speaks the update, so no toast or badge. */
  readonly voiceLive: boolean;
  readonly notify: (toast: Omit<ToastItem, "id">) => string;
  readonly pollIntervalMs?: number;
}

export interface UseTaskNotificationsResult {
  /** Updates seen while voice was off and not yet cleared (the Tasks entry badge). */
  readonly unseen: number;
  readonly clear: () => void;
}

export function useTaskNotifications({
  enabled,
  voiceLive,
  notify,
  pollIntervalMs = TASK_NOTIFY_POLL_MS,
}: UseTaskNotificationsOptions): UseTaskNotificationsResult {
  const [unseen, setUnseen] = useState<number>(0);
  const known = useRef<Map<string, string> | null>(null);
  const voiceLiveRef = useRef(voiceLive);
  voiceLiveRef.current = voiceLive;
  const notifyRef = useRef(notify);
  notifyRef.current = notify;

  const clear = useCallback((): void => setUnseen(0), []);

  useEffect(() => {
    if (!enabled) {
      known.current = null;
      setUnseen(0);
      return undefined;
    }
    let cancelled = false;

    async function poll(): Promise<void> {
      if (typeof document !== "undefined" && document.hidden) return;
      let items: readonly TaskListItem[];
      try {
        items = (await listTasks(undefined, 20)).items;
      } catch {
        return; // Keep the last known state; the Tasks view reports real errors.
      }
      if (cancelled) return;
      const previous = known.current;
      const next = new Map<string, string>();
      let fresh = 0;
      for (const task of items) {
        next.set(task.id, task.status);
        const milestone = MILESTONE_BY_STATUS[task.status];
        if (previous === null || milestone === undefined) continue;
        if (previous.get(task.id) === task.status) continue;
        if (voiceLiveRef.current) continue; // MAYA says it; nothing to show
        notifyRef.current(toastForTask(task, milestone));
        fresh += 1;
      }
      known.current = next;
      if (fresh > 0) setUnseen((count) => count + fresh);
    }

    void poll();
    const timer = setInterval(() => void poll(), pollIntervalMs);
    return () => {
      cancelled = true;
      clearInterval(timer);
    };
  }, [enabled, pollIntervalMs]);

  return { unseen, clear };
}
