/**
 * Typed client for scheduled reports (`/api/v1/schedules`). Same auth handling as the other
 * clients: bearer token, one refresh and retry on 401, and `ApiError` for every non-2xx answer.
 * Responses are read defensively: a malformed entry is skipped, a malformed body is an error.
 */

import { ApiError, NetworkError, apiErrorFromResponse } from "./api-client";
import { getAccessToken, refreshSession } from "./auth-session";

const BASE = "/api/v1/schedules";

/** Days are three-letter codes in every response; numbers 0..6 and full names are read too. */
export type DayCode = "mon" | "tue" | "wed" | "thu" | "fri" | "sat" | "sun";
export const DAY_CODES: readonly DayCode[] = [
  "mon",
  "tue",
  "wed",
  "thu",
  "fri",
  "sat",
  "sun",
];

export type CadenceType = "daily" | "weekdays" | "weekly" | "custom_days";
export type ScheduleFormat = "chat" | "pdf" | "docx" | "pptx" | "xlsx" | "md";
export type RunMode = "app_open" | "wake_pc";
export type UpdateStatus = "done" | "failed" | "needs_attention" | "skipped";

export interface Cadence {
  readonly type: CadenceType;
  /** "HH:MM", 24 hour. */
  readonly time: string;
  /** 0 = Monday .. 6 = Sunday. */
  readonly days: readonly DayCode[];
  readonly timezone: string;
}

export interface Recipient {
  readonly email: string;
  readonly verified: boolean;
}

export interface Delivery {
  readonly mail: boolean;
  readonly recipients: readonly Recipient[];
  readonly chat_artifact: boolean;
  readonly voice_on_open: boolean;
}

export interface Depth {
  readonly sources_target: number;
  readonly use_deep_research: boolean;
}

export interface LastRun {
  readonly at: string;
  readonly status: string;
  readonly task_id: string | null;
  readonly summary: string;
  readonly announced: boolean;
  readonly artifact_ids: readonly string[];
}

export interface Schedule {
  readonly id: string;
  readonly name: string;
  readonly instructions: string;
  readonly topic: string;
  readonly kind: string;
  readonly cadence: Cadence;
  readonly deliver: Delivery;
  readonly format: ScheduleFormat;
  readonly depth: Depth;
  readonly enabled: boolean;
  readonly created_at: string;
  readonly last_run: LastRun | null;
  readonly next_run_at: string | null;
  readonly version: number;
}

export interface ScheduleSettings {
  readonly run_mode: RunMode;
  readonly voice_on_open: boolean;
  readonly wake_registered: boolean;
  /** The IANA zone the server holds for chat and voice, or null. */
  readonly timezone: string | null;
}

export interface ScheduleList {
  readonly items: readonly Schedule[];
  readonly settings: ScheduleSettings;
  readonly account_email: string | null;
  readonly verified_recipients: readonly string[];
  /** False when the server has no mail service: mail delivery cannot work. */
  readonly mail_configured: boolean;
  /** The most schedules this account may have, or null when not stated. */
  readonly max_schedules: number | null;
}

/** The writable part of a schedule (POST and PATCH body). */
export interface ScheduleInput {
  readonly name: string;
  readonly instructions: string;
  readonly cadence: Cadence;
  readonly deliver: Delivery;
  readonly format: ScheduleFormat;
  readonly depth: Depth;
}

export type SchedulePatch = Partial<ScheduleInput> & {
  readonly enabled?: boolean;
};

export interface ScheduleUpdate {
  readonly schedule_id: string;
  readonly name: string;
  readonly run_at: string;
  readonly status: UpdateStatus;
  readonly summary: string;
  readonly spoken_summary: string;
  readonly format: ScheduleFormat;
  readonly artifact_ids: readonly string[];
  readonly needs_attention: boolean;
  /** True when MAYA speaks this update by voice: the backend marks it announced after that turn. */
  readonly voice_on_open: boolean;
  readonly reason: string;
  /** "sent", or why mail was not sent (a short code); "" when no mail was asked. */
  readonly mail_status: string;
}

export interface ScheduleDraft {
  readonly id: string;
  readonly preview_text: string;
  readonly schedule: Partial<Schedule> & { readonly name?: string };
  readonly expires_at: string | null;
}

