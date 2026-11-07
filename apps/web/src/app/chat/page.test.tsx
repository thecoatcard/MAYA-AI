import React from "react";
import { render as rtlRender, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AuthProvider } from "../../hooks/useAuth";
import { setAccessToken } from "../../lib/auth-session";
import ChatPage, { metadata } from "./page";

const mockPush = vi.fn();
const mockReplace = vi.fn();

vi.mock("next/navigation", () => ({
  usePathname: () => "/chat",
  useRouter: () => ({
    push: mockPush,
    replace: mockReplace,
  }),
}));

beforeEach(() => {
  vi.clearAllMocks();
});

afterEach(() => {
  setAccessToken(null);
  vi.unstubAllGlobals();
});

describe("/chat page", () => {
  it("redirects unauthenticated visitor to login via AppShell route guard", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);

    rtlRender(
      <div className="maya-app">
        <AuthProvider>
          <ChatPage />
        </AuthProvider>
      </div>,
    );

    expect(
      await screen.findByTestId("appshell-redirecting"),
    ).toBeInTheDocument();
    await waitFor(() => {
      expect(mockPush).toHaveBeenCalledWith("/login?redirect=%2Fchat");
    });
  });

  it("has a title and a description that reflect the conversational workspace without obsolete notes", () => {
    expect(metadata.title).toBe("Conversational Workspace | MAYA AI");
    expect(String(metadata.description)).not.toContain("not built yet");
    expect(String(metadata.description)).toContain(
      "Conversational intelligence",
    );
  });

  it("renders the Conversational Workspace inside AppShell when authenticated", async () => {
    setAccessToken("test-token");
    const fetchMock = vi.fn(async (url: string) => {
      if (url === "/api/v1/auth/me") {
        return new Response(
          JSON.stringify({
            user_id: "usr-1",
            workspace_id: "ws-primary",
            role: "member",
          }),
          { status: 200, headers: { "Content-Type": "application/json" } },
        );
      }
      if (url === "/api/v1/readyz") {
        return new Response(JSON.stringify({ status: "ready" }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      if (url.includes("/api/v1/chat/sessions")) {
        return new Response(JSON.stringify({ items: [], next_cursor: null }), {
          status: 200,
          headers: { "Content-Type": "application/json" },
        });
      }
      return new Response(JSON.stringify([]), { status: 200 });
    });
    vi.stubGlobal("fetch", fetchMock);

    rtlRender(
      <div className="maya-app">
        <AuthProvider>
          <ChatPage />
        </AuthProvider>
      </div>,
    );

    expect(
      await screen.findByText("Conversational Workspace"),
    ).toBeInTheDocument();
    expect(screen.getByText("Console")).toBeInTheDocument();
    expect(await screen.findByRole("textbox")).toBeInTheDocument();
  });
});
