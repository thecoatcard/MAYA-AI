/**
 * React hook for consuming live task SSE progress events (Phase 06).
 *
 * Implements automatic reconnect with exponential backoff, 401 token refresh retry,
 * task_cancelled event handling, and a polling fallback that refetches task detail
 * on reconnect or when the stream is unavailable (recovering state since Redis pub/sub has no replay).
 */

import { useCallback, useEffect, useRef, useState } from "react";
import { ApiError } from "../lib/api-client";
import { getAccessToken, refreshSession } from "../lib/auth-session";
import { readSseFrames } from "../lib/sse";
import { getTask, type TaskDetailResponse } from "../lib/tasks-client";

export interface TaskEventPayload {
  readonly event_type: string;
  readonly session_id?: string;
  readonly workspace_id?: string;
  readonly user_id?: string;
  readonly timestamp?: string;
  readonly payload?: Record<string, unknown>;
  readonly status?: string;
}

export interface UseTaskEventsOptions {
  readonly taskId: string | null;
  readonly enabled?: boolean;
  readonly onEvent?: (event: TaskEventPayload) => void;
  readonly pollIntervalMs?: number;
}

export interface UseTaskEventsResult {
  readonly isConnected: boolean;
  readonly isReconnecting: boolean;
  readonly events: readonly TaskEventPayload[];
  readonly lastEvent: TaskEventPayload | null;
  readonly taskDetail: TaskDetailResponse | null;
  readonly error: Error | null;
  /** True when the server says the task does not exist (for example it was deleted). */
  readonly taskGone?: boolean;
  readonly clearEvents: () => void;
  readonly refetchTask: () => Promise<TaskDetailResponse | null>;
}

