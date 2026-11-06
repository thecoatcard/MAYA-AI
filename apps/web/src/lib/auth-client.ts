import { apiErrorFromResponse, NetworkError } from "./api-client";
import {
  refreshSession,
  type RefreshedSession,
  setAccessToken,
  withAuthCookieLock,
} from "./auth-session";

export type AuthTokens = RefreshedSession;

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

async function request(
  path: string,
  init: RequestInit,
  cookieLockHeld = false,
): Promise<Response> {
  const usesRefreshCookie = [
    "/token",
    "/refresh",
    "/logout",
    "/signup/complete",
  ].includes(path);
  const send = async (): Promise<Response> => {
    let response: Response;
    try {
      response = await fetch(`/api/v1/auth${path}`, {
        ...init,
        cache: "no-store",
        credentials: "same-origin",
        headers: {
          Accept: "application/json",
          ...(init.body === undefined
            ? {}
            : { "Content-Type": "application/json" }),
          ...(usesRefreshCookie
            ? { "X-Maya-Refresh-Transport": "cookie" }
            : {}),
          ...init.headers,
        },
      });
    } catch (error) {
      throw new NetworkError(error);
    }
    if (!response.ok) {
      throw await apiErrorFromResponse(response);
    }
    return response;
  };
  return usesRefreshCookie && !cookieLockHeld
    ? withAuthCookieLock(send)
    : send();
}

async function readTokens(response: Response): Promise<AuthTokens> {
  let body: unknown;
  try {
    body = await response.json();
  } catch {
    throw new Error("The authentication response was not valid JSON.");
  }
  if (
    !isRecord(body) ||
    typeof body.access_token !== "string" ||
    body.access_token === "" ||
    body.token_type !== "bearer" ||
    typeof body.expires_in !== "number" ||
    body.expires_in <= 0
  ) {
    throw new Error("The authentication response had an unexpected shape.");
  }
  return { accessToken: body.access_token, expiresIn: body.expires_in };
}

export async function signIn(
  username: string,
  password: string,
): Promise<AuthTokens> {
  return withAuthCookieLock(async () => {
    const tokens = await readTokens(
      await request(
        "/token",
        {
          method: "POST",
          body: JSON.stringify({ username, password }),
        },
        true,
      ),
    );
    setAccessToken(tokens.accessToken, tokens.expiresIn);
    return tokens;
  });
}

export { refreshSession };

export async function signOut(): Promise<void> {
  await withAuthCookieLock(async () => {
    await request("/logout", { method: "POST" }, true);
    setAccessToken(null);
  });
}

export interface AcceptedResult {
  readonly status: "accepted";
  readonly retryAfterSeconds: number | null;
}

async function postJson(
  path: string,
  body: Record<string, string>,
): Promise<Response> {
  return request(path, { method: "POST", body: JSON.stringify(body) });
}

export async function registerAccount(body: {
  username: string;
  email: string;
  password: string;
  full_name: string;
  workspace_name?: string;
}): Promise<void> {
  const response = await postJson("/register", body);
  if (response.status !== 202) {
    throw new Error("The account request had an unexpected response.");
  }
}

export async function verifyEmail(body: {
  email: string;
  code: string;
  password: string;
}): Promise<void> {
  const response = await postJson("/verify-email", body);
  if (response.status !== 200) {
    throw new Error("The verification request had an unexpected response.");
  }
}

export async function resendVerification(
  email: string,
): Promise<AcceptedResult> {
  const response = await postJson("/resend-verification", { email });
  if (response.status !== 202) {
    throw new Error("The resend request had an unexpected response.");
  }
  const retryAfter = response.headers.get("Retry-After");
  const parsed = retryAfter === null ? Number.NaN : Number(retryAfter);
  return {
    status: "accepted",
    retryAfterSeconds:
      Number.isFinite(parsed) && parsed > 0 ? Math.ceil(parsed) : null,
  };
}

export async function requestPasswordReset(email: string): Promise<void> {
  const response = await postJson("/forgot-password", { email });
  if (response.status !== 202) {
    throw new Error(
      "The password recovery request had an unexpected response.",
    );
  }
}

export async function resetPassword(body: {
  email: string;
  code: string;
  new_password: string;
}): Promise<void> {
  const response = await postJson("/reset-password", body);
  if (response.status !== 200) {
    throw new Error("The password reset had an unexpected response.");
  }
}

export async function identifyUser(identifier: string): Promise<void> {
  const response = await postJson("/identify", { identifier });
  if (response.status !== 200) {
    throw new Error("The identifier request had an unexpected response.");
  }
  const data: unknown = await response.json();
  if (
    typeof data !== "object" ||
    data === null ||
    !("status" in data) ||
    data.status !== "accepted"
  ) {
    throw new Error("The identifier response had an unexpected shape.");
  }
}

export async function sendSignupOtp(email: string): Promise<AcceptedResult> {
  const response = await postJson("/signup/otp", { email });
  if (response.status !== 202) {
    throw new Error(
      "The signup verification request had an unexpected response.",
    );
  }
  const retryAfter = response.headers.get("Retry-After");
  const parsed = retryAfter === null ? Number.NaN : Number(retryAfter);
  return {
    status: "accepted",
    retryAfterSeconds:
      Number.isFinite(parsed) && parsed > 0 ? Math.ceil(parsed) : null,
  };
}

export async function completeSignup(body: {
  email: string;
  code: string;
  username: string;
  password: string;
  full_name: string;
  workspace_name?: string;
}): Promise<AuthTokens> {
  return withAuthCookieLock(async () => {
    const response = await request(
      "/signup/complete",
      {
        method: "POST",
        body: JSON.stringify(body),
      },
      true,
    );
    const tokens = await readTokens(response);
    setAccessToken(tokens.accessToken, tokens.expiresIn);
    return tokens;
  });
}
