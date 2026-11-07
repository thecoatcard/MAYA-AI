/**
 * Typed client for Browser Automation and Human Takeover API (Phase 13, Decisions D4, D5, D14).
 */

import { ApiError } from "./api-client";
import { getAccessToken, refreshSession } from "./auth-session";

export interface BrowserTakeoverState {
  readonly session_id: string;
  readonly status: string;
  readonly current_url: string;
  readonly challenge_detected: boolean;
  readonly challenge_type?: string | null;
  readonly screenshot_base64?: string | null;
  readonly message: string;
}

export interface BrowserResumeResponse {
  readonly session_id: string;
  readonly status: string;
  readonly current_url: string;
  readonly message: string;
}

export interface BrowserActionPayload {
  readonly action_type: "navigate" | "click" | "fill" | "screenshot" | "scrape";
  readonly target_url?: string;
  readonly selector?: string;
  readonly input_text?: string;
  readonly screenshot_required?: boolean;
  readonly session_id?: string;
  readonly approval_token?: string;
}

export interface BrowserActionResult {
  readonly success: boolean;
  readonly action_type: string;
  readonly current_url: string;
  readonly status_code?: number;
  readonly title?: string;
  readonly screenshot_base64?: string;
  readonly extracted_text?: string;
  readonly error?: string;
  readonly bot_detected: boolean;
  readonly bot_type?: string;
  readonly execution_time_ms: number;
}

async function authenticatedFetch(
  path: string,
  init?: RequestInit,
): Promise<Response> {
  let token = getAccessToken();
  if (!token) {
    const refreshed = await refreshSession();
    token = refreshed ? getAccessToken() : null;
  }

  const headers = new Headers(init?.headers);
  if (token) {
    headers.set("Authorization", `Bearer ${token}`);
  }

  let response = await fetch(path, { ...init, headers });

  if (response.status === 401) {
    const refreshed = await refreshSession();
    if (refreshed) {
      const retryToken = getAccessToken();
      if (retryToken) {
        headers.set("Authorization", `Bearer ${retryToken}`);
        response = await fetch(path, { ...init, headers });
      }
    }
  }

  if (!response.ok) {
    let errorCode = "unknown_error";
    try {
      const body = (await response.json()) as { error?: string };
      if (body?.error) {
        errorCode = body.error;
      }
    } catch {
      // not JSON
    }
    throw new ApiError(response.status, errorCode);
  }

  return response;
}

export async function dispatchBrowserAction(
  payload: BrowserActionPayload,
): Promise<BrowserActionResult> {
  const response = await authenticatedFetch("/api/v1/browser/action", {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return (await response.json()) as BrowserActionResult;
}

export async function getBrowserTakeoverState(
  sessionId: string,
): Promise<BrowserTakeoverState> {
  const response = await authenticatedFetch(
    `/api/v1/browser/session/${encodeURIComponent(sessionId)}/takeover`,
  );
  return (await response.json()) as BrowserTakeoverState;
}

export async function resumeBrowserSession(
  sessionId: string,
): Promise<BrowserResumeResponse> {
  const response = await authenticatedFetch(
    `/api/v1/browser/session/${encodeURIComponent(sessionId)}/resume`,
    {
      method: "POST",
    },
  );
  return (await response.json()) as BrowserResumeResponse;
}
