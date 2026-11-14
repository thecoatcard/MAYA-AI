"use client";

import {
  CalendarClock,
  CopyPlus,
  FileText,
  Mail,
  Pause,
  Play,
  Pencil,
  Trash2,
} from "lucide-react";
import Link from "next/link";
import React, { useEffect, useId, useRef, useState } from "react";

import { useSchedules } from "../../hooks/useSchedules";
import {
  canRegisterScheduleWake,
  registerScheduleWake,
} from "../../lib/desktop-bridge";
import {
  createSchedule,
  deleteSchedule,
  describeCadence,
  describeSchedulesError,
  formatLabel,
  runScheduleNow,
  syncDeviceTimezone,
  updateSchedule,
  updateScheduleSettings,
  type RunMode,
  type Schedule,
  type ScheduleInput,
} from "../../lib/schedules-client";
import {
  Badge,
  Button,
  ConfirmDeleteDialog,
  EmptyState,
  GlassModal,
  MenuSurface,
  Notice,
  Skeleton,
  Switch,
  type ContextMenuEntry,
  type Tone,
} from "../ui";
import { ReportBox } from "../chat/ReportBox";
import { SwitchRow, SettingsCard } from "./primitives";
import { ScheduleForm } from "./ScheduleForm";

function formatWhen(iso: string | null): string {
  if (iso === null || iso === "") return "";
  const date = new Date(iso);
  if (Number.isNaN(date.getTime())) return iso;
  return date.toLocaleString(undefined, {
    dateStyle: "medium",
    timeStyle: "short",
  });
}

const RUN_TONE: Record<string, { tone: Tone; label: string }> = {
  done: { tone: "success", label: "Done" },
  failed: { tone: "danger", label: "Failed" },
  needs_attention: { tone: "warning", label: "Needs attention" },
  skipped: { tone: "neutral", label: "Skipped" },
};

export interface SchedulesSectionProps {
  /** The signed-in account e-mail, when the server provides it. */
  readonly accountEmail?: string | null;
}

/**
 * Settings > Schedules: the scheduled reports as cards, the run-mode choice and the voice-on-open
 * switch. Every state is stated in words. Nothing runs here: the server owns the schedule.
 */