export const FORMAT_OPTIONS: readonly {
  readonly value: ScheduleFormat;
  readonly label: string;
}[] = [
  { value: "chat", label: "Chat only" },
  { value: "pdf", label: "PDF" },
  { value: "docx", label: "Word" },
  { value: "pptx", label: "Slides" },
  { value: "xlsx", label: "Sheet" },
  { value: "md", label: "Markdown" },
];

export const SOURCES_MIN = 5;
export const SOURCES_MAX = 20;

// ---------------------------------------------------------------------------
// Plain words
// ---------------------------------------------------------------------------

const DAY_NAMES: Record<DayCode, string> = {
  mon: "Monday",
  tue: "Tuesday",
  wed: "Wednesday",
  thu: "Thursday",
  fri: "Friday",
  sat: "Saturday",
  sun: "Sunday",
};

export function dayName(day: DayCode): string {
  return DAY_NAMES[day];
}

/** Reads a day from "mon", "Monday" or 0..6 (0 = Monday); null when it is none of those. */
export function readDay(value: unknown): DayCode | null {
  if (typeof value === "number" && Number.isInteger(value)) {
    return DAY_CODES[value] ?? null;
  }
  if (typeof value === "string") {
    const code = value.trim().slice(0, 3).toLowerCase();
    return (DAY_CODES as readonly string[]).includes(code)
      ? (code as DayCode)
      : null;
  }
  return null;
}

/** "Every day at 08:00 (Asia/Kolkata)", "Weekdays at ...", "Every Monday and Friday at ...". */
export function describeCadence(cadence: Cadence): string {
  const tz = cadence.timezone !== "" ? ` (${cadence.timezone})` : "";
  const at = ` at ${cadence.time}${tz}`;
  switch (cadence.type) {
    case "daily":
      return `Every day${at}`;
    case "weekdays":
      return `Every weekday${at}`;
    case "weekly":
    case "custom_days": {
      const names = [...cadence.days]
        .sort((a, b) => DAY_CODES.indexOf(a) - DAY_CODES.indexOf(b))
        .map(dayName);
      if (names.length === 0) return `Every week${at}`;
      const list =
        names.length === 1
          ? names[0]!
          : `${names.slice(0, -1).join(", ")} and ${names[names.length - 1]}`;
      return `Every ${list}${at}`;
    }
  }
}

export function formatLabel(format: ScheduleFormat): string {
  return FORMAT_OPTIONS.find((o) => o.value === format)?.label ?? format;
}

/** The browser time zone, or "UTC" when it cannot be read. */
export function defaultTimezone(): string {
  try {
    const tz = Intl.DateTimeFormat().resolvedOptions().timeZone;
    return typeof tz === "string" && tz !== "" ? tz : "UTC";
  } catch {
    return "UTC";
  }
}

/** Text for a failed schedules call. 429 names the wait when the server says how long. */
export function describeSchedulesError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 401:
        return "You are signed out. Sign in again and retry.";
      case 403:
        return "You do not have permission to do that.";
      case 404:
        return "That schedule no longer exists.";
      case 409:
        return "This schedule was changed somewhere else. Reload and try again.";
      case 422:
        return error.serverMessage !== null && error.serverMessage.trim() !== ""
          ? error.serverMessage
          : error.fields.length > 0
            ? `Check these fields: ${error.fields.join(", ")}.`
            : "That was not accepted. Check it and try again.";
      case 429: {
        const wait = error.retryAfterSeconds;
        if (wait === null) return "Too many tries. Wait a moment and retry.";
        const minutes = Math.ceil(wait / 60);
        return wait >= 60
          ? `Too many tries. Try again in about ${minutes} minute${minutes === 1 ? "" : "s"}.`
          : `Too many tries. Try again in ${Math.max(1, Math.ceil(wait))} seconds.`;
      }
      default:
        if (error.status >= 500) {
          return "The server could not do that right now. Try again in a moment.";
        }
        return "That could not be done. Try again.";
    }
  }
  if (error instanceof NetworkError) {
    return "The connection was lost. Check your network and try again.";
  }
  return "That could not be done. Try again.";
}

