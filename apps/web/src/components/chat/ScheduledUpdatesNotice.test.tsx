import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import * as client from "../../lib/schedules-client";
import {
  ConversationContext,
  type ConversationRuntimeValue,
} from "./ConversationContext";
import {
  BLOCKED_NOTICE,
  READING_NOTICE,
  ScheduledUpdatesNotice,
} from "./ScheduledUpdatesNotice";

vi.mock("../../lib/schedules-client", async (importOriginal) => ({
  ...(await importOriginal<typeof import("../../lib/schedules-client")>()),
  listScheduleUpdates: vi.fn(),
  ackScheduleUpdate: vi.fn(),
  syncDeviceTimezone: vi.fn(async () => undefined),
}));

const UPDATE: client.ScheduleUpdate = {
  schedule_id: "s1",
  name: "AI news",
  run_at: "2026-11-13T02:30:00Z",
  status: "done",
  summary: "Three big releases this week.",
  spoken_summary: "Three big releases.",
  format: "pdf",
  artifact_ids: ["a1"],
  needs_attention: false,
  voice_on_open: false,
  reason: "",
  mail_status: "",
};

function runtime(
  over: Partial<ConversationRuntimeValue> & { state?: string; muted?: boolean },
): ConversationRuntimeValue {
  const { state = "idle", muted = false, ...rest } = over;
  return {
    voice: { state, muted },
    voiceLive: state === "listening",
    startListenOnlyMaster: vi.fn(),
    unmuteMaster: vi.fn(),
    ...rest,
  } as unknown as ConversationRuntimeValue;
}

function mount(value: ConversationRuntimeValue) {
  return render(
    <ConversationContext.Provider value={value}>
      <ScheduledUpdatesNotice />
    </ConversationContext.Provider>,
  );
}

/** The server resolves "voice on open" per update. */
function setVoiceSetting(on: boolean): void {
  vi.mocked(client.listScheduleUpdates).mockResolvedValue([
    { ...UPDATE, voice_on_open: on },
  ]);
}