export function SchedulesSection({
  accountEmail = null,
}: SchedulesSectionProps): React.JSX.Element {
  const ids = useId();
  const store = useSchedules();
  const [editing, setEditing] = useState<Schedule | "new" | null>(null);
  const [deleting, setDeleting] = useState<Schedule | null>(null);
  const [duplicateOf, setDuplicateOf] = useState<Schedule | null>(null);
  const [reportOf, setReportOf] = useState<Schedule | null>(null);
  const [deleteBusy, setDeleteBusy] = useState(false);
  const [deleteError, setDeleteError] = useState<string | null>(null);
  const [notice, setNotice] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [busyId, setBusyId] = useState<string | null>(null);
  const [saveText, setSaveText] = useState("");
  const [confirmWake, setConfirmWake] = useState(false);
  const [wakeBusy, setWakeBusy] = useState(false);
  const [wakeError, setWakeError] = useState<string | null>(null);
  const mounted = useRef(true);
  const cancelWakeRef = useRef<HTMLButtonElement>(null);
  const desktop = canRegisterScheduleWake();

  useEffect(() => {
    mounted.current = true;
    return () => {
      mounted.current = false;
    };
  }, []);
  useEffect(() => {
    if (confirmWake) cancelWakeRef.current?.focus();
  }, [confirmWake]);

  const settings = store.settings;
  const limitReached =
    store.meta.max_schedules !== null &&
    store.items.length >= store.meta.max_schedules;

  // First load of this page: tell the server the device time zone when it differs.
  const settingsTz = settings === null ? undefined : settings.timezone;
  useEffect(() => {
    if (settingsTz === undefined) return;
    void syncDeviceTimezone(settingsTz, true);
  }, [settingsTz]);

  const togglePause = async (schedule: Schedule): Promise<void> => {
    setBusyId(schedule.id);
    setError(null);
    try {
      const saved = await updateSchedule(schedule.id, {
        enabled: !schedule.enabled,
      });
      if (mounted.current) store.upsert(saved);
    } catch (e) {
      if (mounted.current) setError(describeSchedulesError(e));
    } finally {
      if (mounted.current) setBusyId(null);
    }
  };

  const scheduleItems = (schedule: Schedule): ContextMenuEntry[] => {
    const run = schedule.last_run;
    const busy = busyId === schedule.id;
    return [
      {
        id: "run",
        label: "Run now",
        icon: <Play size={14} />,
        disabled: busy,
        onSelect: () => void runNow(schedule),
      },
      {
        id: "pause",
        label: schedule.enabled ? "Pause" : "Resume",
        icon: schedule.enabled ? <Pause size={14} /> : <Play size={14} />,
        disabled: busy,
        onSelect: () => void togglePause(schedule),
      },
      {
        id: "edit",
        label: "Edit",
        icon: <Pencil size={14} />,
        onSelect: () => setEditing(schedule),
      },
      {
        id: "duplicate",
        label: "Duplicate",
        icon: <CopyPlus size={14} />,
        disabled: limitReached,
        disabledReason: "The schedule limit is reached. Delete one first.",
        onSelect: () => {
          setDuplicateOf(schedule);
          setEditing("new");
        },
      },
      {
        id: "report",
        label: "View last report",
        icon: <FileText size={14} />,
        disabled: run === null || run.summary === "",
        disabledReason: "No report yet.",
        onSelect: () => setReportOf(schedule),
      },
      { kind: "separator" },
      {
        id: "delete",
        label: "Delete",
        icon: <Trash2 size={14} />,
        danger: true,
        onSelect: () => {
          setDeleteError(null);
          setDeleting(schedule);
        },
      },
    ];
  };

  const runNow = async (schedule: Schedule): Promise<void> => {
    setBusyId(schedule.id);
    setError(null);
    setNotice(null);
    try {
      await runScheduleNow(schedule.id);
      if (mounted.current) {
        setNotice(
          `"${schedule.name}" is running now. The result shows here and in Tasks when it finishes.`,
        );
      }
    } catch (e) {
      if (mounted.current) setError(describeSchedulesError(e));
    } finally {
      if (mounted.current) setBusyId(null);
    }
  };

  const confirmDelete = async (): Promise<void> => {
    if (deleting === null) return;
    setDeleteBusy(true);
    setDeleteError(null);
    try {
      await deleteSchedule(deleting.id);
      if (!mounted.current) return;
      store.remove(deleting.id);
      setDeleting(null);
    } catch (e) {
      if (mounted.current) setDeleteError(describeSchedulesError(e));
    } finally {
      if (mounted.current) setDeleteBusy(false);
    }
  };

  const saveForm = async (
    input: ScheduleInput,
    voiceOnOpen: boolean,
  ): Promise<string | null> => {
    try {
      const saved =
        editing === "new" || editing === null
          ? await createSchedule(input)
          : await updateSchedule(editing.id, input);
      store.upsert(saved);
      if (settings !== null && settings.voice_on_open !== voiceOnOpen) {
        try {
          store.setSettings(
            await updateScheduleSettings({ voice_on_open: voiceOnOpen }),
          );
        } catch (e) {
          setEditing(null);
          setError(
            `The schedule was saved, but the voice setting was not: ${describeSchedulesError(e)}`,
          );
          return null;
        }
      }
      if (mounted.current) setEditing(null);
      return null;
    } catch (e) {
      return describeSchedulesError(e);
    }
  };

  const changeVoice = async (): Promise<void> => {
    if (settings === null) return;
    setSaveText("Saving…");
    setError(null);
    try {
      store.setSettings(
        await updateScheduleSettings({
          voice_on_open: !settings.voice_on_open,
        }),
      );
      if (mounted.current) setSaveText("Saved");
    } catch (e) {
      if (mounted.current) {
        setSaveText("");
        setError(describeSchedulesError(e));
      }
    }
  };

  const chooseMode = async (mode: RunMode): Promise<void> => {
    if (settings === null || settings.run_mode === mode) return;
    if (mode === "wake_pc") {
      setWakeError(null);
      setConfirmWake(true);
      return;
    }
    setError(null);
    try {
      // Leaving the wake mode also removes the Windows task, so nothing stays behind.
      if (settings.wake_registered && desktop) {
        const removed = await registerScheduleWake(false);
        if (!removed.ok) {
          setError(
            "The Windows Scheduled Task could not be removed. Run mode was not changed.",
          );
          return;
        }
      }
      store.setSettings(
        await updateScheduleSettings({
          run_mode: mode,
          ...(desktop ? { wake_registered: false } : {}),
        }),
      );
    } catch (e) {
      if (mounted.current) setError(describeSchedulesError(e));
    }
  };

  const enableWake = async (): Promise<void> => {
    setWakeBusy(true);
    setWakeError(null);
    const result = await registerScheduleWake(true);
    if (!result.ok) {
      if (mounted.current) {
        setWakeBusy(false);
        setWakeError(
          "The Windows Scheduled Task could not be created. Run mode was not changed.",
        );
      }
      return;
    }
    try {
      const saved = await updateScheduleSettings({
        run_mode: "wake_pc",
        wake_registered: true,
      });
      if (mounted.current) store.setSettings(saved);
      if (mounted.current) setConfirmWake(false);
    } catch (e) {
      // Keep the two sides in step: undo the task when the server did not accept the mode.
      await registerScheduleWake(false);
      if (mounted.current) setWakeError(describeSchedulesError(e));
    } finally {
      if (mounted.current) setWakeBusy(false);
    }
  };

  if (store.failed) {
    return (
      <div className="grid gap-2" data-testid="schedules-section">
        <Notice tone="danger">
          Schedules could not be loaded. Nothing was changed.
        </Notice>
        <div>
          <Button size="sm" onClick={store.reload}>
            Retry
          </Button>
        </div>
      </div>
    );
  }
  if (store.loading || settings === null) {
    return (
      <div aria-busy="true" aria-label="Loading schedules">
        <Skeleton lines={3} />
      </div>
    );
  }

  return (
    <div className="grid min-w-0 gap-4" data-testid="schedules-section">
      {error !== null && (
        <Notice tone="danger" onDismiss={() => setError(null)}>
          {error}
        </Notice>
      )}
      {notice !== null && (
        <Notice tone="info" onDismiss={() => setNotice(null)}>
          {notice}
        </Notice>
      )}

      <SettingsCard
        title="When schedules run"
        description="A schedule runs on this PC, through the MAYA backend."
      >
        <div
          role="radiogroup"
          aria-labelledby={`${ids}-mode`}
          className="grid min-w-0 gap-3 px-4 py-3"
        >
          <p id={`${ids}-mode`} className="m-0 text-maya-body font-medium">
            Run mode
          </p>
          <label className="flex min-w-0 items-start gap-2 text-maya-small text-maya-text">
            <input
              type="radio"
              name={`${ids}-run-mode`}
              className="mt-1"
              checked={settings.run_mode === "app_open"}
              onChange={() => void chooseMode("app_open")}
            />
            <span>
              While MAYA is open, and catch up when I start it (default)
            </span>
          </label>
          <label className="flex min-w-0 items-start gap-2 text-maya-small text-maya-text">
            <input
              type="radio"
              name={`${ids}-run-mode`}
              className="mt-1"
              checked={settings.run_mode === "wake_pc"}
              disabled={!desktop}
              aria-describedby={`${ids}-wake-help`}
              onChange={() => void chooseMode("wake_pc")}
            />
            <span>Also wake my PC to run schedules (experimental)</span>
          </label>
          <p
            id={`${ids}-wake-help`}
            className="m-0 pl-6 text-maya-small text-maya-secondary"
            data-testid="wake-help"
          >
            {desktop
              ? settings.wake_registered
                ? "A Windows Scheduled Task is set up. Choose the first option to remove it."
                : "Needs Docker Desktop running, the PC on and you logged in. It creates a Windows Scheduled Task that you can remove here."
              : "Waking the PC needs the MAYA desktop app. In a browser only the first option is available."}
          </p>
        </div>
        <SwitchRow
          id={`${ids}-voice`}
          title="Speak the update by voice when I open MAYA"
          help="If a schedule has finished while MAYA was closed, MAYA reads you the update with the microphone muted."
          checked={settings.voice_on_open}
          disabled={false}
          stateText={
            saveText !== "" ? saveText : settings.voice_on_open ? "On" : "Off"
          }
          onToggle={() => void changeVoice()}
        />
      </SettingsCard>

      <div className="flex items-center justify-between gap-3">
        <h3 className="m-0 text-maya-heading font-semibold text-maya-text">
          Your scheduled reports
        </h3>
        <Button
          size="sm"
          variant="primary"
          disabled={limitReached}
          aria-describedby={limitReached ? `${ids}-limit` : undefined}
          onClick={() => setEditing("new")}
        >
          New schedule
        </Button>
      </div>
      {limitReached && (
        <p
          id={`${ids}-limit`}
          className="m-0 text-maya-small text-maya-secondary"
          data-testid="schedule-limit"
        >
          You have reached the limit of {store.meta.max_schedules} scheduled
          reports. Delete one to add another.
        </p>
      )}

      {store.items.length === 0 ? (
        <EmptyState
          icon={CalendarClock}
          title="No scheduled reports yet"
          description="Create one here, or ask MAYA in chat, for example: send me an AI news report every day at 8."
        />
      ) : (
        <ul className="m-0 grid list-none gap-3 p-0" aria-label="Schedules">
          {store.items.map((schedule) => {
            const run = schedule.last_run;
            const runTone = run !== null ? RUN_TONE[run.status] : undefined;
            const labelId = `${ids}-${schedule.id}`;
            return (
              <MenuSurface
                as="li"
                key={schedule.id}
                label={schedule.name}
                getItems={() => scheduleItems(schedule)}
                data-testid="schedule-card"
                className="grid min-w-0 gap-3 rounded-maya-lg border border-maya-border bg-maya-pane p-4"
              >
                <div className="flex min-w-0 items-start justify-between gap-3">
                  <div className="min-w-0">
                    <h4
                      id={labelId}
                      dir="auto"
                      className="m-0 text-maya-body font-semibold text-maya-text [overflow-wrap:anywhere]"
                    >
                      {schedule.name}
                    </h4>
                    <p className="m-0 mt-1 text-maya-small text-maya-secondary">
                      {describeCadence(schedule.cadence)}
                    </p>
                  </div>
                  <div className="flex shrink-0 flex-col items-end gap-0.5">
                    <Switch
                      checked={schedule.enabled}
                      disabled={busyId === schedule.id}
                      labelledBy={labelId}
                      describedBy={`${labelId}-state`}
                      onCheckedChange={() => void togglePause(schedule)}
                    />
                    <span
                      id={`${labelId}-state`}
                      aria-live="polite"
                      className="text-maya-caption text-maya-muted"
                    >
                      {schedule.enabled ? "Running" : "Paused"}
                    </span>
                  </div>
                </div>

                <ul
                  className="m-0 flex list-none flex-wrap gap-2 p-0"
                  aria-label="Delivery and format"
                >
                  <li>
                    <Badge tone="neutral">
                      {schedule.format === "chat"
                        ? "Chat only"
                        : `${formatLabel(schedule.format)}: full report in Artifacts`}
                    </Badge>
                  </li>
                  {schedule.deliver.mail &&
                    schedule.deliver.recipients.map((r) => (
                      <li key={r.email}>
                        <Badge
                          tone={r.verified ? "success" : "warning"}
                          icon={Mail}
                        >
                          Mail: {r.email}{" "}
                          {r.verified
                            ? "(verified)"
                            : "(Not verified: mail to this address is held back until you confirm the code)"}
                        </Badge>
                      </li>
                    ))}
                  {schedule.deliver.mail &&
                    schedule.deliver.recipients.length === 0 && (
                      <li>
                        <Badge tone="warning" icon={Mail}>
                          Mail: no verified address
                        </Badge>
                      </li>
                    )}
                </ul>

                <dl className="m-0 grid gap-1 text-maya-small">
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <dt className="text-maya-muted">Last run</dt>
                    <dd className="m-0 flex min-w-0 flex-wrap items-center gap-2 text-maya-text">
                      {run === null ? (
                        "Not run yet"
                      ) : (
                        <>
                          {runTone !== undefined && (
                            <Badge tone={runTone.tone}>{runTone.label}</Badge>
                          )}
                          <span>{formatWhen(run.at)}</span>
                        </>
                      )}
                    </dd>
                  </div>
                  {run !== null && run.summary !== "" && (
                    // The report is Markdown: rendered (headings, bullets, sources), scrollable.
                    <ReportBox
                      markdown={run.summary}
                      label={`Last report: ${schedule.name}`}
                    />
                  )}
                  {run !== null && run.artifact_ids.length > 0 && (
                    <p className="m-0">
                      <Link
                        href="/artifacts"
                        className="text-maya-link underline"
                      >
                        Open Artifacts
                      </Link>
                    </p>
                  )}
                  <div className="flex flex-wrap items-baseline gap-x-2">
                    <dt className="text-maya-muted">Next run</dt>
                    <dd className="m-0 text-maya-text">
                      {!schedule.enabled
                        ? "Paused"
                        : schedule.next_run_at !== null
                          ? formatWhen(schedule.next_run_at)
                          : "Not scheduled"}
                    </dd>
                  </div>
                </dl>

                <div className="flex flex-wrap gap-2">
                  <Button
                    size="sm"
                    leftIcon={<Play size={14} aria-hidden="true" />}
                    disabled={busyId === schedule.id}
                    aria-label={`Run ${schedule.name} now`}
                    onClick={() => void runNow(schedule)}
                  >
                    Run now
                  </Button>
                  <Button
                    size="sm"
                    leftIcon={<Pencil size={14} aria-hidden="true" />}
                    aria-label={`Edit ${schedule.name}`}
                    onClick={() => setEditing(schedule)}
                  >
                    Edit
                  </Button>
                  <Button
                    size="sm"
                    variant="ghost"
                    leftIcon={<Trash2 size={14} aria-hidden="true" />}
                    aria-label={`Delete ${schedule.name}`}
                    onClick={() => {
                      setDeleteError(null);
                      setDeleting(schedule);
                    }}
                  >
                    Delete
                  </Button>
                </div>
              </MenuSurface>
            );
          })}
        </ul>
      )}

      {reportOf?.last_run != null && (
        <GlassModal
          isOpen
          onClose={() => setReportOf(null)}
          title={`Last report: ${reportOf.name}`}
          size="md"
        >
          <ReportBox
            markdown={reportOf.last_run.summary}
            label={`Last report: ${reportOf.name}`}
          />
        </GlassModal>
      )}

      {editing !== null && (
        <ScheduleForm
          schedule={editing === "new" ? null : editing}
          prefill={editing === "new" ? duplicateOf : null}
          accountEmail={store.meta.account_email ?? accountEmail}
          mailConfigured={store.meta.mail_configured}
          voiceOnOpen={settings.voice_on_open}
          onClose={() => {
            setEditing(null);
            setDuplicateOf(null);
          }}
          onSave={saveForm}
        />
      )}

      {deleting !== null && (
        <ConfirmDeleteDialog
          title={`Delete "${deleting.name}"?`}
          removes={["This schedule and its settings."]}
          keeps={["Reports it already made stay in Artifacts."]}
          confirmLabel="Delete schedule"
          busy={deleteBusy}
          error={deleteError}
          onConfirm={() => void confirmDelete()}
          onClose={() => setDeleting(null)}
        />
      )}

      {confirmWake && (
        <GlassModal
          isOpen
          onClose={wakeBusy ? () => undefined : () => setConfirmWake(false)}
          title="Let MAYA wake this PC for schedules?"
          size="sm"
        >
          <div className="grid min-w-0 gap-3">
            <ul className="m-0 grid list-disc gap-0.5 pl-5 text-maya-small text-maya-secondary">
              <li>Docker Desktop has to be running.</li>
              <li>The PC has to be on and you have to be logged in.</li>
              <li>
                It creates a Windows Scheduled Task. You can remove it here by
                choosing the first run mode again.
              </li>
            </ul>
            {wakeError !== null && (
              <p role="alert" className="m-0 text-maya-small text-maya-danger">
                {wakeError}
              </p>
            )}
            <div className="flex flex-wrap items-start justify-end gap-2">
              <Button
                ref={cancelWakeRef}
                variant="ghost"
                size="sm"
                disabled={wakeBusy}
                onClick={() => setConfirmWake(false)}
              >
                Cancel
              </Button>
              <Button
                variant="primary"
                size="sm"
                loading={wakeBusy}
                onClick={() => void enableWake()}
              >
                Create the task
              </Button>
            </div>
          </div>
        </GlassModal>
      )}
    </div>
  );
}
