/** Typed client for GET /api/v1/decisions/status (no secrets, no content). */

import { getAccessToken, refreshSession } from "./auth-session";

export interface DecisionStartupCheck {
  state: "pending" | "ok" | "failed" | string;
  reason: string;
  checked_at?: string;
}

export interface DecisionStatus {
  enabled: boolean;
  model: string;
  circuit: string;
  startup_check?: DecisionStartupCheck;
}

export async function getDecisionStatus(): Promise<DecisionStatus> {
  let token = getAccessToken();
  if (!token) {
    const refreshed = await refreshSession();
    token = refreshed ? getAccessToken() : null;
  }
  const headers = new Headers();
  if (token) headers.set("Authorization", `Bearer ${token}`);
  const response = await fetch("/api/v1/decisions/status", { headers });
  if (!response.ok) throw new Error(`status ${response.status}`);
  const body = (await response.json()) as Partial<DecisionStatus>;
  return {
    enabled: body.enabled === true,
    model: typeof body.model === "string" ? body.model : "",
    circuit: typeof body.circuit === "string" ? body.circuit : "closed",
    startup_check: body.startup_check,
  };
}
