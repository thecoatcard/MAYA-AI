"use client";

import React, { useId, useState } from "react";

import {
  FORMAT_OPTIONS,
  SOURCES_MAX,
  SOURCES_MIN,
  confirmRecipientCode,
  DAY_CODES,
  dayName,
  defaultTimezone,
  describeSchedulesError,
  requestRecipientCode,
  type CadenceType,
  type DayCode,
  type Recipient,
  type Schedule,
  type ScheduleFormat,
  type ScheduleInput,
} from "../../lib/schedules-client";
import { Badge, Button, GlassModal, Select, Switch } from "../ui";
import { GlassInput, GlassTextarea } from "../ui/GlassInput";

export interface ScheduleFormProps {
  /** The schedule being edited, or null for a new one. */
  readonly schedule: Schedule | null;
  /** Start a NEW schedule with this one's values (Duplicate). Ignored when `schedule` is set. */
  readonly prefill?: Schedule | null;
  /** The signed-in account's e-mail, listed as an already verified recipient when known. */
  readonly accountEmail: string | null;
  /** The saved "speak the update when I open MAYA" setting. */
  readonly voiceOnOpen: boolean;
  /** False when the server has no mail service: the mail switch is off and says why. */
  readonly mailConfigured?: boolean;
  readonly onClose: () => void;
  /** Resolves an error text, or null when saved. The caller closes the dialog on null. */
  readonly onSave: (
    input: ScheduleInput,
    voiceOnOpen: boolean,
  ) => Promise<string | null>;
}

const EMAIL_RE = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function sameEmail(a: string, b: string): boolean {
  return a.trim().toLowerCase() === b.trim().toLowerCase();
}

/**
 * Create or edit a scheduled report. Plain fields, no hidden defaults: the time zone starts at the
 * browser's, the format at "Chat only". A recipient other than the account e-mail has to be
 * verified with a 6-digit code before mail is sent to it; unverified ones are kept out of the save.
 */
