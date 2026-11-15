"use client";

import { CalendarClock, Check } from "lucide-react";
import Link from "next/link";
import React, { useCallback, useEffect, useId, useRef, useState } from "react";

import { ApiError } from "../../lib/api-client";
import {
  cancelScheduleDraft,
  confirmScheduleDraft,
  describeSchedulesError,
  getScheduleDraft,
  type ScheduleDraft,
} from "../../lib/schedules-client";
import { Button, Notice, Skeleton } from "../ui";

type CardState =
  | "loading"
  | "review"
  | "working"
  | "created"
  | "cancelled"
  | "gone"
  | "load-failed";

export interface ScheduleDraftCardProps {
  readonly draftId: string;
}

/**
 * A scheduled report MAYA drafted in chat: the exact plan in words, with Create schedule and
 * Cancel. Nothing is created until the button is clicked: there is no keyboard shortcut, no voice
 * path and no timer. The draft lives on the server, so the card is rebuilt from `draftId` after a
 * reload; a draft that was already used or has expired says so. It is visually distinct from the
 * approval card (info tint and a calendar icon) and has no animation.
 */
export function ScheduleDraftCard({
  draftId,
}: ScheduleDraftCardProps): React.JSX.Element {
  const titleId = useId();
  const [state, setState] = useState<CardState>("loading");
  const [draft, setDraft] = useState<ScheduleDraft | null>(null);
  const [error, setError] = useState<string | null>(null);
  const mounted = useRef(true);

  const load = useCallback(() => {
    setState("loading");
    setError(null);
    getScheduleDraft(draftId)
      .then((value) => {
        if (!mounted.current) return;
        setDraft(value);
        setState("review");
      })
      .catch((e: unknown) => {
        if (!mounted.current) return;
        // 404 or 410: already confirmed, cancelled or expired.
        if (e instanceof ApiError && (e.status === 404 || e.status === 410)) {
          setState("gone");
        } else {
          setError(describeSchedulesError(e));
          setState("load-failed");
        }
      });
  }, [draftId]);

  useEffect(() => {
    mounted.current = true;
    load();
    return () => {
      mounted.current = false;
    };
  }, [load]);

  const confirm = async (): Promise<void> => {
    setState("working");
    setError(null);
    try {
      await confirmScheduleDraft(draftId);
      if (mounted.current) setState("created");
    } catch (e) {
      if (!mounted.current) return;
      if (e instanceof ApiError && (e.status === 404 || e.status === 410)) {
        setState("gone");
        return;
      }
      setError(describeSchedulesError(e));
      setState("review");
    }
  };

  const cancel = async (): Promise<void> => {
    setState("working");
    setError(null);
    try {
      await cancelScheduleDraft(draftId);
      if (mounted.current) setState("cancelled");
    } catch (e) {
      if (!mounted.current) return;
      setError(describeSchedulesError(e));
      setState("review");
    }
  };

  const result =
    state === "created" ? (
      <p className="m-0 flex items-center gap-2 text-maya-small text-maya-text">
        <Check size={14} aria-hidden="true" />
        <span>
          Schedule created.{" "}
          <Link
            href="/settings#settings-schedules"
            className="text-maya-link underline"
          >
            Settings &gt; Schedules
          </Link>
        </span>
      </p>
    ) : state === "cancelled" ? (
      <p className="m-0 text-maya-small text-maya-secondary">
        Cancelled. No schedule was created.
      </p>
    ) : state === "gone" ? (
      <p className="m-0 text-maya-small text-maya-secondary">
        This draft is no longer waiting: it was already used, cancelled or has
        expired. Nothing was created from it now.
      </p>
    ) : null;

  return (
    <section
      aria-labelledby={titleId}
      data-testid="schedule-draft-card"
      data-state={state}
      className="grid min-w-0 gap-3 rounded-maya-lg border border-maya-info bg-maya-info-soft p-3 text-maya-text motion-none shadow-maya-1"
    >
      <div className="flex items-center justify-between gap-2">
        <h4
          id={titleId}
          className="m-0 flex items-center gap-2 text-maya-small font-medium"
        >
          <CalendarClock size={16} aria-hidden="true" />
          Scheduled report, not created yet
        </h4>
        <span
          className="maya-chip font-mono text-maya-caption text-maya-muted"
          data-testid="schedule-draft-status"
        >
          {state}
        </span>
      </div>

      {state === "loading" && (
        <div aria-busy="true" aria-label="Loading the schedule draft">
          <Skeleton lines={2} />
        </div>
      )}

      {draft !== null && (state === "review" || state === "working") && (
        <>
          <p
            dir="auto"
            data-testid="schedule-draft-preview"
            className="m-0 whitespace-pre-wrap text-maya-small [overflow-wrap:anywhere]"
          >
            {draft.preview_text}
          </p>
          <p className="m-0 text-maya-caption text-maya-secondary">
            Not created until you click.
          </p>
          <div className="flex flex-wrap items-start gap-2">
            <Button
              size="sm"
              variant="primary"
              loading={state === "working"}
              onClick={() => void confirm()}
            >
              Create schedule
            </Button>
            <Button
              size="sm"
              variant="ghost"
              disabled={state === "working"}
              onClick={() => void cancel()}
            >
              Cancel
            </Button>
          </div>
        </>
      )}

      {state === "load-failed" && (
        <div className="grid gap-2">
          <Notice tone="danger">{error}</Notice>
          <div>
            <Button size="sm" onClick={load}>
              Retry
            </Button>
          </div>
        </div>
      )}

      {error !== null && state === "review" && (
        <p role="alert" className="m-0 text-maya-small text-maya-danger">
          {error} Nothing was created.
        </p>
      )}

      <div aria-live="polite" role="status">
        {result}
      </div>
    </section>
  );
}
