import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "./api-client";
import {
  ackScheduleUpdate,
  confirmRecipientCode,
  confirmScheduleDraft,
  createSchedule,
  defaultTimezone,
  describeMailStatus,
  readDay,
  resetTimezoneSyncForTests,
  syncDeviceTimezone,
  describeCadence,
  describeSchedulesError,
  getScheduleDraft,
  listScheduleUpdates,
  listSchedules,
  requestRecipientCode,
  runScheduleNow,
  updateSchedule,
  updateScheduleSettings,
  type ScheduleInput,
} from "./schedules-client";

vi.mock("./auth-session", () => ({
  getAccessToken: () => "access-token",
  refreshSession: vi.fn(async () => false),
}));

function json(body: unknown, status = 200): Response {
  return new Response(JSON.stringify(body), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}

const SCHEDULE = {
  id: "s1",
  name: "AI news",
  instructions: "AI news",
  topic: "ai",
  kind: "report",
  cadence: {
    type: "daily",
    time: "08:00",
    days: [],
    timezone: "Asia/Kolkata",
  },
  deliver: {
    mail: true,
    recipients: [{ email: "a@b.com", verified: true }],
    chat_artifact: true,
    voice_on_open: false,
  },
  format: "pdf",
  depth: { sources_target: 10, use_deep_research: false },
  enabled: true,
  created_at: "2026-11-01T00:00:00Z",
  last_run: null,
  next_run_at: "2026-11-14T02:30:00Z",
  version: 3,
};

const INPUT: ScheduleInput = {
  name: SCHEDULE.name,
  instructions: SCHEDULE.instructions,
  cadence: { type: "daily", time: "08:00", days: [], timezone: "Asia/Kolkata" },
  deliver: SCHEDULE.deliver,
  format: "pdf",
  depth: SCHEDULE.depth,
};

describe("schedules client", () => {
  const fetchMock = vi.fn();
  beforeEach(() => {
    fetchMock.mockReset();
    vi.stubGlobal("fetch", fetchMock);
  });
  afterEach(() => vi.unstubAllGlobals());

  it("lists schedules and settings with the bearer token", async () => {
    fetchMock.mockResolvedValue(
      json({
        items: [SCHEDULE, { nope: true }],
        settings: {
          run_mode: "wake_pc",
          voice_on_open: true,
          wake_registered: true,
          timezone: "Asia/Kolkata",
        },
        account_email: "me@example.com",
        verified_recipients: ["me@example.com"],
        mail_configured: false,
        max_schedules: 5,
      }),
    );
    const list = await listSchedules();
    expect(list.items).toHaveLength(1); // the malformed entry is skipped
    expect(list.items[0]!.cadence.timezone).toBe("Asia/Kolkata");
    expect(list.settings).toEqual({
      run_mode: "wake_pc",
      voice_on_open: true,
      wake_registered: true,
      timezone: "Asia/Kolkata",
    });
    expect(list.account_email).toBe("me@example.com");
    expect(list.mail_configured).toBe(false);
    expect(list.max_schedules).toBe(5);
    expect(list.verified_recipients).toEqual(["me@example.com"]);
    const [url, init] = fetchMock.mock.calls[0]!;
    expect(url).toBe("/api/v1/schedules");
    expect((init.headers as Headers).get("Authorization")).toBe(
      "Bearer access-token",
    );
  });

  it("rejects a body that is not a list", async () => {
    fetchMock.mockResolvedValue(json({ items: "x" }));
    await expect(listSchedules()).rejects.toBeInstanceOf(ApiError);
  });

  it("creates, patches and runs with the documented paths and bodies", async () => {
    fetchMock.mockResolvedValueOnce(json(SCHEDULE, 201));
    await createSchedule(INPUT);
    expect(fetchMock.mock.calls[0]![0]).toBe("/api/v1/schedules");
    expect(fetchMock.mock.calls[0]![1].method).toBe("POST");
    expect(JSON.parse(fetchMock.mock.calls[0]![1].body)).toEqual(INPUT);

    fetchMock.mockResolvedValueOnce(json(SCHEDULE));
    await updateSchedule("s1", { enabled: false });
    expect(fetchMock.mock.calls[1]![0]).toBe("/api/v1/schedules/s1");
    expect(fetchMock.mock.calls[1]![1].method).toBe("PATCH");
    expect(JSON.parse(fetchMock.mock.calls[1]![1].body)).toEqual({
      enabled: false,
    });

    fetchMock.mockResolvedValueOnce(json({ task_id: "t9" }));
    expect(await runScheduleNow("s1")).toEqual({ task_id: "t9" });
    expect(fetchMock.mock.calls[2]![0]).toBe("/api/v1/schedules/s1/run-now");
  });

  it("reads unannounced updates and acknowledges one", async () => {
    fetchMock.mockResolvedValueOnce(
      json({
        items: [
          {
            schedule_id: "s1",
            name: "AI news",
            run_at: "2026-11-13T02:30:00Z",
            status: "needs_attention",
            summary: "Two sources failed",
            spoken_summary: "Two sources failed",
            format: "pdf",
            artifact_ids: ["a1"],
            needs_attention: true,
          },
        ],
      }),
    );
    const updates = await listScheduleUpdates();
    expect(updates[0]!.needs_attention).toBe(true);
    expect(updates[0]!.artifact_ids).toEqual(["a1"]);
    fetchMock.mockResolvedValueOnce(new Response(null, { status: 204 }));
    await ackScheduleUpdate("s1");
    expect(fetchMock.mock.calls[1]![0]).toBe(
      "/api/v1/schedules/updates/s1/ack",
    );
    expect(fetchMock.mock.calls[1]![1].method).toBe("POST");
  });

  it("calls settings, recipients and drafts", async () => {
    fetchMock.mockResolvedValueOnce(
      json({
        run_mode: "app_open",
        voice_on_open: true,
        wake_registered: false,
      }),
    );
    const s = await updateScheduleSettings({ voice_on_open: true });
    expect(s.voice_on_open).toBe(true);
    expect(fetchMock.mock.calls[0]![1].method).toBe("PUT");

    fetchMock.mockResolvedValueOnce(json({ sent: true }));
    expect(await requestRecipientCode("x@y.com")).toEqual({
      sent: true,
      alreadyVerified: false,
    });
    expect(fetchMock.mock.calls[1]![0]).toBe(
      "/api/v1/schedules/recipients/request",
    );
    fetchMock.mockResolvedValueOnce(json({ verified: true }));
    expect(await confirmRecipientCode("x@y.com", "123456")).toBe(true);
    expect(JSON.parse(fetchMock.mock.calls[2]![1].body)).toEqual({
      email: "x@y.com",
      code: "123456",
    });

    fetchMock.mockResolvedValueOnce(
      json({
        id: "d1",
        preview_text: "Daily at 8",
        schedule: {},
        expires_at: null,
      }),
    );
    expect((await getScheduleDraft("d1")).preview_text).toBe("Daily at 8");
    fetchMock.mockResolvedValueOnce(json(SCHEDULE));
    await confirmScheduleDraft("d1");
    expect(fetchMock.mock.calls[4]![0]).toBe(
      "/api/v1/schedules/drafts/d1/confirm",
    );
  });

  it("words the cadence in plain language", () => {
    expect(describeCadence(INPUT.cadence)).toBe(
      "Every day at 08:00 (Asia/Kolkata)",
    );
    expect(
      describeCadence({
        type: "custom_days",
        time: "09:30",
        days: ["fri", "mon"],
        timezone: "UTC",
      }),
    ).toBe("Every Monday and Friday at 09:30 (UTC)");
    expect(
      describeCadence({
        type: "weekdays",
        time: "07:00",
        days: [],
        timezone: "",
      }),
    ).toBe("Every weekday at 07:00");
  });

  it("describes 429 with the wait and 409 as a conflict", () => {
    expect(
      describeSchedulesError(
        new ApiError(429, "rate_limited", { retryAfterSeconds: 120 }),
      ),
    ).toMatch(/2 minutes/);
    expect(describeSchedulesError(new ApiError(409, "conflict"))).toMatch(
      /changed somewhere else/,
    );
    expect(
      describeSchedulesError(
        new ApiError(422, "validation_error", { message: "Pick a time." }),
      ),
    ).toBe("Pick a time.");
  });

  it("defaults the time zone to the browser's", () => {
    expect(defaultTimezone()).toBe(
      Intl.DateTimeFormat().resolvedOptions().timeZone,
    );
  });

  it("reads days as three-letter codes, numbers or full names", () => {
    expect(readDay("mon")).toBe("mon");
    expect(readDay("Sunday")).toBe("sun");
    expect(readDay(2)).toBe("wed");
    expect(readDay(9)).toBeNull();
    expect(readDay("xyz")).toBeNull();
  });

  it("parses a schedule with day codes", async () => {
    fetchMock.mockResolvedValue(
      json({
        items: [
          {
            ...SCHEDULE,
            cadence: {
              ...SCHEDULE.cadence,
              type: "custom_days",
              days: ["mon", "fri"],
            },
          },
        ],
        settings: {},
      }),
    );
    const list = await listSchedules();
    expect(list.items[0]!.cadence.days).toEqual(["mon", "fri"]);
    expect(list.mail_configured).toBe(true); // an older server says nothing
    expect(list.max_schedules).toBeNull();
  });

  it("reports an already verified address without a code", async () => {
    fetchMock.mockResolvedValue(json({ sent: false, verified: true }));
    expect(await requestRecipientCode("me@example.com")).toEqual({
      sent: false,
      alreadyVerified: true,
    });
  });

  it("reads the new update fields and words the mail status", async () => {
    fetchMock.mockResolvedValue(
      json({
        items: [
          {
            schedule_id: "s1",
            name: "n",
            run_at: "",
            status: "done",
            summary: "",
            spoken_summary: "",
            format: "chat",
            artifact_ids: [],
            voice_on_open: true,
            reason: "Two sources failed",
            mail_status: "not_configured",
          },
        ],
      }),
    );
    const [u] = await listScheduleUpdates();
    expect(u!.voice_on_open).toBe(true);
    expect(u!.reason).toBe("Two sources failed");
    expect(describeMailStatus(u!.mail_status)).toMatch(/not configured/);
    expect(describeMailStatus("sent")).toBe("");
    expect(describeMailStatus("")).toBe("");
  });

  it("syncs the device time zone once, only when it differs", async () => {
    resetTimezoneSyncForTests();
    fetchMock.mockResolvedValue(
      json({
        run_mode: "app_open",
        voice_on_open: false,
        wake_registered: false,
        timezone: "Asia/Kolkata",
      }),
    );
    await syncDeviceTimezone("Somewhere/Else");
    expect(fetchMock).toHaveBeenCalledTimes(1);
    expect(JSON.parse(fetchMock.mock.calls[0]![1].body)).toEqual({
      timezone: defaultTimezone(),
    });
    await syncDeviceTimezone("Somewhere/Else"); // once per start
    expect(fetchMock).toHaveBeenCalledTimes(1);
    resetTimezoneSyncForTests();
    await syncDeviceTimezone(defaultTimezone()); // same zone: nothing to send
    expect(fetchMock).toHaveBeenCalledTimes(1);
  });
});
