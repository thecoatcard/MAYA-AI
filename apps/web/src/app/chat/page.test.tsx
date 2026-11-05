import React from "react";
import { render, screen } from "@testing-library/react";
import { afterEach, describe, expect, it, vi } from "vitest";

import { setAccessToken } from "../../lib/auth-session";
import ChatPage, { metadata } from "./page";

afterEach(() => {
  setAccessToken(null);
  vi.unstubAllGlobals();
});

describe("/chat page", () => {
  it("renders the not-signed-in state without calling the API", () => {
    const fetchMock = vi.fn();
    vi.stubGlobal("fetch", fetchMock);
    render(<ChatPage />);
    expect(
      screen.getByRole("heading", { name: "Sign-in is not built yet" }),
    ).toBeInTheDocument();
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(fetchMock).not.toHaveBeenCalled();
  });

  it("has a title and a description that match what exists", () => {
    expect(metadata.title).toBe("Chat | MAYA AI");
    expect(String(metadata.description)).toContain("not built yet");
  });
});
