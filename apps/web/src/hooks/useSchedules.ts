"use client";

import { useCallback, useEffect, useRef, useState } from "react";

import {
  listSchedules,
  type Schedule,
  type ScheduleList,
  type ScheduleSettings,
} from "../lib/schedules-client";

export interface UseSchedulesResult {
  readonly items: readonly Schedule[];
  readonly settings: ScheduleSettings | null;
  /** Account facts from the list call; defaults until it loaded. */
  readonly meta: Pick<
    ScheduleList,
    | "account_email"
    | "mail_configured"
    | "max_schedules"
    | "verified_recipients"
  >;
  readonly loading: boolean;
  readonly failed: boolean;
  readonly reload: () => void;
  /** Replace one schedule in the list (after a save) or add it. */
  readonly upsert: (schedule: Schedule) => void;
  readonly remove: (id: string) => void;
  readonly setSettings: (settings: ScheduleSettings) => void;
}

/** The schedules and the run settings. Nothing is invented: before the load there are no items. */
export function useSchedules(): UseSchedulesResult {
  const [items, setItems] = useState<readonly Schedule[]>([]);
  const [settings, setSettings] = useState<ScheduleSettings | null>(null);
  const [meta, setMeta] = useState<UseSchedulesResult["meta"]>({
    account_email: null,
    mail_configured: true,
    max_schedules: null,
    verified_recipients: [],
  });
  const [loading, setLoading] = useState(true);
  const [failed, setFailed] = useState(false);
  const mounted = useRef(true);

  const reload = useCallback(() => {
    setLoading(true);
    setFailed(false);
    listSchedules()
      .then((list) => {
        if (!mounted.current) return;
        setItems(list.items);
        setSettings(list.settings);
        setMeta({
          account_email: list.account_email,
          mail_configured: list.mail_configured,
          max_schedules: list.max_schedules,
          verified_recipients: list.verified_recipients,
        });
        setLoading(false);
      })
      .catch(() => {
        if (!mounted.current) return;
        setFailed(true);
        setLoading(false);
      });
  }, []);

  useEffect(() => {
    mounted.current = true;
    reload();
    return () => {
      mounted.current = false;
    };
  }, [reload]);

  const upsert = useCallback((schedule: Schedule) => {
    setItems((prev) =>
      prev.some((s) => s.id === schedule.id)
        ? prev.map((s) => (s.id === schedule.id ? schedule : s))
        : [...prev, schedule],
    );
  }, []);
  const remove = useCallback((id: string) => {
    setItems((prev) => prev.filter((s) => s.id !== id));
  }, []);

  return {
    items,
    settings,
    meta,
    loading,
    failed,
    reload,
    upsert,
    remove,
    setSettings,
  };
}
