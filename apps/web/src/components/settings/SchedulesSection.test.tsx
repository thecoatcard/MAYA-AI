import React from "react";
import {
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { beforeEach, describe, expect, it, vi } from "vitest";

import { ApiError } from "../../lib/api-client";
import * as bridge from "../../lib/desktop-bridge";
import * as client from "../../lib/schedules-client";
import { SchedulesSection } from "./SchedulesSection";

vi.mock("../../lib/schedules-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/schedules-client")>()),
  listSchedules: vi.fn(),
  createSchedule: vi.fn(),
  updateSchedule: vi.fn(),
  deleteSchedule: vi.fn(),
  runScheduleNow: vi.fn(),
  updateScheduleSettings: vi.fn(),
  requestRecipientCode: vi.fn(),
  confirmRecipientCode: vi.fn(),
  syncDeviceTimezone: vi.fn(async () => undefined),
}));

vi.mock("../../lib/desktop-bridge", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/desktop-bridge")>()),
  canRegisterScheduleWake: vi.fn(() => false),
  registerScheduleWake: vi.fn(),
}));

const TZ = Intl.DateTimeFormat().resolvedOptions().timeZone;

const SETTINGS: client.ScheduleSettings = {
  run_mode: "app_open",
  voice_on_open: false,
  wake_registered: false,
  timezone: TZ,
};

const SCHEDULE: client.Schedule = {
  id: "s1",
  name: "AI news",
  instructions: "Top AI news",
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
    recipients: [
      { email: "a@b.com", verified: true },
      { email: "c@d.com", verified: false },
    ],
    chat_artifact: true,
    voice_on_open: false,
  },
  format: "pdf",
  depth: { sources_target: 12, use_deep_research: false },
  enabled: true,
  created_at: "2026-11-01T00:00:00Z",
  last_run: {
    at: "2026-11-13T02:30:00Z",
    status: "done",
    task_id: "t1",
    summary: "Three big releases.",
    announced: true,
    artifact_ids: ["a1"],
  },
  next_run_at: "2026-11-14T02:30:00Z",
  version: 1,
};

function load(
  items: client.Schedule[] = [SCHEDULE],
  settings: client.ScheduleSettings = SETTINGS,
  extra: Partial<client.ScheduleList> = {},
): void {
  vi.mocked(client.listSchedules).mockResolvedValue({
    items,
    settings,
    account_email: null,
    verified_recipients: [],
    mail_configured: true,
    max_schedules: null,
    ...extra,
  });
}

async function mount(email: string | null = "me@example.com") {
  render(<SchedulesSection accountEmail={email} />);
  await screen.findByRole("heading", { name: "Your scheduled reports" });
}