export function ScheduleForm({
  schedule,
  prefill = null,
  accountEmail,
  voiceOnOpen,
  mailConfigured = true,
  onClose,
  onSave,
}: ScheduleFormProps): React.JSX.Element {
  const ids = useId();
  const base = schedule ?? prefill;
  const [name, setName] = useState(
    schedule?.name ?? (prefill !== null ? `${prefill.name} (copy)` : ""),
  );
  const [instructions, setInstructions] = useState(base?.instructions ?? "");
  const [type, setType] = useState<CadenceType>(
    base === null
      ? "daily"
      : base.cadence.type === "weekly"
        ? "custom_days"
        : base.cadence.type,
  );
  const [time, setTime] = useState(base?.cadence.time ?? "08:00");
  const [days, setDays] = useState<readonly DayCode[]>(
    base?.cadence.days ?? ["mon"],
  );
  const [timezone, setTimezone] = useState(
    base?.cadence.timezone || defaultTimezone(),
  );
  const [mail, setMail] = useState(
    mailConfigured && (base?.deliver.mail ?? false),
  );
  const [recipients, setRecipients] = useState<readonly Recipient[]>(() => {
    const list = [...(base?.deliver.recipients ?? [])];
    if (
      accountEmail !== null &&
      !list.some((r) => sameEmail(r.email, accountEmail))
    ) {
      list.unshift({ email: accountEmail, verified: true });
    }
    return list;
  });
  const [format, setFormat] = useState<ScheduleFormat>(base?.format ?? "chat");
  const [sources, setSources] = useState(base?.depth.sources_target ?? 10);
  const [deep, setDeep] = useState(base?.depth.use_deep_research ?? false);
  const [voice, setVoice] = useState(voiceOnOpen);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [touched, setTouched] = useState(false);

  // Recipient flow.
  const [newEmail, setNewEmail] = useState("");
  const [pending, setPending] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [recipientBusy, setRecipientBusy] = useState(false);
  const [recipientError, setRecipientError] = useState<string | null>(null);

  const nameError =
    touched && name.trim() === "" ? "Give the schedule a name." : undefined;
  const instrError =
    touched && instructions.trim() === ""
      ? "Say what the report should include."
      : undefined;
  const daysError =
    type === "custom_days" && days.length === 0
      ? "Pick at least one day."
      : undefined;

  const toggleDay = (day: DayCode): void =>
    setDays((prev) =>
      prev.includes(day) ? prev.filter((d) => d !== day) : [...prev, day],
    );

  const sendCode = async (): Promise<void> => {
    const email = newEmail.trim();
    setRecipientError(null);
    if (!EMAIL_RE.test(email)) {
      setRecipientError("Enter a full e-mail address.");
      return;
    }
    setRecipientBusy(true);
    try {
      const result = await requestRecipientCode(email);
      if (result.alreadyVerified) {
        // No code was sent: the address is already verified.
        setRecipients((prev) =>
          prev.some((r) => sameEmail(r.email, email))
            ? prev.map((r) =>
                sameEmail(r.email, email) ? { ...r, verified: true } : r,
              )
            : [...prev, { email, verified: true }],
        );
      } else {
        setPending(email);
        setCode("");
        if (!recipients.some((r) => sameEmail(r.email, email))) {
          setRecipients((prev) => [...prev, { email, verified: false }]);
        }
      }
      setNewEmail("");
    } catch (e) {
      setRecipientError(describeSchedulesError(e));
    } finally {
      setRecipientBusy(false);
    }
  };

  const confirmCode = async (): Promise<void> => {
    if (pending === null) return;
    setRecipientError(null);
    if (!/^\d{6}$/.test(code)) {
      setRecipientError("Enter the 6-digit code from the e-mail.");
      return;
    }
    setRecipientBusy(true);
    try {
      const ok = await confirmRecipientCode(pending, code);
      if (ok) {
        setRecipients((prev) =>
          prev.map((r) =>
            sameEmail(r.email, pending) ? { ...r, verified: true } : r,
          ),
        );
        setPending(null);
        setCode("");
      } else {
        setRecipientError("That code did not match. Check it and try again.");
      }
    } catch (e) {
      setRecipientError(describeSchedulesError(e));
    } finally {
      setRecipientBusy(false);
    }
  };

  const save = async (): Promise<void> => {
    setTouched(true);
    if (name.trim() === "" || instructions.trim() === "") return;
    if (daysError !== undefined) return;
    setBusy(true);
    setError(null);
    const input: ScheduleInput = {
      name: name.trim(),
      instructions: instructions.trim(),
      cadence: {
        type,
        time,
        days:
          type === "custom_days"
            ? DAY_CODES.filter((d) => days.includes(d))
            : [],
        timezone,
      },
      deliver: {
        mail,
        recipients: [...recipients],
        chat_artifact: true,
        voice_on_open: voice,
      },
      format,
      depth: { sources_target: sources, use_deep_research: deep },
    };
    const message = await onSave(input, voice);
    setBusy(false);
    if (message !== null) setError(message);
  };

  return (
    <GlassModal
      isOpen
      onClose={busy ? () => undefined : onClose}
      title={
        schedule === null ? "New scheduled report" : "Edit scheduled report"
      }
      size="md"
    >
      <form
        className="grid min-w-0 gap-4"
        data-testid="schedule-form"
        onSubmit={(e) => {
          e.preventDefault();
          void save();
        }}
        noValidate
      >
        <GlassInput
          label="Name"
          value={name}
          maxLength={80}
          error={nameError}
          onChange={(e) => setName(e.target.value)}
          autoFocus
        />
        <GlassTextarea
          label="What should the report include?"
          helperText="MAYA may ask you follow-up questions in chat before the schedule is created."
          rows={4}
          value={instructions}
          error={instrError}
          onChange={(e) => setInstructions(e.target.value)}
        />

        <fieldset className="m-0 grid min-w-0 gap-3 border-0 p-0">
          <legend className="mb-1 text-maya-caption font-medium text-maya-text">
            When
          </legend>
          <div className="grid gap-3 sm:grid-cols-2">
            <Select
              label="Repeat"
              value={type}
              onChange={(e) => setType(e.target.value as CadenceType)}
            >
              <option value="daily">Every day</option>
              <option value="weekdays">Weekdays (Mon to Fri)</option>
              <option value="custom_days">On chosen days</option>
            </Select>
            <GlassInput
              label="Time"
              type="time"
              value={time}
              onChange={(e) => setTime(e.target.value)}
            />
          </div>
          {type === "custom_days" && (
            <div
              role="group"
              aria-label="Days of the week"
              className="flex flex-wrap gap-2"
            >
              {DAY_CODES.map((d) => (
                <label
                  key={d}
                  className="inline-flex min-h-8 cursor-pointer items-center gap-1.5 rounded-maya-md border border-maya-border-strong bg-maya-raised px-3 text-maya-caption text-maya-text pointer-coarse:min-h-11"
                >
                  <input
                    type="checkbox"
                    checked={days.includes(d)}
                    aria-label={dayName(d)}
                    onChange={() => toggleDay(d)}
                  />
                  <span aria-hidden="true">{dayName(d).slice(0, 3)}</span>
                </label>
              ))}
            </div>
          )}
          {daysError !== undefined && (
            <p role="alert" className="m-0 text-maya-small text-maya-danger">
              {daysError}
            </p>
          )}
          <GlassInput
            label="Time zone"
            value={timezone}
            onChange={(e) => setTimezone(e.target.value)}
            helperText="Starts as this device's time zone."
          />
        </fieldset>

        <fieldset className="m-0 grid min-w-0 gap-3 border-0 p-0">
          <legend className="mb-1 text-maya-caption font-medium text-maya-text">
            Delivery
          </legend>
          <div className="flex items-center justify-between gap-3">
            <span id={`${ids}-mail`} className="text-maya-small text-maya-text">
              Also send the report by e-mail
            </span>
            <Switch
              checked={mail}
              disabled={!mailConfigured}
              labelledBy={`${ids}-mail`}
              describedBy={mailConfigured ? undefined : `${ids}-mail-off`}
              onCheckedChange={setMail}
              showState
            />
          </div>
          {!mailConfigured && (
            <p
              id={`${ids}-mail-off`}
              className="m-0 text-maya-small text-maya-secondary"
              data-testid="mail-not-configured"
            >
              Mail service is not configured on this server.
            </p>
          )}
          {mail && (
            <div className="grid gap-3" data-testid="recipients">
              <ul className="m-0 grid list-none gap-1.5 p-0">
                {recipients.map((r) => (
                  <li
                    key={r.email}
                    className="flex min-w-0 flex-wrap items-center justify-between gap-2 text-maya-small"
                  >
                    <span className="min-w-0 [overflow-wrap:anywhere]">
                      {r.email}
                    </span>
                    <span className="flex items-center gap-2">
                      <Badge tone={r.verified ? "success" : "warning"}>
                        {r.verified ? "Verified" : "Not verified yet"}
                      </Badge>
                      {!r.verified && (
                        <span className="maya-sr-only">
                          Not verified: mail to this address is held back until
                          you confirm the code
                        </span>
                      )}
                      {accountEmail === null ||
                      !sameEmail(r.email, accountEmail) ? (
                        <Button
                          size="sm"
                          variant="ghost"
                          type="button"
                          aria-label={`Remove ${r.email}`}
                          onClick={() => {
                            setRecipients((prev) =>
                              prev.filter((x) => x.email !== r.email),
                            );
                            if (pending === r.email) setPending(null);
                          }}
                        >
                          Remove
                        </Button>
                      ) : null}
                    </span>
                  </li>
                ))}
              </ul>
              <div className="flex flex-wrap items-end gap-2">
                <GlassInput
                  label="Add an address"
                  type="email"
                  value={newEmail}
                  containerClassName="min-w-0 flex-1"
                  onChange={(e) => setNewEmail(e.target.value)}
                  onKeyDown={(e) => {
                    if (e.key === "Enter") {
                      e.preventDefault();
                      void sendCode();
                    }
                  }}
                />
                <Button
                  type="button"
                  size="sm"
                  loading={recipientBusy && pending === null}
                  onClick={() => void sendCode()}
                >
                  Send code
                </Button>
              </div>
              {pending !== null && (
                <div className="flex flex-wrap items-end gap-2">
                  <GlassInput
                    label={`6-digit code sent to ${pending}`}
                    inputMode="numeric"
                    autoComplete="one-time-code"
                    maxLength={6}
                    value={code}
                    containerClassName="min-w-0 flex-1"
                    onChange={(e) =>
                      setCode(e.target.value.replace(/\D/g, "").slice(0, 6))
                    }
                  />
                  <Button
                    type="button"
                    size="sm"
                    variant="primary"
                    loading={recipientBusy}
                    onClick={() => void confirmCode()}
                  >
                    Verify
                  </Button>
                </div>
              )}
              {recipientError !== null && (
                <p
                  role="alert"
                  className="m-0 text-maya-small text-maya-danger"
                  data-testid="recipient-error"
                >
                  {recipientError}
                </p>
              )}
              <p className="m-0 text-maya-caption text-maya-muted">
                Mail to an address that is not verified is held back until you
                confirm the code.
              </p>
            </div>
          )}
        </fieldset>

        <Select
          label="Report format"
          value={format}
          helperText={
            format === "chat"
              ? "The report is told in chat. No file is made."
              : "The full report is saved as a file in Artifacts."
          }
          onChange={(e) => setFormat(e.target.value as ScheduleFormat)}
        >
          {FORMAT_OPTIONS.map((o) => (
            <option key={o.value} value={o.value}>
              {o.label}
            </option>
          ))}
        </Select>

        <div className="grid gap-2">
          <label
            htmlFor={`${ids}-sources`}
            className="text-maya-caption font-medium text-maya-text"
          >
            Sources to read: {sources}
          </label>
          <input
            id={`${ids}-sources`}
            type="range"
            min={SOURCES_MIN}
            max={SOURCES_MAX}
            step={1}
            value={sources}
            aria-valuetext={`${sources} sources`}
            onChange={(e) => setSources(Number(e.target.value))}
          />
          <div className="flex items-center justify-between gap-3">
            <span id={`${ids}-deep`} className="text-maya-small text-maya-text">
              Use deep research
            </span>
            <Switch
              checked={deep}
              labelledBy={`${ids}-deep`}
              onCheckedChange={setDeep}
              showState
            />
          </div>
        </div>

        <div className="flex items-center justify-between gap-3">
          <span id={`${ids}-voice`} className="text-maya-small text-maya-text">
            Speak the update by voice when I open MAYA
          </span>
          <Switch
            checked={voice}
            labelledBy={`${ids}-voice`}
            onCheckedChange={setVoice}
            showState
          />
        </div>

        {error !== null && (
          <p
            role="alert"
            className="m-0 text-maya-small text-maya-danger"
            data-testid="schedule-form-error"
          >
            {error}
          </p>
        )}
        <div className="flex flex-wrap items-start justify-end gap-2">
          <Button
            variant="ghost"
            size="sm"
            type="button"
            disabled={busy}
            onClick={onClose}
          >
            Cancel
          </Button>
          <Button variant="primary" size="sm" type="submit" loading={busy}>
            {schedule === null ? "Create schedule" : "Save changes"}
          </Button>
        </div>
      </form>
    </GlassModal>
  );
}
