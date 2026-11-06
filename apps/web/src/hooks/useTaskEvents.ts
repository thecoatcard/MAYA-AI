/**
 * React hook for consuming live task SSE progress events (Phase 06).
 */

import { useEffect, useRef, useState, useCallback } from "react";
import { getAccessToken, refreshSession } from "../lib/auth-session";
import { readSseFrames } from "../lib/sse";

export interface TaskEventPayload {
  readonly event_type: string;
  readonly session_id?: string;
  readonly workspace_id?: string;
  readonly user_id?: string;
  readonly timestamp?: string;
  readonly payload?: Record<string, unknown>;
}

export interface UseTaskEventsOptions {
  readonly taskId: string | null;
  readonly enabled?: boolean;
  readonly onEvent?: (event: TaskEventPayload) => void;
}

export interface UseTaskEventsResult {
  readonly isConnected: boolean;
  readonly isReconnecting: boolean;
  readonly events: readonly TaskEventPayload[];
  readonly lastEvent: TaskEventPayload | null;
  readonly error: Error | null;
  readonly clearEvents: () => void;
}

export function useTaskEvents({
  taskId,
  enabled = true,
  onEvent,
}: UseTaskEventsOptions): UseTaskEventsResult {
  const [isConnected, setIsConnected] = useState<boolean>(false);
  const [isReconnecting, setIsReconnecting] = useState<boolean>(false);
  const [events, setEvents] = useState<readonly TaskEventPayload[]>([]);
  const [lastEvent, setLastEvent] = useState<TaskEventPayload | null>(null);
  const [error, setError] = useState<Error | null>(null);

  const onEventRef = useRef(onEvent);
  onEventRef.current = onEvent;

  const clearEvents = useCallback(() => {
    setEvents([]);
    setLastEvent(null);
  }, []);

  useEffect(() => {
    if (!taskId || !enabled) {
      setIsConnected(false);
      setIsReconnecting(false);
      return;
    }

    const abortController = new AbortController();
    let isCancelled = false;

    async function streamEvents(): Promise<void> {
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

        const response = await fetch(`/api/v1/tasks/${taskId}/events`, {
          headers,
          signal: abortController.signal,
        });

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

        for await (const frame of readSseFrames(
          response.body,
          abortController.signal,
        )) {
          if (isCancelled) break;
          if (!frame.data) continue;

          try {
            const parsed = JSON.parse(frame.data) as TaskEventPayload;
            setLastEvent(parsed);
            setEvents((prev) => [...prev, parsed]);
            if (onEventRef.current) {
              onEventRef.current(parsed);
            }
          } catch {
            // Heartbeat or non-json message, ignore
          }
        }
      } catch (err) {
        if (!isCancelled && !abortController.signal.aborted) {
          setError(err instanceof Error ? err : new Error(String(err)));
          setIsConnected(false);
        }
      } finally {
        if (!isCancelled) {
          setIsConnected(false);
        }
      }
    }

    void streamEvents();

    return () => {
      isCancelled = true;
      abortController.abort();
      setIsConnected(false);
    };
  }, [taskId, enabled]);

  return {
    isConnected,
    isReconnecting,
    events,
    lastEvent,
    error,
    clearEvents,
  };
}