describe("ScheduledUpdatesNotice", () => {
  beforeEach(() => {
    vi.mocked(client.listScheduleUpdates).mockReset();
    vi.mocked(client.ackScheduleUpdate).mockReset();
    vi.mocked(client.listScheduleUpdates).mockResolvedValue([UPDATE]);
    vi.mocked(client.ackScheduleUpdate).mockResolvedValue();
    setVoiceSetting(false);
  });
  afterEach(() => {
    vi.unstubAllGlobals();
  });

  it("renders nothing when there are no updates", async () => {
    vi.mocked(client.listScheduleUpdates).mockResolvedValue([]);
    const { container } = mount(runtime({}));
    await waitFor(() => expect(client.listScheduleUpdates).toHaveBeenCalled());
    expect(
      container.querySelector("[data-testid=scheduled-updates]"),
    ).toBeNull();
  });

  it("never draws the report over the conversation", async () => {
    mount(runtime({}));
    await waitFor(() => expect(client.ackScheduleUpdate).toHaveBeenCalled());
    expect(screen.queryByTestId("update-report")).toBeNull();
    expect(screen.queryByText("Three big releases this week.")).toBeNull();
    expect(screen.queryByTestId("scheduled-updates")).toBeNull();
  });

  it("acknowledges a clean, unspoken update silently, once", async () => {
    mount(runtime({}));
    await waitFor(() =>
      expect(client.ackScheduleUpdate).toHaveBeenCalledWith("s1"),
    );
    expect(client.ackScheduleUpdate).toHaveBeenCalledTimes(1);
  });

  it("shows only a quiet needs-attention line, never the report", async () => {
    vi.mocked(client.listScheduleUpdates).mockResolvedValue([
      {
        ...UPDATE,
        status: "needs_attention",
        needs_attention: true,
        artifact_ids: [],
      },
    ]);
    mount(runtime({}));
    expect(await screen.findByText("Needs attention")).toBeTruthy();
    expect(screen.queryByText("Three big releases this week.")).toBeNull();
    expect(client.ackScheduleUpdate).not.toHaveBeenCalled();
  });

  it("acknowledges a problem line on Dismiss and keeps it when that fails", async () => {
    vi.mocked(client.listScheduleUpdates).mockResolvedValue([
      { ...UPDATE, needs_attention: true },
    ]);
    vi.mocked(client.ackScheduleUpdate).mockRejectedValue(new Error("x"));
    mount(runtime({}));
    fireEvent.click(await screen.findByRole("button", { name: /Dismiss/ }));
    expect(await screen.findByRole("alert")).toHaveTextContent(
      /Could not dismiss/,
    );
    expect(screen.getByText("AI news")).toBeTruthy();
  });

  it("starts the listen-only voice only when the setting is on", async () => {
    const off = runtime({});
    mount(off);
    await waitFor(() => expect(client.ackScheduleUpdate).toHaveBeenCalled());
    expect(off.startListenOnlyMaster).not.toHaveBeenCalled();
  });

  it("starts the listen-only voice when an update is marked voice_on_open", async () => {
    setVoiceSetting(true);
    const value = runtime({});
    mount(value);
    await waitFor(() => expect(value.startListenOnlyMaster).toHaveBeenCalled());
  });

  it("does not acknowledge a voice update: the backend does after the spoken turn", async () => {
    setVoiceSetting(true);
    const value = runtime({});
    mount(value);
    await waitFor(() => expect(value.startListenOnlyMaster).toHaveBeenCalled());
    expect(client.ackScheduleUpdate).not.toHaveBeenCalled();
  });

  it("shows the reason and why mail was not sent", async () => {
    vi.mocked(client.listScheduleUpdates).mockResolvedValue([
      {
        ...UPDATE,
        reason: "Two sources failed",
        mail_status: "not_configured",
      },
    ]);
    mount(runtime({}));
    expect(await screen.findByTestId("update-reason")).toHaveTextContent(
      "Two sources failed",
    );
    expect(screen.getByTestId("update-mail-status")).toHaveTextContent(
      "Mail not sent: the mail service is not configured on this server.",
    );
  });

  it("says nothing about mail when it was sent", async () => {
    vi.mocked(client.listScheduleUpdates).mockResolvedValue([
      { ...UPDATE, mail_status: "sent" },
    ]);
    mount(runtime({}));
    await waitFor(() => expect(client.ackScheduleUpdate).toHaveBeenCalled());
    expect(screen.queryByTestId("update-mail-status")).toBeNull();
  });

  it("syncs the device time zone once on start", async () => {
    mount(runtime({}));
    await waitFor(() => expect(client.syncDeviceTimezone).toHaveBeenCalled());
  });

  it("shows the muted reading notice and Unmute while the session is live", async () => {
    setVoiceSetting(true);
    const idle = runtime({});
    const { rerender } = mount(idle);
    await waitFor(() => expect(idle.startListenOnlyMaster).toHaveBeenCalled());
    const live = runtime({ state: "listening", muted: true });
    rerender(
      <ConversationContext.Provider value={live}>
        <ScheduledUpdatesNotice />
      </ConversationContext.Provider>,
    );
    expect(await screen.findByText(READING_NOTICE)).toBeTruthy();
    fireEvent.click(screen.getByRole("button", { name: "Unmute" }));
    expect(live.unmuteMaster).toHaveBeenCalled();
  });

  it("does not start voice when microphone permission is denied", async () => {
    setVoiceSetting(true);
    vi.stubGlobal("navigator", {
      ...navigator,
      permissions: { query: vi.fn(async () => ({ state: "denied" })) },
    });
    const value = runtime({});
    mount(value);
    expect(await screen.findByText(BLOCKED_NOTICE)).toBeTruthy();
    expect(value.startListenOnlyMaster).not.toHaveBeenCalled();
  });

  it("waits for a click when the browser has not allowed audio yet", async () => {
    setVoiceSetting(true);
    vi.stubGlobal("navigator", {
      ...navigator,
      userActivation: { hasBeenActive: false },
    });
    const value = runtime({});
    mount(value);
    const button = await screen.findByRole("button", { name: "Read it aloud" });
    expect(value.startListenOnlyMaster).not.toHaveBeenCalled();
    fireEvent.click(button);
    expect(value.startListenOnlyMaster).toHaveBeenCalledTimes(1);
  });
});
