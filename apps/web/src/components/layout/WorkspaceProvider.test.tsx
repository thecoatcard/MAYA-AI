import React from "react";
import { act, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuth } from "../../hooks/useAuth";
import { checkBackendReady } from "../../lib/api-client";
import { listApprovals } from "../../lib/approvals-client";
import { makeAuth } from "./layout-test-utils";
import {
  APPROVALS_POLL_MS,
  EMPTY_CONVERSATION,
  EMPTY_VOICE,
  WorkspaceProvider,
  useWorkspace,
} from "./WorkspaceProvider";

vi.mock("../../hooks/useAuth", () => ({ useAuth: vi.fn() }));
vi.mock("../../lib/api-client", () => ({ checkBackendReady: vi.fn() }));
vi.mock("../../lib/approvals-client", () => ({ listApprovals: vi.fn() }));

function Probe(): React.JSX.Element {
  const ws = useWorkspace();
  return (
    <div>
      <span data-testid="backend">{ws.backend.state}</span>
      <span data-testid="reason">{ws.backend.reason ?? ""}</span>
      <span data-testid="pending">{ws.pendingApprovals}</span>
      <span data-testid="user">{ws.auth.user?.user_id ?? "none"}</span>
      <span data-testid="conv">
        {String(ws.conversation === EMPTY_CONVERSATION)}
      </span>
      <span data-testid="voice">{String(ws.voice === EMPTY_VOICE)}</span>
    </div>
  );
}

beforeEach(() => {
  vi.mocked(useAuth).mockReturnValue(makeAuth());
  vi.mocked(checkBackendReady).mockResolvedValue({ ready: true });
  vi.mocked(listApprovals).mockResolvedValue([
    {} as never,
    {} as never,
    {} as never,
  ]);
});

afterEach(() => {
  vi.useRealTimers();
  vi.clearAllMocks();
});

describe("WorkspaceProvider", () => {
  it("exposes auth, backend readiness, pending approvals and empty extension points", async () => {
    render(
      <WorkspaceProvider>
        <Probe />
      </WorkspaceProvider>,
    );
    expect(screen.getByTestId("backend")).toHaveTextContent("checking");
    await waitFor(() =>
      expect(screen.getByTestId("backend")).toHaveTextContent("ready"),
    );
    await waitFor(() =>
      expect(screen.getByTestId("pending")).toHaveTextContent("3"),
    );
    expect(listApprovals).toHaveBeenCalledWith("pending", 50);
    expect(screen.getByTestId("user")).toHaveTextContent("usr-alice");
    expect(screen.getByTestId("conv")).toHaveTextContent("true");
    expect(screen.getByTestId("voice")).toHaveTextContent("true");
  });

  it("reports an unreachable backend with its reason", async () => {
    vi.mocked(checkBackendReady).mockResolvedValue({
      ready: false,
      reason: "unreachable",
    });
    render(
      <WorkspaceProvider>
        <Probe />
      </WorkspaceProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("backend")).toHaveTextContent("unreachable"),
    );
    expect(screen.getByTestId("reason")).toHaveTextContent("unreachable");
  });

  it("does not load approvals when signed out", async () => {
    vi.mocked(useAuth).mockReturnValue(
      makeAuth({ isAuthenticated: false, user: null, token: null }),
    );
    render(
      <WorkspaceProvider>
        <Probe />
      </WorkspaceProvider>,
    );
    await waitFor(() =>
      expect(screen.getByTestId("backend")).toHaveTextContent("ready"),
    );
    expect(listApprovals).not.toHaveBeenCalled();
    expect(screen.getByTestId("pending")).toHaveTextContent("0");
  });

  it("polls approvals lightly and keeps the last count when a poll fails", async () => {
    vi.useFakeTimers();
    render(
      <WorkspaceProvider>
        <Probe />
      </WorkspaceProvider>,
    );
    await act(async () => {
      await vi.advanceTimersByTimeAsync(0);
    });
    expect(screen.getByTestId("pending")).toHaveTextContent("3");
    vi.mocked(listApprovals).mockRejectedValue(new Error("offline"));
    await act(async () => {
      await vi.advanceTimersByTimeAsync(APPROVALS_POLL_MS);
    });
    expect(listApprovals).toHaveBeenCalledTimes(2);
    expect(screen.getByTestId("pending")).toHaveTextContent("3");
  });

  it("throws when used outside the provider", () => {
    const spy = vi.spyOn(console, "error").mockImplementation(() => {});
    expect(() => render(<Probe />)).toThrow(/WorkspaceProvider/);
    spy.mockRestore();
  });
});
