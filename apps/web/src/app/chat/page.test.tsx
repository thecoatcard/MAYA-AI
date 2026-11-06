import React from "react";
import { render as rtlRender, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setAccessToken } from "../../lib/auth-session";
import ChatPage, { metadata } from "./page";

afterEach(() => {
  setAccessToken(null);
  vi.unstubAllGlobals();
});

describe("/chat page", () => {
  it("checks the refresh cookie before showing the signed-out state", async () => {
    const fetchMock = vi.fn(async () => new Response(null, { status: 401 }));
    vi.stubGlobal("fetch", fetchMock);
    rtlRender(
      <div className="maya-app">
        <ChatPage />
      </div>,
    );
    expect(
      await screen.findByRole("heading", { name: "Sign in to chat" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(fetchMock).toHaveBeenCalledWith(
      "/api/v1/auth/refresh",
      expect.objectContaining({
        method: "POST",
        credentials: "same-origin",
      }),
    );
  });

  it("has a title and a description that match what exists", () => {
    expect(metadata.title).toBe("Chat | MAYA AI");
    expect(String(metadata.description)).toContain("not built yet");
  });
});
