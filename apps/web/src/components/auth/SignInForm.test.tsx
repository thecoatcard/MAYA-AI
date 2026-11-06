import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { getAccessToken, setAccessToken } from "../../lib/auth-session";
import { SignInForm } from "./SignInForm";

const { replace } = vi.hoisted(() => ({ replace: vi.fn() }));
vi.mock("next/navigation", () => ({
  useRouter: () => ({ replace }),
}));

beforeEach(() => {
  replace.mockReset();
  window.localStorage.clear();
});

afterEach(() => {
  setAccessToken(null);
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("SignInForm", () => {
  it("signs in, keeps tokens in memory and navigates to chat", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(
            JSON.stringify({
              access_token: "fake-access-token-placeholder",
              token_type: "bearer",
              expires_in: 900,
            }),
            { status: 200, headers: { "Content-Type": "application/json" } },
          ),
      ),
    );
    render(<SignInForm />);

    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "maya-user" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "fake-password-placeholder" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => expect(replace).toHaveBeenCalledWith("/chat"));
    expect(getAccessToken()).toBe("fake-access-token-placeholder");
    expect(JSON.stringify({ ...window.localStorage })).not.toContain(
      "fake-access-token-placeholder",
    );
    const fetchCall = vi.mocked(fetch).mock.calls[0];
    expect(fetchCall?.[1]).toMatchObject({
      credentials: "same-origin",
      headers: { "X-Maya-Refresh-Transport": "cookie" },
    });
  });

  it("shows a safe invalid-credentials message without echoing the password", async () => {
    vi.stubGlobal(
      "fetch",
      vi.fn(
        async () =>
          new Response(JSON.stringify({ error: "invalid_credentials" }), {
            status: 401,
          }),
      ),
    );
    render(<SignInForm />);

    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "maya-user" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "fake-password-placeholder" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The username or password is incorrect.",
    );
    expect(screen.queryByText("fake-password-placeholder")).toBeNull();
    expect(replace).not.toHaveBeenCalled();
    expect(getAccessToken()).toBeNull();
  });
});
