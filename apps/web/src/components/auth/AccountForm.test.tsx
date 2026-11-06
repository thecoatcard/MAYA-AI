import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import { AccountForm } from "./AccountForm";

beforeEach(() => {
  window.localStorage.clear();
});

afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  window.localStorage.clear();
});

describe("AccountForm", () => {
  describe("register mode", () => {
    it("renders all registration fields and submits account details", async () => {
      const fetchMock = vi.fn(
        async () =>
          new Response(JSON.stringify({ status: "accepted" }), {
            status: 202,
            headers: { "Content-Type": "application/json" },
          }),
      );
      vi.stubGlobal("fetch", fetchMock);

      render(<AccountForm mode="register" />);

      expect(
        screen.getByRole("heading", { name: "Create your account", level: 1 }),
      ).toBeInTheDocument();
      expect(screen.getByLabelText("Full name")).toBeInTheDocument();
      expect(screen.getByLabelText("Username")).toBeInTheDocument();
      expect(screen.getByLabelText("Email")).toBeInTheDocument();
      expect(screen.getByLabelText("Password")).toBeInTheDocument();
      expect(
        screen.getByLabelText("Workspace name (optional)"),
      ).toBeInTheDocument();

      fireEvent.change(screen.getByLabelText("Full name"), {
        target: { value: "Test User" },
      });
      fireEvent.change(screen.getByLabelText("Username"), {
        target: { value: "testuser" },
      });
      fireEvent.change(screen.getByLabelText("Email"), {
        target: { value: "user@example.com" },
      });
      fireEvent.change(screen.getByLabelText("Password"), {
        target: { value: "example-user-password-123!" },
      });
      fireEvent.change(screen.getByLabelText("Workspace name (optional)"), {
        target: { value: "My Org" },
      });

      fireEvent.click(screen.getByRole("button", { name: "Create account" }));

      await waitFor(() => {
        expect(screen.getByRole("status")).toHaveTextContent(
          "If these details can be used, a verification message will arrive shortly.",
        );
      });

      expect(fetchMock).toHaveBeenCalledTimes(1);
      const [url, init] = fetchMock.mock.calls[0] as unknown as [
        string,
        RequestInit,
      ];
      expect(url).toBe("/api/v1/auth/register");
      expect(init.method).toBe("POST");
      expect(JSON.parse(init.body as string)).toEqual({
        full_name: "Test User",
        username: "testuser",
        email: "user@example.com",
        password: "example-user-password-123!",
        workspace_name: "My Org",
      });

      expect(
        screen.getByRole("link", { name: "Enter a verification code" }),
      ).toHaveAttribute("href", "/verify?email=user%40example.com");
      expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute(
        "href",
        "/login",
      );
    });
  });

  describe("verify mode", () => {
    it("submits verification code and registration password", async () => {
      const fetchMock = vi.fn(
        async () =>
          new Response(JSON.stringify({ status: "verified" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
      );
      vi.stubGlobal("fetch", fetchMock);

      render(<AccountForm mode="verify" />);

      expect(
        screen.getByRole("heading", { name: "Verify your email", level: 1 }),
      ).toBeInTheDocument();

      fireEvent.change(screen.getByLabelText("Email"), {
        target: { value: "user@example.com" },
      });
      fireEvent.change(screen.getByLabelText("6-digit code"), {
        target: { value: "123456" },
      });
      fireEvent.change(screen.getByLabelText("Registration password"), {
        target: { value: "example-user-password-123!" },
      });

      fireEvent.click(screen.getByRole("button", { name: "Verify email" }));

      await waitFor(() => {
        expect(screen.getByRole("status")).toHaveTextContent(
          "Email verification is complete. You can now sign in.",
        );
      });

      const [url, init] = fetchMock.mock.calls[0] as unknown as [
        string,
        RequestInit,
      ];
      expect(url).toBe("/api/v1/auth/verify-email");
      expect(JSON.parse(init.body as string)).toEqual({
        email: "user@example.com",
        code: "123456",
        password: "example-user-password-123!",
      });

      expect(screen.getByRole("link", { name: "Sign in" })).toHaveAttribute(
        "href",
        "/login",
      );
    });

    it("supports resending the verification code with cooldown", async () => {
      const fetchMock = vi.fn(
        async () =>
          new Response(JSON.stringify({ status: "accepted" }), {
            status: 202,
            headers: {
              "Content-Type": "application/json",
              "Retry-After": "60",
            },
          }),
      );
      vi.stubGlobal("fetch", fetchMock);

      render(<AccountForm mode="verify" />);

      fireEvent.change(screen.getByLabelText("Email"), {
        target: { value: "user@example.com" },
      });

      const resendBtn = screen.getByRole("button", { name: "Resend code" });
      fireEvent.click(resendBtn);

      await waitFor(() => {
        expect(screen.getByRole("status")).toHaveTextContent(
          "If this email can be used, a verification message will arrive shortly.",
        );
      });

      expect(fetchMock).toHaveBeenCalledWith(
        "/api/v1/auth/resend-verification",
        expect.objectContaining({
          method: "POST",
          body: JSON.stringify({ email: "user@example.com" }),
        }),
      );
    });
  });

  describe("forgot password mode", () => {
    it("submits email for password recovery", async () => {
      const fetchMock = vi.fn(
        async () =>
          new Response(JSON.stringify({ status: "accepted" }), {
            status: 202,
            headers: { "Content-Type": "application/json" },
          }),
      );
      vi.stubGlobal("fetch", fetchMock);

      render(<AccountForm mode="forgot" />);

      fireEvent.change(screen.getByLabelText("Email"), {
        target: { value: "user@example.com" },
      });

      fireEvent.click(
        screen.getByRole("button", { name: "Send instructions" }),
      );

      await waitFor(() => {
        expect(screen.getByRole("status")).toHaveTextContent(
          "If this email can be used, password reset instructions will arrive shortly.",
        );
      });

      expect(
        screen.getByRole("link", { name: "Enter a reset code" }),
      ).toHaveAttribute("href", "/reset-password");
    });
  });

  describe("reset password mode", () => {
    it("submits email, code and new password", async () => {
      const fetchMock = vi.fn(
        async () =>
          new Response(JSON.stringify({ status: "password_reset" }), {
            status: 200,
            headers: { "Content-Type": "application/json" },
          }),
      );
      vi.stubGlobal("fetch", fetchMock);

      render(<AccountForm mode="reset" />);

      fireEvent.change(screen.getByLabelText("Email"), {
        target: { value: "user@example.com" },
      });
      fireEvent.change(screen.getByLabelText("6-digit code"), {
        target: { value: "654321" },
      });
      fireEvent.change(screen.getByLabelText("New password"), {
        target: { value: "example-new-password-999!" },
      });

      fireEvent.click(screen.getByRole("button", { name: "Update password" }));

      await waitFor(() => {
        expect(screen.getByRole("status")).toHaveTextContent(
          "Your password has been updated. You can now sign in.",
        );
      });

      expect(
        screen.getByRole("link", { name: "Back to sign in" }),
      ).toHaveAttribute("href", "/login");
    });
  });

  describe("error handling", () => {
    it("displays friendly error message for invalid codes", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(
          async () =>
            new Response(JSON.stringify({ error: "invalid_or_expired_code" }), {
              status: 400,
            }),
        ),
      );

      render(<AccountForm mode="verify" />);

      fireEvent.change(screen.getByLabelText("Email"), {
        target: { value: "user@example.com" },
      });
      fireEvent.change(screen.getByLabelText("6-digit code"), {
        target: { value: "000000" },
      });
      fireEvent.change(screen.getByLabelText("Registration password"), {
        target: { value: "WrongPass" },
      });

      fireEvent.click(screen.getByRole("button", { name: "Verify email" }));

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "That code could not be verified. Check it or request a new one.",
      );
    });

    it("displays error when network is unreachable", async () => {
      vi.stubGlobal(
        "fetch",
        vi.fn(async () => {
          throw new TypeError("Failed to fetch");
        }),
      );

      render(<AccountForm mode="register" />);

      fireEvent.change(screen.getByLabelText("Full name"), {
        target: { value: "Test User" },
      });
      fireEvent.change(screen.getByLabelText("Username"), {
        target: { value: "testuser" },
      });
      fireEvent.change(screen.getByLabelText("Email"), {
        target: { value: "user@example.com" },
      });
      fireEvent.change(screen.getByLabelText("Password"), {
        target: { value: "example-user-password-123!" },
      });

      fireEvent.click(screen.getByRole("button", { name: "Create account" }));

      expect(await screen.findByRole("alert")).toHaveTextContent(
        "MAYA could not be reached. Check the connection and try again.",
      );
    });
  });
});
