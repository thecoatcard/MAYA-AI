"use client";

import { CalendarClock, MicOff } from "lucide-react";
import React, { useEffect, useRef, useState } from "react";

import { useScheduleUpdates } from "../../hooks/useScheduleUpdates";
import {
  describeMailStatus,
  syncDeviceTimezone,
  type ScheduleUpdate,
} from "../../lib/schedules-client";
import { Badge, Button } from "../ui";
import { useConversationRuntime } from "./ConversationContext";

export const READING_NOTICE =
  "MAYA is reading your scheduled update. Your mic is muted.";
export const BLOCKED_NOTICE =
  "MAYA did not read the update aloud because the browser blocked audio or microphone permission was denied. You can read it in the Scheduled report conversation.";

type Gate = "ok" | "needs-click" | "blocked";

/** Whether voice may start by itself: not when permission is denied or audio needs a click first. */
async function voiceGate(): Promise<Gate> {
  try {
    const permissions = (navigator as Navigator).permissions;
    if (permissions?.query) {
      const status = await permissions.query({
        name: "microphone" as PermissionName,
      });
      if (status.state === "denied") return "blocked";
    }
  } catch {
    /* the Permissions API may not know "microphone": carry on */
  }
  const activation = (
    navigator as Navigator & { userActivation?: { hasBeenActive: boolean } }
  ).userActivation;
  if (activation !== undefined && !activation.hasBeenActive) {
    return "needs-click";
  }
  return "ok";
}

/** Whether an update needs the person to know something beyond "your report is ready". */
function hasProblem(update: ScheduleUpdate): boolean {
  return (
    update.needs_attention ||
    update.status === "failed" ||
    update.reason !== "" ||
    describeMailStatus(update.mail_status) !== ""
  );
}

/**
 * A single quiet line for an update that went wrong. The report itself is never shown here: it
 * lives in its own conversation ("Scheduled report ...") in the sidebar.
 */
function UpdateNote({
  update,
  onDismiss,
}: {
  readonly update: ScheduleUpdate;
  readonly onDismiss: () => Promise<boolean>;
}): React.JSX.Element {
  const [busy, setBusy] = useState(false);
  const [failed, setFailed] = useState(false);
  const mail = describeMailStatus(update.mail_status);
  return (
    <li
      data-testid="schedule-update"
      className="flex min-w-0 flex-wrap items-center gap-2 text-maya-small text-maya-secondary"
    >
      <CalendarClock size={14} aria-hidden="true" className="shrink-0" />
      <span dir="auto" className="min-w-0 font-medium [overflow-wrap:anywhere]">
        {update.name}
      </span>
      {update.needs_attention && <Badge tone="warning">Needs attention</Badge>}
      {update.status === "failed" && <Badge tone="danger">Failed</Badge>}
      {update.reason !== "" && (
        <span
          dir="auto"
          data-testid="update-reason"
          className="[overflow-wrap:anywhere]"
        >
          {update.reason}
        </span>
      )}
      {mail !== "" && (
        <span data-testid="update-mail-status">Mail not sent: {mail}.</span>
      )}
      <Button
        size="sm"
        variant="ghost"
        loading={busy}
        aria-label={`Dismiss ${update.name}`}
        onClick={() => {
          setBusy(true);
          setFailed(false);
          void onDismiss().then((ok) => {
            if (!ok) {
              setBusy(false);
              setFailed(true);
            }
          });
        }}
      >
        Dismiss
      </Button>
      {failed && (
        <span role="alert" className="text-maya-danger">
          Could not dismiss. Try again.
        </span>
      )}
    </li>
  );
}

/**
 * Scheduled reports that finished while MAYA was closed. The report is never drawn over the
 * conversation: it lives in its own "Scheduled report" conversation. With "Speak the update by
 * voice when I open MAYA" on, MAYA tells the person by voice (listen-only, the microphone is never
 * requested). Only what needs attention (a failure, a mail problem, a blocked voice) shows as a
 * quiet line; a clean update that is not spoken is acknowledged silently. Static: no motion.
 */