// ---------------------------------------------------------------------------
// Parsing
// ---------------------------------------------------------------------------

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function s(value: unknown, fallback = ""): string {
  return typeof value === "string" ? value : fallback;
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? (value as unknown[]).filter((v): v is string => typeof v === "string")
    : [];
}

const FORMATS: readonly string[] = FORMAT_OPTIONS.map((o) => o.value);

function readFormat(value: unknown): ScheduleFormat {
  return typeof value === "string" && FORMATS.includes(value)
    ? (value as ScheduleFormat)
    : "chat";
}

function readCadence(value: unknown): Cadence {
  const r = isRecord(value) ? value : {};
  const type: CadenceType =
    r.type === "weekdays" || r.type === "weekly" || r.type === "custom_days"
      ? r.type
      : "daily";
  return {
    type,
    time: s(r.time, "08:00"),
    days: Array.isArray(r.days)
      ? (r.days as unknown[])
          .map(readDay)
          .filter((d): d is DayCode => d !== null)
      : [],
    timezone: s(r.timezone),
  };
}

function readDelivery(value: unknown): Delivery {
  const r = isRecord(value) ? value : {};
  const recipients: Recipient[] = [];
  if (Array.isArray(r.recipients)) {
    for (const raw of r.recipients as unknown[]) {
      if (!isRecord(raw) || typeof raw.email !== "string") continue;
      recipients.push({ email: raw.email, verified: raw.verified === true });
    }
  }
  return {
    mail: r.mail === true,
    recipients,
    chat_artifact: r.chat_artifact !== false,
    voice_on_open: r.voice_on_open === true,
  };
}

function readDepth(value: unknown): Depth {
  const r = isRecord(value) ? value : {};
  const n = typeof r.sources_target === "number" ? r.sources_target : 10;
  return {
    sources_target: Math.min(SOURCES_MAX, Math.max(SOURCES_MIN, Math.round(n))),
    use_deep_research: r.use_deep_research === true,
  };
}

function readLastRun(value: unknown): LastRun | null {
  if (!isRecord(value) || typeof value.at !== "string") return null;
  return {
    at: value.at,
    status: s(value.status),
    task_id: typeof value.task_id === "string" ? value.task_id : null,
    summary: s(value.summary),
    announced: value.announced === true,
    artifact_ids: strings(value.artifact_ids),
  };
}

export function parseSchedule(value: unknown): Schedule {
  if (!isRecord(value) || typeof value.id !== "string" || value.id === "") {
    throw new ApiError(200, "unexpected_response");
  }
  return {
    id: value.id,
    name: s(value.name),
    instructions: s(value.instructions),
    topic: s(value.topic),
    kind: s(value.kind),
    cadence: readCadence(value.cadence),
    deliver: readDelivery(value.deliver),
    format: readFormat(value.format),
    depth: readDepth(value.depth),
    enabled: value.enabled !== false,
    created_at: s(value.created_at),
    last_run: readLastRun(value.last_run),
    next_run_at:
      typeof value.next_run_at === "string" ? value.next_run_at : null,
    version: typeof value.version === "number" ? value.version : 1,
  };
}

function parseSettings(value: unknown): ScheduleSettings {
  const r = isRecord(value) ? value : {};
  return {
    run_mode: r.run_mode === "wake_pc" ? "wake_pc" : "app_open",
    voice_on_open: r.voice_on_open === true,
    wake_registered: r.wake_registered === true,
    timezone:
      typeof r.timezone === "string" && r.timezone !== "" ? r.timezone : null,
  };
}

function parseUpdate(value: unknown): ScheduleUpdate | null {
  if (!isRecord(value) || typeof value.schedule_id !== "string") return null;
  const status: UpdateStatus =
    value.status === "failed" ||
    value.status === "needs_attention" ||
    value.status === "skipped"
      ? value.status
      : "done";
  return {
    schedule_id: value.schedule_id,
    name: s(value.name),
    run_at: s(value.run_at),
    status,
    summary: s(value.summary),
    spoken_summary: s(value.spoken_summary),
    format: readFormat(value.format),
    artifact_ids: strings(value.artifact_ids),
    needs_attention:
      value.needs_attention === true || status === "needs_attention",
    voice_on_open: value.voice_on_open === true,
    reason: s(value.reason),
    mail_status: s(value.mail_status),
  };
}

