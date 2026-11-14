"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  ackScheduleUpdate,
  listScheduleUpdates,
  type ScheduleUpdate,
} from "../lib/schedules-client";

export interface UseScheduleUpdatesResult {
  readonly updates: readonly ScheduleUpdate[];
  /** True once the first load finished (success or not). */
  readonly loaded: boolean;
  /** Acknowledge one update. It leaves the list only after the server accepted it. */
  readonly dismiss: (scheduleId: string) => Promise<boolean>;
}

/**
 * The scheduled updates not yet announced to the person, fetched once when `enabled` turns true
 * (after login). A failed load shows nothing: a missing notice is better than a wrong one.
 */
export function useScheduleUpdates(enabled: boolean): UseScheduleUpdatesResult {
  const [updates, setUpdates] = useState<readonly ScheduleUpdate[]>([]);
  const [loaded, setLoaded] = useState(false);
  const mounted = useRef(true);
  const fetched = useRef(false);
  const updatesRef = useRef<readonly ScheduleUpdate[]>([]);
  updatesRef.current = updates;

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);

  useEffect(() => {
    if (!enabled || fetched.current) return;
    fetched.current = true;
    listScheduleUpdates()
      .then((items) => {
        if (!mounted.current) return;
        setUpdates(items);
        setLoaded(true);
      })
      .catch(() => {
        if (mounted.current) setLoaded(true);
      });
  }, [enabled]);

  const dismiss = useCallback(async (scheduleId: string) => {
    // An update MAYA is to speak is marked announced by the backend after the spoken turn: hiding
    // it here must not acknowledge it, or the voice would never read it.
    const speaks =
      updatesRef.current.find((u) => u.schedule_id === scheduleId)
        ?.voice_on_open === true;
    if (!speaks) {
      try {
        await ackScheduleUpdate(scheduleId);
      } catch {
        return false;
      }
    }
    if (mounted.current) {
      setUpdates((prev) => prev.filter((u) => u.schedule_id !== scheduleId));
    }
    return true;
  }, []);

  return { updates, loaded, dismiss };
}