export function ScheduledUpdatesNotice(): React.JSX.Element | null {
  const runtime = useConversationRuntime();
  const { updates, dismiss } = useScheduleUpdates(true);
  const [gate, setGate] = useState<Gate | null>(null);
  const [reading, setReading] = useState(false);
  const attempted = useRef(false);
  const autoAcked = useRef(new Set<string>());
  const startListenOnly = runtime.startListenOnlyMaster;
  const voiceState = runtime.voice.state;
  const voiceIdle =
    voiceState === "idle" ||
    voiceState === "unavailable" ||
    voiceState === "error";

  // Once per app start: tell the server the device time zone when it differs.
  useEffect(() => {
    void syncDeviceTimezone();
  }, []);

  // MAYA only tells; the report is read from its conversation. A finished update that will not be
  // spoken and has nothing to warn about is acknowledged at once, so it is not announced again.
  useEffect(() => {
    for (const update of updates) {
      if (
        update.voice_on_open ||
        hasProblem(update) ||
        autoAcked.current.has(update.schedule_id)
      ) {
        continue;
      }
      autoAcked.current.add(update.schedule_id);
      void dismiss(update.schedule_id);
    }
  }, [updates, dismiss]);

  // The server decides per update whether MAYA speaks it (the "voice on open" setting, resolved).
  const hasUpdates = updates.length > 0;
  const wantsVoice = updates.some((u) => u.voice_on_open);
  useEffect(() => {
    if (!wantsVoice || attempted.current || startListenOnly === undefined) {
      return;
    }
    attempted.current = true;
    let cancelled = false;
    void (async () => {
      const result = await voiceGate();
      if (cancelled) return;
      setGate(result);
      if (result === "ok") {
        startListenOnly();
        setReading(true);
      }
    })();
    return () => {
      cancelled = true;
    };
  }, [wantsVoice, startListenOnly]);

  // The reading line goes away once the session has been live and then ends. A render between the
  // start call and the first non-idle state must not clear it.
  const seenLive = useRef(false);
  useEffect(() => {
    if (!reading) {
      seenLive.current = false;
    } else if (!voiceIdle) {
      seenLive.current = true;
    } else if (seenLive.current) {
      setReading(false);
    }
  }, [reading, voiceIdle]);

  if (!hasUpdates) return null;

  const showReading =
    reading && runtime.voiceLive && (runtime.voice.muted ?? false);
  const notes = updates.filter(hasProblem);
  const visible =
    showReading ||
    (reading && voiceState === "connecting") ||
    gate === "blocked" ||
    (gate === "needs-click" && !reading) ||
    notes.length > 0;
  // Nothing to say: no card, no overlay. MAYA tells by voice; the report is in its conversation.
  if (!visible) return null;

  return (
    <section
      aria-label="Scheduled updates"
      data-testid="scheduled-updates"
      className="grid min-w-0 gap-1 border-b border-maya-border px-4 py-2"
    >
      {(showReading || (reading && voiceState === "connecting")) && (
        <div
          role="status"
          data-testid="schedule-reading"
          className="flex min-w-0 flex-wrap items-center gap-2 text-maya-small text-maya-secondary"
        >
          <MicOff size={14} aria-hidden="true" />
          <span>{READING_NOTICE}</span>
          {runtime.unmuteMaster !== undefined && (
            <Button size="sm" onClick={runtime.unmuteMaster}>
              Unmute
            </Button>
          )}
        </div>
      )}
      {gate === "blocked" && (
        <p
          role="status"
          data-testid="schedule-voice-blocked"
          className="m-0 text-maya-small text-maya-secondary"
        >
          {BLOCKED_NOTICE}
        </p>
      )}
      {gate === "needs-click" && !reading && (
        <div
          role="status"
          className="flex flex-wrap items-center gap-2 text-maya-small text-maya-secondary"
        >
          <span>The browser needs a click before MAYA can speak.</span>
          <Button
            size="sm"
            onClick={() => {
              runtime.startListenOnlyMaster?.();
              setReading(true);
            }}
          >
            Read it aloud
          </Button>
        </div>
      )}
      <ul className="m-0 grid list-none gap-1 p-0">
        {notes.map((update) => (
          <UpdateNote
            key={update.schedule_id}
            update={update}
            onDismiss={() => dismiss(update.schedule_id)}
          />
        ))}
      </ul>
    </section>
  );
}