/** Parses a draft preview: the card shows `preview_text`, never anything the model could style. */
export function parseDraft(value: unknown): ScheduleDraft {
  if (!isRecord(value) || typeof value.id !== "string" || value.id === "") {
    throw new ApiError(200, "unexpected_response");
  }
  return {
    id: value.id,
    preview_text: s(value.preview_text) || s(value.preview),
    schedule: isRecord(value.schedule)
      ? (value.schedule as ScheduleDraft["schedule"])
      : {},
    expires_at: typeof value.expires_at === "string" ? value.expires_at : null,
  };
}

// ---------------------------------------------------------------------------
// Fetch with auth
// ---------------------------------------------------------------------------

async function authenticatedFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  let token = getAccessToken();
  if (!token) {
    const refreshed = await refreshSession();
    token = refreshed ? getAccessToken() : null;
  }
  const headers = new Headers(init?.headers);
  headers.set("Accept", "application/json");
  if (init?.body !== undefined) headers.set("Content-Type", "application/json");
  if (token) headers.set("Authorization", `Bearer ${token}`);

  let response: Response;
  try {
    response = await fetch(path, { ...init, headers, cache: "no-store" });
    if (response.status === 401) {
      const refreshed = await refreshSession();
      const retryToken = refreshed ? getAccessToken() : null;
      if (retryToken) {
        headers.set("Authorization", `Bearer ${retryToken}`);
        response = await fetch(path, { ...init, headers, cache: "no-store" });
      }
    }
  } catch (error) {
    if (
      typeof error === "object" &&
      error !== null &&
      (error as { name?: unknown }).name === "AbortError"
    ) {
      throw error;
    }
    throw new NetworkError(error);
  }
  if (!response.ok) throw await apiErrorFromResponse(response);
  return response;
}

async function json(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new ApiError(response.status, "unexpected_response");
  }
}

// ---------------------------------------------------------------------------
// Calls
// ---------------------------------------------------------------------------

export async function listSchedules(): Promise<ScheduleList> {
  const body = await json(await authenticatedFetch(BASE));
  if (!isRecord(body) || !Array.isArray(body.items)) {
    throw new ApiError(200, "unexpected_response");
  }
  const items: Schedule[] = [];
  for (const raw of body.items as unknown[]) {
    try {
      items.push(parseSchedule(raw));
    } catch {
      /* skip a malformed entry */
    }
  }
  return {
    items,
    settings: parseSettings(body.settings),
    account_email:
      typeof body.account_email === "string" && body.account_email !== ""
        ? body.account_email
        : null,
    verified_recipients: strings(body.verified_recipients),
    // An older server that does not say anything is treated as configured.
    mail_configured: body.mail_configured !== false,
    max_schedules:
      typeof body.max_schedules === "number" ? body.max_schedules : null,
  };
}

export async function createSchedule(input: ScheduleInput): Promise<Schedule> {
  const response = await authenticatedFetch(BASE, {
    method: "POST",
    body: JSON.stringify(input),
  });
  return parseSchedule(await json(response));
}

export async function updateSchedule(
  id: string,
  patch: SchedulePatch,
): Promise<Schedule> {
  const response = await authenticatedFetch(
    `${BASE}/${encodeURIComponent(id)}`,
    { method: "PATCH", body: JSON.stringify(patch) },
  );
  return parseSchedule(await json(response));
}

export async function deleteSchedule(id: string): Promise<void> {
  await authenticatedFetch(`${BASE}/${encodeURIComponent(id)}`, {
    method: "DELETE",
  });
}

export async function runScheduleNow(id: string): Promise<{ task_id: string }> {
  const response = await authenticatedFetch(
    `${BASE}/${encodeURIComponent(id)}/run-now`,
    { method: "POST" },
  );
  const body = await json(response);
  if (!isRecord(body) || typeof body.task_id !== "string") {
    throw new ApiError(200, "unexpected_response");
  }
  return { task_id: body.task_id };
}

/** Unannounced updates only. */
export async function listScheduleUpdates(): Promise<
  readonly ScheduleUpdate[]
