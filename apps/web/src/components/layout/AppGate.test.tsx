import React from "react";
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { useAuth } from "../../hooks/useAuth";
import { checkBackendReady } from "../../lib/api-client";
import { listApprovals } from "../../lib/approvals-client";
import { AppGate, describeBackendReason } from "./AppGate";
import { makeAuth } from "./layout-test-utils";
import { WorkspaceProvider } from "./WorkspaceProvider";

vi.mock("../../hooks/useAuth", () => ({ useAuth: vi.fn() }));
vi.mock("../../lib/api-client", () => ({ checkBackendReady: vi.fn() }));
vi.mock("../../lib/approvals-client", () => ({ listApprovals: vi.fn() }));
vi.mock("../auth/AuthView", () => ({
  AuthView: () => <div data-testid="auth-view">auth view</div>,
}));

function renderGate(): void {
  render(
    <WorkspaceProvider>
      <AppGate>
        <p>workspace content</p>
      </AppGate>
    </WorkspaceProvider>,
  );
}

beforeEach(() => {
  vi.mocked(useAuth).mockReturnValue(makeAuth());
  vi.mocked(checkBackendReady).mockResolvedValue({ ready: true });
  vi.mocked(listApprovals).mockResolvedValue([]);
});

afterEach(() => vi.clearAllMocks());

describe("AppGate", () => {
  it("booting: shows a status while the backend check is pending", () => {
    vi.mocked(checkBackendReady).mockReturnValue(new Promise(() => {}));
    renderGate();
    expect(screen.getByTestId("gate-booting")).toBeInTheDocument();
    expect(screen.queryByText("workspace content")).not.toBeInTheDocument();
  });

  it("booting: waits while the session is being restored", async () => {
    vi.mocked(useAuth).mockReturnValue(
      makeAuth({ isLoading: true, isAuthenticated: false, user: null }),
    );
    renderGate();
    await waitFor(() => expect(checkBackendReady).toHaveBeenCalled());
    expect(screen.getByTestId("gate-booting")).toBeInTheDocument();
  });

  it("backend_unreachable: explains, offers Retry and the first-run hint", async () => {
    vi.mocked(useAuth).mockReturnValue(
      makeAuth({ isAuthenticated: false, user: null, token: null }),
    );
    vi.mocked(checkBackendReady).mockResolvedValue({
      ready: false,
      reason: "unreachable",
    });
    renderGate();
    expect(
      await screen.findByText("Can't reach the MAYA backend"),
    ).toBeInTheDocument();
    expect(screen.getByText(/first run/i)).toBeInTheDocument();

    vi.mocked(checkBackendReady).mockResolvedValue({ ready: true });
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByTestId("auth-view")).toBeInTheDocument();
    expect(checkBackendReady).toHaveBeenCalledTimes(2);
  });

  it("signed_out: renders the auth view inside the app chrome", async () => {
    vi.mocked(useAuth).mockReturnValue(
      makeAuth({ isAuthenticated: false, user: null, token: null }),
    );
    renderGate();
    expect(await screen.findByTestId("auth-view")).toBeInTheDocument();
    expect(screen.getByTestId("gate-signed-out")).toBeInTheDocument();
    expect(screen.getAllByRole("main")).toHaveLength(1);
  });

  it("ready: renders the children", async () => {
    renderGate();
    expect(await screen.findByText("workspace content")).toBeInTheDocument();
  });

  it("does not eject a signed-in user when the backend goes away", async () => {
    vi.mocked(checkBackendReady).mockResolvedValue({
      ready: false,
      reason: "unreachable",
    });
    renderGate();
    expect(await screen.findByText("workspace content")).toBeInTheDocument();
  });

  it("backend_unreachable: maps the machine reason to plain words", async () => {
    vi.mocked(useAuth).mockReturnValue(
      makeAuth({ isAuthenticated: false, user: null, token: null }),
    );
    vi.mocked(checkBackendReady).mockResolvedValue({
      ready: false,
      reason: "status_503",
    });
    renderGate();
    expect(
      await screen.findByTestId("gate-unreachable-reason"),
    ).toHaveTextContent("HTTP 503");
    expect(describeBackendReason("Failed to fetch")).not.toContain("fetch");
    expect(describeBackendReason(undefined)).toMatch(/did not answer/);
  });

  it("signed_out after a signed-in session: says the session ended", async () => {
    vi.mocked(useAuth).mockReturnValue(makeAuth());
    const tree = (): React.JSX.Element => (
      <WorkspaceProvider>
        <AppGate>
          <p>workspace content</p>
        </AppGate>
      </WorkspaceProvider>
    );
    const view = render(tree());
    expect(await screen.findByText("workspace content")).toBeInTheDocument();
    vi.mocked(useAuth).mockReturnValue(
      makeAuth({ isAuthenticated: false, user: null, token: null }),
    );
    view.rerender(tree());
    expect(await screen.findByText("Your session ended")).toBeInTheDocument();
    expect(screen.getByTestId("auth-view")).toBeInTheDocument();
  });
});