export function useTaskEvents({
  taskId,
  enabled = true,
  onEvent,
  pollIntervalMs = 3000,
}: UseTaskEventsOptions): UseTaskEventsResult {
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [isReconnecting, setIsReconnecting] = useState<boolean>(false);
  const [events, setEvents] = useState<readonly TaskEventPayload[]>([]);
  const [lastEvent, setLastEvent] = useState<TaskEventPayload | null>(null);
  const [taskDetail, setTaskDetail] = useState<TaskDetailResponse | null>(null);
  const [error, setError] = useState<Error | null>(null);
  const [taskGone, setTaskGone] = useState<boolean>(false);

  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;
  const taskIdRef = useRef(taskId);
  taskIdRef.current = taskId;

  const clearEvents = useCallback(() => {
    setEvents([]);
    setLastEvent(null);
  }, []);

  const refetchTask =
    useCallback(async (): Promise<TaskDetailResponse | null> => {
      if (!taskId) return null;
      try {
        const detail = await getTask(taskId);
        // A slow answer for a task that is no longer the one watched must not replace it.
        if (taskIdRef.current === taskId) setTaskDetail(detail);
        return detail;
      } catch (err) {
        if (
          taskIdRef.current === taskId &&
          err instanceof ApiError &&
          err.status === 404
        ) {
          setTaskGone(true);
        }
        return null;
      }
    }, [taskId]);

  useEffect(() => {
    // Each task owns its own state: switching to another task never shows the previous task's
    // detail, events or error under the new one.
    setEvents([]);
    setLastEvent(null);
    setTaskDetail(null);
    setError(null);
    setTaskGone(false);
  }, [taskId]);

  useEffect(() => {
    if (!taskId || !enabled) {
      setIsConnected(false);
      setIsReconnecting(false);
      return;
    }

    let isCancelled = false;
    let reconnectTimeout: ReturnType<typeof setTimeout> | null = null;
    let pollInterval: ReturnType<typeof setInterval> | null = null;
    let currentAbortController: AbortController | null = null;
    let backoffDelayMs = 1000;
    const maxBackoffMs = 10000;

    async function refetchTaskDetail(): Promise<TaskDetailResponse | null> {
      if (!taskId || isCancelled) return null;
      try {
        const detail = await getTask(taskId);
        if (!isCancelled) {
          setTaskDetail(detail);
        }
        return detail;
      } catch (err) {
        if (!isCancelled && err instanceof ApiError && err.status === 404) {
          setTaskGone(true);
        }
        return null;
      }
    }

    function scheduleReconnect(): void {
      if (isCancelled) return;

      // Start polling fallback while disconnected/reconnecting
      if (!pollInterval) {
        pollInterval = setInterval(() => {
          void refetchTaskDetail();
        }, pollIntervalMs);
      }

      const delay = backoffDelayMs;
      backoffDelayMs = Math.min(backoffDelayMs * 2, maxBackoffMs);

      reconnectTimeout = setTimeout(() => {
        if (!isCancelled) {
          void streamEvents();
        }
      }, delay);
    }

    async function streamEvents(): Promise<void> {
      if (isCancelled) return;
      currentAbortController = new AbortController();

      try {
        let token = getAccessToken();
        if (!token) {
          const refreshed = await refreshSession();
          token = refreshed ? getAccessToken() : null;
        }

        const headers = new Headers();
        if (token) {
          headers.set("Authorization", `Bearer ${token}`);
        }

        let response = await fetch(`/api/v1/tasks/${taskId}/events`, {
          headers,
          signal: currentAbortController.signal,
        });

        // 401 refresh and retry path
        if (response.status === 401) {
          const refreshed = await refreshSession();
          if (refreshed) {
            const retryToken = getAccessToken();
            if (retryToken) {
              headers.set("Authorization", `Bearer ${retryToken}`);
              response = await fetch(`/api/v1/tasks/${taskId}/events`, {
                headers,
                signal: currentAbortController.signal,
              });
            }
          }
        }

        if (!response.ok) {
          throw new Error(
            `SSE stream connection failed with status ${response.status}`,
          );
        }

        if (!response.body) {
          throw new Error("Response body is not readable for SSE");
        }

        if (isCancelled) return;
        setIsConnected(true);
        setIsReconnecting(false);
        setError(null);
        backoffDelayMs = 1000; // Reset backoff on connection success

        // Clear polling fallback interval once stream is successfully established
        if (pollInterval) {
          clearInterval(pollInterval);
          pollInterval = null;
        }

        // Refetch task detail to sync state missed during disconnection
        void refetchTaskDetail();

        for await (const frame of readSseFrames(
          response.body,
          currentAbortController.signal,
        )) {
          if (isCancelled) break;
          if (!frame.data) continue;

          try {
            const parsed = JSON.parse(frame.data) as TaskEventPayload;

            // Handle task_cancelled event (status cancelled, not failed)
            if (parsed.event_type === "task_cancelled") {
              const updatedPayload = {
                status: "cancelled",
                ...(parsed.payload || {}),
              };
              const normalizedEvent: TaskEventPayload = {
                ...parsed,
                status: "cancelled",
                payload: updatedPayload,
              };
              setLastEvent(normalizedEvent);
              setEvents((prev) => [...prev, normalizedEvent]);
              onEventRef.current?.(normalizedEvent);
              continue;
            }

            setLastEvent(parsed);
            setEvents((prev) => [...prev, parsed]);
            onEventRef.current?.(parsed);
          } catch {
            // Heartbeat or non-json message, ignore
          }
        }
      } catch (err) {
        if (!isCancelled && !currentAbortController.signal.aborted) {
          setError(err instanceof Error ? err : new Error(String(err)));
        }
      } finally {
        if (!isCancelled) {
          setIsConnected(false);
          setIsReconnecting(true);
          scheduleReconnect();
        }
      }
    }

    void streamEvents();

    return () => {
      isCancelled = true;
      if (reconnectTimeout) {
        clearTimeout(reconnectTimeout);
      }
      if (pollInterval) {
        clearInterval(pollInterval);
      }
      currentAbortController?.abort();
      setIsConnected(false);
      setIsReconnecting(false);
    };
  }, [taskId, enabled, pollIntervalMs]);

  return {
    isConnected,
    isReconnecting,
    events,
    lastEvent,
    taskDetail,
    error,
    taskGone,
    clearEvents,
    refetchTask,
  };
}