> {
  const body = await json(await authenticatedFetch(`${BASE}/updates`));
  if (!isRecord(body) || !Array.isArray(body.items)) {
    throw new ApiError(200, "unexpected_response");
  }
  const out: ScheduleUpdate[] = [];
  for (const raw of body.items as unknown[]) {
    const item = parseUpdate(raw);
    if (item !== null) out.push(item);
  }
  return out;
}

export async function ackScheduleUpdate(scheduleId: string): Promise<void> {
  await authenticatedFetch(
    `${BASE}/updates/${encodeURIComponent(scheduleId)}/ack`,
    { method: "POST" },
  );
}

export async function updateScheduleSettings(patch: {
  run_mode?: RunMode;
  voice_on_open?: boolean;
  /** Send after the desktop bridge created (true) or removed (false) the Windows task. */
  wake_registered?: boolean;
  /** The device IANA zone, so chat and voice default to it. */
  timezone?: string;
}): Promise<ScheduleSettings> {
  const response = await authenticatedFetch(`${BASE}/settings`, {
    method: "PUT",
    body: JSON.stringify(patch),
  });
  return parseSettings(await json(response));
}

/** `alreadyVerified` is true when no code was sent because the address is already verified. */
export async function requestRecipientCode(
  email: string,
): Promise<{ sent: boolean; alreadyVerified: boolean }> {
  const response = await authenticatedFetch(`${BASE}/recipients/request`, {
    method: "POST",
    body: JSON.stringify({ email }),
  });
  let body: unknown = null;
  try {
    body = await response.json();
  } catch {
    /* an empty body means a code was sent */
  }
  const r = isRecord(body) ? body : {};
  return {
    sent: r.sent !== false,
    alreadyVerified: r.verified === true,
  };
}

let timezoneSynced = false;

/**
 * Sends the device IANA zone to the server when it differs from the one the server holds, so chat
 * and voice default to it. Once per page load unless `force`; failures are ignored (best effort).
 */
export async function syncDeviceTimezone(
  known?: string | null,
  force = false,
): Promise<void> {
  if (timezoneSynced && !force) return;
  timezoneSynced = true;
  const zone = defaultTimezone();
  try {
    const current =
      known !== undefined ? known : (await listSchedules()).settings.timezone;
    if (current !== zone) await updateScheduleSettings({ timezone: zone });
  } catch {
    /* best effort: the next start tries again */
  }
}

/** Test hook: lets a test run the once-per-start sync again. */
export function resetTimezoneSyncForTests(): void {
  timezoneSynced = false;
}

/** Plain words for a mail_status that is not "sent"; "" when there is nothing to say. */
export function describeMailStatus(status: string): string {
  const key = status.trim().toLowerCase();
  if (key === "" || key === "sent" || key === "ok" || key === "not_requested") {
    return "";
  }
  switch (key) {
    case "not_configured":
      return "the mail service is not configured on this server";
    case "no_verified_recipient":
    case "held":
    case "unverified":
      return "no verified address, mail is held back until you confirm the code";
    case "failed":
    case "error":
      return "sending failed";
    default:
      return "it could not be sent";
  }
}

export async function confirmRecipientCode(
  email: string,
  code: string,
): Promise<boolean> {
  const response = await authenticatedFetch(`${BASE}/recipients/confirm`, {
    method: "POST",
    body: JSON.stringify({ email, code }),
  });
  const body = await json(response);
  return isRecord(body) && body.verified === true;
}

export async function getScheduleDraft(id: string): Promise<ScheduleDraft> {
  const response = await authenticatedFetch(
    `${BASE}/drafts/${encodeURIComponent(id)}`,
  );
  return parseDraft(await json(response));
}

export async function confirmScheduleDraft(id: string): Promise<Schedule> {
  const response = await authenticatedFetch(
    `${BASE}/drafts/${encodeURIComponent(id)}/confirm`,
    { method: "POST" },
  );
  return parseSchedule(await json(response));
}

export async function cancelScheduleDraft(id: string): Promise<void> {
  await authenticatedFetch(`${BASE}/drafts/${encodeURIComponent(id)}/cancel`, {
    method: "POST",
  });
}
