import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it, vi } from "vitest";

import {
  ApiError,
  BackendUnreachableError,
  NetworkError,
} from "../../lib/api-client";
import * as authClient from "../../lib/auth-client";
import * as authSession from "../../lib/auth-session";
import { UnifiedAuthForm } from "./UnifiedAuthForm";

const replaceMock = vi.fn();

vi.mock("next/navigation", () => ({
  useRouter: () => ({
    replace: replaceMock,
  }),
}));

describe("UnifiedAuthForm", () => {
  beforeEach(() => {
    vi.clearAllMocks();
    vi.spyOn(authClient, "getBootstrapStatus").mockResolvedValue({
      available: false,
    });
  });

  afterEach(() => {
    vi.restoreAllMocks();
  });

  it("offers neutral, user-selected sign-in and create-account choices", () => {
    const identifySpy = vi.spyOn(authClient, "identifyUser");
    const sendOtpSpy = vi.spyOn(authClient, "sendSignupOtp");

    render(<UnifiedAuthForm />);

    expect(screen.getByText("Welcome to MAYA")).toBeInTheDocument();
    expect(screen.getByRole("button", { name: "Sign in" })).toBeInTheDocument();
    expect(
      screen.getByRole("button", { name: "Create account" }),
    ).toBeInTheDocument();
    expect(identifySpy).not.toHaveBeenCalled();
    expect(sendOtpSpy).not.toHaveBeenCalled();
    expect(screen.queryByLabelText("Username or email")).toBeNull();
  });

  it("signs in directly after the user chooses that path without identifying the account", async () => {
    vi.spyOn(authClient, "identifyUser");
    const signInSpy = vi.spyOn(authClient, "signIn").mockResolvedValueOnce({
      accessToken: "fake-access-token-placeholder",
      expiresIn: 900,
    });
    const setAccessTokenSpy = vi.spyOn(authSession, "setAccessToken");

    render(<UnifiedAuthForm initialIdentifier="existing_user" />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(screen.getByLabelText("Username or email")).toHaveValue(
      "existing_user",
    );
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "fake-password-placeholder" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    await waitFor(() => {
      expect(signInSpy).toHaveBeenCalledWith(
        "existing_user",
        "fake-password-placeholder",
      );
      expect(setAccessTokenSpy).toHaveBeenCalledWith(
        "fake-access-token-placeholder",
        900,
      );
      expect(replaceMock).toHaveBeenCalledWith("/chat");
    });
    expect(authClient.identifyUser).not.toHaveBeenCalled();
  });

  it("uses a neutral sign-in failure message instead of confirming account status", async () => {
    vi.spyOn(authClient, "signIn").mockRejectedValueOnce(
      new ApiError(401, "invalid_credentials"),
    );
    render(<UnifiedAuthForm />);

    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    fireEvent.change(screen.getByLabelText("Username or email"), {
      target: { value: "person@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "wrong-password-placeholder" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "The username or email and password could not be verified.",
    );
    expect(screen.queryByText(/password is incorrect/i)).toBeNull();
  });

  it("requests a code only after choosing account creation and keeps the response neutral", async () => {
    const identifySpy = vi.spyOn(authClient, "identifyUser");
    const sendOtpSpy = vi
      .spyOn(authClient, "sendSignupOtp")
      .mockResolvedValueOnce({
        status: "accepted",
        retryAfterSeconds: 45,
      });

    render(<UnifiedAuthForm />);
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    expect(sendOtpSpy).not.toHaveBeenCalled();
    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: "newbie@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Full name"), {
      target: { value: "Newbie User" },
    });
    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "newbie" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    expect(
      await screen.findByLabelText(/6-digit verification code/i),
    ).toBeInTheDocument();
    expect(sendOtpSpy).toHaveBeenCalledWith("newbie@example.com");
    expect(screen.getByRole("status")).toHaveTextContent(
      "If a verification code can be issued for this address, it will arrive shortly.",
    );
    expect(screen.getByRole("status").textContent).not.toMatch(
      /\b(sent|exists|does not exist)\b/i,
    );
    expect(identifySpy).not.toHaveBeenCalled();
  });

  it("completes account creation using details entered by the user", async () => {
    vi.spyOn(authClient, "identifyUser");
    vi.spyOn(authClient, "sendSignupOtp").mockResolvedValueOnce({
      status: "accepted",
      retryAfterSeconds: 45,
    });
    const completeSignupSpy = vi
      .spyOn(authClient, "completeSignup")
      .mockResolvedValueOnce({
        accessToken: "fake-access-token-placeholder",
        expiresIn: 900,
      });
    const setAccessTokenSpy = vi.spyOn(authSession, "setAccessToken");

    render(<UnifiedAuthForm />);
    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: "newbie@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Full name"), {
      target: { value: "Newbie User" },
    });
    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "newbie" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    fireEvent.change(await screen.findByLabelText(/6-digit/i), {
      target: { value: "654321" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "fake-password-placeholder" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Complete registration" }),
    );

    await waitFor(() => {
      expect(completeSignupSpy).toHaveBeenCalledWith({
        email: "newbie@example.com",
        code: "654321",
        username: "newbie",
        password: "fake-password-placeholder",
        full_name: "Newbie User",
      });
      expect(setAccessTokenSpy).toHaveBeenCalledWith(
        "fake-access-token-placeholder",
        900,
      );
      expect(replaceMock).toHaveBeenCalledWith("/chat");
    });
    expect(authClient.identifyUser).not.toHaveBeenCalled();
  });

  it("shows a neutral network error when requesting a verification code fails", async () => {
    vi.spyOn(authClient, "sendSignupOtp").mockRejectedValueOnce(
      new NetworkError("network unavailable"),
    );
    render(<UnifiedAuthForm />);

    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: "person@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Full name"), {
      target: { value: "Test User" },
    });
    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "testuser" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "MAYA could not be reached. Check the connection and try again.",
    );
  });

  it("displays verification failures without making an existence claim", async () => {
    vi.spyOn(authClient, "sendSignupOtp").mockResolvedValueOnce({
      status: "accepted",
      retryAfterSeconds: 60,
    });
    vi.spyOn(authClient, "completeSignup").mockRejectedValueOnce(
      new ApiError(400, "invalid_or_expired_code"),
    );
    render(<UnifiedAuthForm />);

    fireEvent.click(screen.getByRole("button", { name: "Create account" }));
    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: "person@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Full name"), {
      target: { value: "Test User" },
    });
    fireEvent.change(screen.getByLabelText("Username"), {
      target: { value: "testuser" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Continue" }));
    fireEvent.change(await screen.findByLabelText(/6-digit/i), {
      target: { value: "000000" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "fake-password-placeholder" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Complete registration" }),
    );

    expect(await screen.findByRole("alert")).toHaveTextContent(
      "That verification code is invalid or has expired. Request a new code or try again.",
    );
  });

  it("never offers hard-coded development credentials", async () => {
    render(<UnifiedAuthForm />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    expect(screen.queryByText(/Fill Admin/i)).toBeNull();
    expect(screen.queryByText(/AdminPassword/)).toBeNull();
    expect(screen.queryByText(/Local Development Account/i)).toBeNull();
  });

  it("shows the first-run owner form when bootstrap is available and signs in on submit", async () => {
    vi.mocked(authClient.getBootstrapStatus).mockResolvedValue({
      available: true,
    });
    const bootstrapSpy = vi
      .spyOn(authClient, "bootstrapOwner")
      .mockResolvedValueOnce({
        accessToken: "fake-access-token-placeholder",
        expiresIn: 900,
      });

    render(<UnifiedAuthForm />);

    expect(
      await screen.findByText("Create your local owner account"),
    ).toBeInTheDocument();
    fireEvent.change(screen.getByLabelText("Full name"), {
      target: { value: "Ada Owner" },
    });
    fireEvent.change(screen.getByLabelText("Email address"), {
      target: { value: "ada@example.com" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "fake-password-placeholder" },
    });
    fireEvent.click(
      screen.getByRole("button", { name: "Create owner account" }),
    );

    await waitFor(() => {
      expect(bootstrapSpy).toHaveBeenCalledWith({
        fullName: "Ada Owner",
        email: "ada@example.com",
        password: "fake-password-placeholder",
      });
      expect(replaceMock).toHaveBeenCalledWith("/chat");
    });
  });

  it("shows can't-reach with Retry instead of a login form when the backend is down", async () => {
    vi.mocked(authClient.getBootstrapStatus)
      .mockRejectedValueOnce(new BackendUnreachableError())
      .mockResolvedValueOnce({ available: false });

    render(<UnifiedAuthForm />);

    expect(
      await screen.findByText("Can't reach the MAYA backend"),
    ).toBeInTheDocument();
    expect(screen.queryByText("Welcome to MAYA")).toBeNull();

    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    expect(await screen.findByText("Welcome to MAYA")).toBeInTheDocument();
  });

  it("ignores an unsafe redirect query parameter after sign-in", async () => {
    window.history.pushState({}, "", "/login?redirect=//evil.example/x");
    vi.spyOn(authClient, "signIn").mockResolvedValueOnce({
      accessToken: "fake-access-token-placeholder",
      expiresIn: 900,
    });
    render(<UnifiedAuthForm />);
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    fireEvent.change(screen.getByLabelText("Username or email"), {
      target: { value: "someone" },
    });
    fireEvent.change(screen.getByLabelText("Password"), {
      target: { value: "fake-password-placeholder" },
    });
    fireEvent.click(screen.getByRole("button", { name: "Sign in" }));
    await waitFor(() => expect(replaceMock).toHaveBeenCalledWith("/chat"));
    window.history.pushState({}, "", "/");
  });
});