describe("SchedulesSection", () => {
  beforeEach(() => {
    for (const fn of [
      client.listSchedules,
      client.createSchedule,
      client.updateSchedule,
      client.deleteSchedule,
      client.runScheduleNow,
      client.updateScheduleSettings,
      client.requestRecipientCode,
      client.confirmRecipientCode,
      bridge.registerScheduleWake,
    ]) {
      vi.mocked(fn as () => unknown).mockReset();
    }
    vi.mocked(bridge.canRegisterScheduleWake).mockReturnValue(false);
    load();
  });

  it("lists a schedule in plain words with chips, last run and next run", async () => {
    await mount();
    const card = screen.getByTestId("schedule-card");
    expect(
      within(card).getByText("Every day at 08:00 (Asia/Kolkata)"),
    ).toBeTruthy();
    expect(within(card).getByText(/Mail: a@b.com\s+\(verified\)/)).toBeTruthy();
    expect(
      within(card).getByText(
        /Mail: c@d.com\s+\(Not verified: mail to this address is held back until you confirm the code\)/,
      ),
    ).toBeTruthy();
    expect(
      within(card).getByText("PDF: full report in Artifacts"),
    ).toBeTruthy();
    expect(within(card).getByText("Done")).toBeTruthy();
    expect(within(card).getByText("Three big releases.")).toBeTruthy();
    expect(
      within(card).getByRole("link", { name: "Open Artifacts" }),
    ).toBeTruthy();
    expect(within(card).getByText("Next run")).toBeTruthy();
  });

  it("renders the last report as Markdown in a scrollable box, not as raw text", async () => {
    load([
      {
        ...SCHEDULE,
        last_run: {
          ...SCHEDULE.last_run!,
          summary:
            "# Scheduled report\n\n## Top stories\n\n- First story [1]\n- Second story [2]\n\n## Sources\n\n1. Example - https://example.org/a",
        },
      },
    ]);
    await mount();
    const box = await screen.findByTestId("report-box");
    expect(box.textContent).not.toContain("## ");
    expect(screen.getByRole("heading", { name: /Top stories/ })).toBeTruthy();
    expect(box.querySelectorAll("li").length).toBeGreaterThanOrEqual(2);
    expect(box).toHaveAttribute("tabindex", "0");
    expect(box.className).toContain("overflow-y-auto");
    // The height limit can be lifted to read the whole report in place.
    fireEvent.click(screen.getByRole("button", { name: "Show full report" }));
    expect(box).toHaveAttribute("data-expanded", "true");
    expect(screen.getByRole("button", { name: "Show less" })).toBeTruthy();
  });

  it("shows Chat only for a chat-format schedule and an honest empty state", async () => {
    load([
      {
        ...SCHEDULE,
        format: "chat",
        deliver: { ...SCHEDULE.deliver, mail: false },
      },
    ]);
    await mount();
    expect(screen.getByText("Chat only")).toBeTruthy();
    expect(screen.queryByText(/Mail:/)).toBeNull();
  });

  it("shows an empty state with no schedules", async () => {
    load([]);
    await mount();
    expect(screen.getByText("No scheduled reports yet")).toBeTruthy();
  });

  it("pauses and resumes with the switch, and states it in words", async () => {
    vi.mocked(client.updateSchedule).mockResolvedValue({
      ...SCHEDULE,
      enabled: false,
    });
    await mount();
    const toggle = screen.getByRole("switch", { name: "AI news" });
    expect(toggle).toHaveAttribute("aria-checked", "true");
    fireEvent.click(toggle);
    await waitFor(() =>
      expect(client.updateSchedule).toHaveBeenCalledWith("s1", {
        enabled: false,
      }),
    );
    expect(
      await screen.findByText("Paused", { selector: "span" }),
    ).toBeTruthy();
  });

  it("runs now", async () => {
    vi.mocked(client.runScheduleNow).mockResolvedValue({ task_id: "t2" });
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "Run AI news now" }));
    await waitFor(() =>
      expect(client.runScheduleNow).toHaveBeenCalledWith("s1"),
    );
    expect(await screen.findByText(/is running now/)).toBeTruthy();
  });

  it("deletes only after the confirm dialog", async () => {
    vi.mocked(client.deleteSchedule).mockResolvedValue();
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "Delete AI news" }));
    expect(client.deleteSchedule).not.toHaveBeenCalled();
    const dialog = await screen.findByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Delete schedule" }),
    );
    await waitFor(() =>
      expect(client.deleteSchedule).toHaveBeenCalledWith("s1"),
    );
    await waitFor(() =>
      expect(screen.queryByTestId("schedule-card")).toBeNull(),
    );
  });

  it("creates a schedule with the browser time zone and Chat only by default", async () => {
    load([]);
    vi.mocked(client.createSchedule).mockResolvedValue(SCHEDULE);
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "New schedule" }));
    const form = await screen.findByTestId("schedule-form");
    expect(within(form).getByLabelText("Time zone")).toHaveValue(
      Intl.DateTimeFormat().resolvedOptions().timeZone,
    );
    expect(within(form).getByLabelText("Report format")).toHaveValue("chat");
    // Required fields are checked before anything is sent.
    fireEvent.click(
      within(form).getByRole("button", { name: "Create schedule" }),
    );
    expect(client.createSchedule).not.toHaveBeenCalled();
    fireEvent.change(within(form).getByLabelText("Name"), {
      target: { value: "Morning brief" },
    });
    fireEvent.change(
      within(form).getByLabelText("What should the report include?"),
      { target: { value: "Weather and news" } },
    );
    fireEvent.change(within(form).getByLabelText("Report format"), {
      target: { value: "docx" },
    });
    fireEvent.click(
      within(form).getByRole("button", { name: "Create schedule" }),
    );
    await waitFor(() => expect(client.createSchedule).toHaveBeenCalled());
    const body = vi.mocked(client.createSchedule).mock
      .calls[0]![0] as client.ScheduleInput;
    expect(body.name).toBe("Morning brief");
    expect(body.format).toBe("docx");
    expect(body.cadence.timezone).toBe(
      Intl.DateTimeFormat().resolvedOptions().timeZone,
    );
    expect(body.cadence.type).toBe("daily");
    await waitFor(() =>
      expect(screen.queryByTestId("schedule-form")).toBeNull(),
    );
  });

  it("edits an existing schedule with a PATCH", async () => {
    vi.mocked(client.updateSchedule).mockResolvedValue({
      ...SCHEDULE,
      name: "AI weekly",
    });
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "Edit AI news" }));
    const form = await screen.findByTestId("schedule-form");
    expect(within(form).getByLabelText("Name")).toHaveValue("AI news");
    fireEvent.change(within(form).getByLabelText("Name"), {
      target: { value: "AI weekly" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Save changes" }));
    await waitFor(() => expect(client.updateSchedule).toHaveBeenCalled());
    expect(vi.mocked(client.updateSchedule).mock.calls[0]![0]).toBe("s1");
    expect(await screen.findByText("AI weekly")).toBeTruthy();
  });

  it("shows the account e-mail as verified and runs the recipient code flow", async () => {
    load([]);
    vi.mocked(client.requestRecipientCode).mockResolvedValue({
      sent: true,
      alreadyVerified: false,
    });
    vi.mocked(client.confirmRecipientCode).mockResolvedValue(true);
    await mount("me@example.com");
    fireEvent.click(screen.getByRole("button", { name: "New schedule" }));
    const form = await screen.findByTestId("schedule-form");
    fireEvent.click(
      within(form).getByRole("switch", {
        name: "Also send the report by e-mail",
      }),
    );
    const list = within(form).getByTestId("recipients");
    expect(within(list).getByText("me@example.com")).toBeTruthy();
    expect(within(list).getByText("Verified")).toBeTruthy();

    fireEvent.change(within(form).getByLabelText("Add an address"), {
      target: { value: "boss@corp.com" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Send code" }));
    await waitFor(() =>
      expect(client.requestRecipientCode).toHaveBeenCalledWith("boss@corp.com"),
    );
    expect(await within(form).findByText("Not verified yet")).toBeTruthy();
    fireEvent.change(
      within(form).getByLabelText("6-digit code sent to boss@corp.com"),
      { target: { value: "12ab3456" } },
    );
    fireEvent.click(within(form).getByRole("button", { name: "Verify" }));
    await waitFor(() =>
      expect(client.confirmRecipientCode).toHaveBeenCalledWith(
        "boss@corp.com",
        "123456",
      ),
    );
    await waitFor(() =>
      expect(within(form).getAllByText("Verified")).toHaveLength(2),
    );
  });

  it("rejects a bad address and shows the rate-limit wait", async () => {
    load([]);
    vi.mocked(client.requestRecipientCode).mockRejectedValue(
      new ApiError(429, "rate_limited", { retryAfterSeconds: 300 }),
    );
    await mount(null);
    fireEvent.click(screen.getByRole("button", { name: "New schedule" }));
    const form = await screen.findByTestId("schedule-form");
    fireEvent.click(
      within(form).getByRole("switch", {
        name: "Also send the report by e-mail",
      }),
    );
    fireEvent.change(within(form).getByLabelText("Add an address"), {
      target: { value: "nope" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Send code" }));
    expect(within(form).getByTestId("recipient-error")).toHaveTextContent(
      "Enter a full e-mail address.",
    );
    expect(client.requestRecipientCode).not.toHaveBeenCalled();
    fireEvent.change(within(form).getByLabelText("Add an address"), {
      target: { value: "x@y.com" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Send code" }));
    expect(
      await within(form).findByText(/Try again in about 5 minutes/),
    ).toBeTruthy();
  });

  it("saves the voice-on-open switch to the settings endpoint", async () => {
    vi.mocked(client.updateScheduleSettings).mockResolvedValue({
      ...SETTINGS,
      voice_on_open: true,
    });
    await mount();
    fireEvent.click(
      screen.getByRole("switch", {
        name: "Speak the update by voice when I open MAYA",
      }),
    );
    await waitFor(() =>
      expect(client.updateScheduleSettings).toHaveBeenCalledWith({
        voice_on_open: true,
      }),
    );
  });

  it("disables the wake option in a browser and explains why", async () => {
    await mount();
    const wake = screen.getByRole("radio", {
      name: /Also wake my PC to run schedules/,
    });
    expect(wake).toBeDisabled();
    expect(screen.getByTestId("wake-help")).toHaveTextContent(
      /needs the MAYA desktop app/,
    );
    expect(
      screen.getByRole("radio", { name: /While MAYA is open/ }),
    ).toBeChecked();
  });

  it("asks first in the desktop app, then creates the task and saves the mode", async () => {
    vi.mocked(bridge.canRegisterScheduleWake).mockReturnValue(true);
    vi.mocked(bridge.registerScheduleWake).mockResolvedValue({ ok: true });
    vi.mocked(client.updateScheduleSettings).mockResolvedValue({
      run_mode: "wake_pc",
      voice_on_open: false,
      wake_registered: true,
      timezone: TZ,
    });
    await mount();
    fireEvent.click(
      screen.getByRole("radio", { name: /Also wake my PC to run schedules/ }),
    );
    const dialog = await screen.findByRole("dialog");
    expect(dialog).toHaveTextContent(/Docker Desktop has to be running/);
    expect(dialog).toHaveTextContent(/Windows Scheduled Task/);
    expect(bridge.registerScheduleWake).not.toHaveBeenCalled();
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Create the task" }),
    );
    await waitFor(() =>
      expect(bridge.registerScheduleWake).toHaveBeenCalledWith(true),
    );
    await waitFor(() =>
      expect(client.updateScheduleSettings).toHaveBeenCalledWith({
        run_mode: "wake_pc",
        wake_registered: true,
      }),
    );
    expect(
      await screen.findByText(/A Windows Scheduled Task is set up/),
    ).toBeTruthy();
  });

  it("keeps the mode when the desktop task cannot be created, and cancel changes nothing", async () => {
    vi.mocked(bridge.canRegisterScheduleWake).mockReturnValue(true);
    vi.mocked(bridge.registerScheduleWake).mockResolvedValue({
      ok: false,
      reason: "failed",
    });
    await mount();
    fireEvent.click(
      screen.getByRole("radio", { name: /Also wake my PC to run schedules/ }),
    );
    let dialog = await screen.findByRole("dialog");
    fireEvent.click(
      within(dialog).getByRole("button", { name: "Create the task" }),
    );
    expect(await within(dialog).findByRole("alert")).toHaveTextContent(
      /could not be created/,
    );
    expect(client.updateScheduleSettings).not.toHaveBeenCalled();
    fireEvent.click(within(dialog).getByRole("button", { name: "Cancel" }));
    await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    expect(
      screen.getByRole("radio", { name: /While MAYA is open/ }),
    ).toBeChecked();
    dialog = null as never;
  });

  it("removes the Windows task when going back to the default mode", async () => {
    vi.mocked(bridge.canRegisterScheduleWake).mockReturnValue(true);
    vi.mocked(bridge.registerScheduleWake).mockResolvedValue({ ok: true });
    vi.mocked(client.updateScheduleSettings).mockResolvedValue(SETTINGS);
    load([SCHEDULE], {
      run_mode: "wake_pc",
      voice_on_open: false,
      wake_registered: true,
      timezone: TZ,
    });
    await mount();
    fireEvent.click(screen.getByRole("radio", { name: /While MAYA is open/ }));
    await waitFor(() =>
      expect(bridge.registerScheduleWake).toHaveBeenCalledWith(false),
    );
    await waitFor(() =>
      expect(client.updateScheduleSettings).toHaveBeenCalledWith({
        run_mode: "app_open",
        wake_registered: false,
      }),
    );
  });

  it("offers a retry when the schedules cannot be loaded", async () => {
    vi.mocked(client.listSchedules).mockRejectedValue(new Error("x"));
    render(<SchedulesSection />);
    expect(await screen.findByText(/could not be loaded/)).toBeTruthy();
    expect(screen.getByRole("button", { name: "Retry" })).toBeTruthy();
  });

  it("disables the mail switch with the reason when the server has no mail service", async () => {
    load([], SETTINGS, { mail_configured: false });
    await mount();
    fireEvent.click(screen.getByRole("button", { name: "New schedule" }));
    const form = await screen.findByTestId("schedule-form");
    expect(
      within(form).getByRole("switch", {
        name: "Also send the report by e-mail",
      }),
    ).toBeDisabled();
    expect(within(form).getByTestId("mail-not-configured")).toHaveTextContent(
      "Mail service is not configured on this server",
    );
  });

  it("disables New schedule at the limit and says why", async () => {
    load([SCHEDULE], SETTINGS, { max_schedules: 1 });
    await mount();
    expect(screen.getByRole("button", { name: "New schedule" })).toBeDisabled();
    expect(screen.getByTestId("schedule-limit")).toHaveTextContent(
      "limit of 1 scheduled reports",
    );
  });

  it("uses the account e-mail from the server as already verified", async () => {
    load([], SETTINGS, { account_email: "srv@example.com" });
    await mount(null);
    fireEvent.click(screen.getByRole("button", { name: "New schedule" }));
    const form = await screen.findByTestId("schedule-form");
    fireEvent.click(
      within(form).getByRole("switch", {
        name: "Also send the report by e-mail",
      }),
    );
    const list = within(form).getByTestId("recipients");
    expect(within(list).getByText("srv@example.com")).toBeTruthy();
    expect(within(list).getByText("Verified")).toBeTruthy();
  });

  it("marks an already verified address without asking for a code", async () => {
    load([], SETTINGS);
    vi.mocked(client.requestRecipientCode).mockResolvedValue({
      sent: false,
      alreadyVerified: true,
    });
    await mount(null);
    fireEvent.click(screen.getByRole("button", { name: "New schedule" }));
    const form = await screen.findByTestId("schedule-form");
    fireEvent.click(
      within(form).getByRole("switch", {
        name: "Also send the report by e-mail",
      }),
    );
    fireEvent.change(within(form).getByLabelText("Add an address"), {
      target: { value: "known@corp.com" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Send code" }));
    expect(await within(form).findByText("known@corp.com")).toBeTruthy();
    expect(within(form).getByText("Verified")).toBeTruthy();
    expect(within(form).queryByRole("button", { name: "Verify" })).toBeNull();
  });

  it("can save with an unverified recipient, and sends day codes", async () => {
    load([], SETTINGS);
    vi.mocked(client.requestRecipientCode).mockResolvedValue({
      sent: true,
      alreadyVerified: false,
    });
    vi.mocked(client.createSchedule).mockResolvedValue(SCHEDULE);
    await mount(null);
    fireEvent.click(screen.getByRole("button", { name: "New schedule" }));
    const form = await screen.findByTestId("schedule-form");
    fireEvent.change(within(form).getByLabelText("Name"), {
      target: { value: "Brief" },
    });
    fireEvent.change(
      within(form).getByLabelText("What should the report include?"),
      { target: { value: "News" } },
    );
    fireEvent.change(within(form).getByLabelText("Repeat"), {
      target: { value: "custom_days" },
    });
    fireEvent.click(within(form).getByRole("checkbox", { name: "Friday" }));
    fireEvent.click(
      within(form).getByRole("switch", {
        name: "Also send the report by e-mail",
      }),
    );
    fireEvent.change(within(form).getByLabelText("Add an address"), {
      target: { value: "later@corp.com" },
    });
    fireEvent.click(within(form).getByRole("button", { name: "Send code" }));
    await within(form).findByText("Not verified yet");
    fireEvent.click(
      within(form).getByRole("button", { name: "Create schedule" }),
    );
    await waitFor(() => expect(client.createSchedule).toHaveBeenCalled());
    const body = vi.mocked(client.createSchedule).mock
      .calls[0]![0] as client.ScheduleInput;
    expect(body.cadence.days).toEqual(["mon", "fri"]);
    expect(body.deliver.recipients).toEqual([
      { email: "later@corp.com", verified: false },
    ]);
  });

  it("sends the device time zone when the server holds a different one", async () => {
    load([], { ...SETTINGS, timezone: "Somewhere/Else" });
    await mount();
    await waitFor(() =>
      expect(client.syncDeviceTimezone).toHaveBeenCalledWith(
        "Somewhere/Else",
        true,
      ),
    );
  });
});
