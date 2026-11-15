/**
 * Typed client for the caller's own connections (GET/PUT/POST/DELETE
 * /api/v1/settings/connections). The API never returns a token: only a masked status.
 */

import { ApiError, apiErrorFromResponse } from "./api-client";
import { getAccessToken, refreshSession } from "./auth-session";

export type ConnectionState =
  | "not_connected"
  | "saved"
  | "connected"
  | "error"
  | "server_default"
  | "unreadable";

export interface Connection {
  readonly provider: string;
  readonly label: string;
  readonly state: ConnectionState;
  readonly source: "user" | "environment" | null;
  readonly message: string;
  readonly account: string | null;
  readonly scopes: readonly string[] | null;
  readonly database_id: string | null;
  readonly database_title: string | null;
  readonly last_tested_at: string | null;
  readonly takes_database_id: boolean;
  /** "mailbox" for the mailbox card, "token" for GitHub and Notion. */
  readonly kind?: "token" | "mailbox";
  /** Mailbox only: what the form may show back. Never the password; the address is masked. */
  readonly mailbox?: MailboxView | null;
}

export interface MailboxView {
  readonly address: string;
  readonly imap_host: string | null;
  readonly imap_port: number | null;
  readonly smtp_host: string | null;
  readonly smtp_port: number | null;
  readonly password_saved: boolean;
}

/** One half of the mailbox test (incoming or outgoing) with its plain sentence. */
export interface TestStep {
  readonly label: string;
  readonly ok: boolean;
  readonly message: string;
}

/** What the mailbox form sends. `token` is the app password (write-only). */
export interface SavePayload {
  token?: string;
  database_id?: string;
  address?: string;
  imap_host?: string;
  imap_port?: number;
  smtp_host?: string;
  smtp_port?: number;
}

export interface TestOutcome {
  readonly ok: boolean;
  readonly code: string;
  readonly message: string;
  readonly account: string | null;
  readonly scopes: readonly string[] | null;
  readonly database_title: string | null;
  readonly steps?: readonly TestStep[] | null;
}

export interface ConnectionList {
  readonly encryption_available: boolean;
  // Server tokens exist but MAYA_ENV_CONNECTIONS_USERS names nobody (additive; absent on old servers).
  readonly server_defaults_blocked?: boolean;
  readonly connections: readonly Connection[];
}

export interface ConnectionResult {
  readonly connection: Connection;
  readonly test: TestOutcome | null;
}

const BASE = "/api/v1/settings/connections";

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
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let response = await fetch(path, { ...init, headers });
  if (response.status === 401 && (await refreshSession())) {
    const retry = getAccessToken();
    if (retry) {
      headers.set("Authorization", `Bearer ${retry}`);
      response = await fetch(path, { ...init, headers });
    }
  }
  if (!response.ok) throw await apiErrorFromResponse(response);
  return response;
}

export async function listConnections(): Promise<ConnectionList> {
  const response = await authenticatedFetch(BASE);
  return (await response.json()) as ConnectionList;
}

export async function saveConnection(
  provider: string,
  payload: SavePayload,
): Promise<ConnectionResult> {
  const response = await authenticatedFetch(`${BASE}/${provider}`, {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(payload),
  });
  return (await response.json()) as ConnectionResult;
}

export async function testConnection(
  provider: string,
): Promise<ConnectionResult> {
  const response = await authenticatedFetch(`${BASE}/${provider}/test`, {
    method: "POST",
  });
  return (await response.json()) as ConnectionResult;
}

export async function disconnectConnection(
  provider: string,
): Promise<Connection> {
  const response = await authenticatedFetch(`${BASE}/${provider}`, {
    method: "DELETE",
  });
  return ((await response.json()) as { connection: Connection }).connection;
}

/** One plain sentence for a failed request (the server's own words when it gave them). */
export function describeConnectionsError(error: unknown): string {
  if (error instanceof ApiError) {
    if (error.serverMessage !== null && error.serverMessage.trim() !== "") {
      return error.serverMessage;
    }
    if (error.status === 401) return "You are signed out. Sign in and retry.";
    if (error.status === 429) {
      return "Too many attempts. Wait a minute and try again.";
    }
    if (error.status >= 500) {
      return "The server could not do that right now. Try again in a moment.";
    }
  }
  return "That could not be done. Try again.";
}
