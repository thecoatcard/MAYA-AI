/**
 * Typed client for the per-user chat settings (GET/PUT /api/v1/settings/chat).
 */

import { apiErrorFromResponse } from "./api-client";
import { getAccessToken, refreshSession } from "./auth-session";

export interface ChatSettings {
  /** The stored preference: detect work requests with an extra model call. */
  readonly intent_classifier: boolean;
  /** False when the deployment master switch is off (the preference is inert). */
  readonly classifier_available: boolean;
  /** Chat may save and recall memories for this user. On unless turned off. */
  readonly memory_in_chat: boolean;
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
    throw await apiErrorFromResponse(response);
  }
  return response;
}

function readSettings(value: unknown): ChatSettings {
  const record = (value ?? {}) as Record<string, unknown>;
  return {
    intent_classifier: record.intent_classifier === true,
    classifier_available: record.classifier_available === true,
    memory_in_chat: record.memory_in_chat !== false,
  };
}

export async function getChatSettings(): Promise<ChatSettings> {
  const response = await authenticatedFetch("/api/v1/settings/chat");
  return readSettings(await response.json());
}

export async function updateChatSettings(payload: {
  intent_classifier?: boolean;
  memory_in_chat?: boolean;
}): Promise<ChatSettings> {
  const response = await authenticatedFetch("/api/v1/settings/chat", {
    method: "PUT",
    headers: { "Content-Type": "application/json" },
    // Only the fields given are sent; the server changes only those.
    body: JSON.stringify(payload),
  });
  return readSettings(await response.json());
}

/* ------------------------------------------------------------ Notion auto-approve (default off) */

export type NotionAutoApproveScope =
  "create_page" | "add_row" | "append_blocks" | "update_row";

export const NOTION_AUTO_APPROVE_SCOPES: readonly NotionAutoApproveScope[] = [
  "create_page",
  "add_row",
  "append_blocks",
  "update_row",
];

export interface NotionAutoApproveSettings {
  readonly enabled: boolean;
  readonly scopes: Readonly<Record<NotionAutoApproveScope, boolean>>;
  readonly version: number;
  readonly hourly_cap: number;
  readonly daily_cap: number;
  readonly used_last_hour: number;
  readonly used_today: number;
  /** Written by the server; these are never auto-approved, whatever the setting says. */
  readonly always_manual: readonly string[];
  readonly last_auto_approved: {
    readonly at: string;
    readonly tool: string | null;
  } | null;
}

function readNotionAutoApprove(value: unknown): NotionAutoApproveSettings {
  const record = (value ?? {}) as Record<string, unknown>;
  const scopes = (record.scopes ?? {}) as Record<string, unknown>;
  const last = record.last_auto_approved as Record<string, unknown> | null;
  const count = (v: unknown): number => (typeof v === "number" ? v : 0);
  return {
    // Anything but an explicit true reads as off, the safe default.
    enabled: record.enabled === true,
    scopes: {
      create_page: scopes.create_page === true,
      add_row: scopes.add_row === true,
      append_blocks: scopes.append_blocks === true,
      update_row: scopes.update_row === true,
    },
    version: count(record.version),
    hourly_cap: count(record.hourly_cap),
    daily_cap: count(record.daily_cap),
    used_last_hour: count(record.used_last_hour),
    used_today: count(record.used_today),
    always_manual: Array.isArray(record.always_manual)
      ? record.always_manual.filter((x): x is string => typeof x === "string")
      : [],
    last_auto_approved:
      last !== null && typeof last === "object" && typeof last.at === "string"
        ? {
            at: last.at,
            tool: typeof last.tool === "string" ? last.tool : null,
          }
        : null,
  };
}

export async function getNotionAutoApprove(): Promise<NotionAutoApproveSettings> {
  const response = await authenticatedFetch(
    "/api/v1/settings/notion-auto-approve",
  );
  return readNotionAutoApprove(await response.json());
}

/**
 * Only called from the Settings page. `confirm` must be true when this turns the master switch
 * on; the page sets it only after the owner confirmed the dialog.
 */
export async function updateNotionAutoApprove(payload: {
  enabled?: boolean;
  scopes?: Partial<Record<NotionAutoApproveScope, boolean>>;
  confirm?: boolean;
}): Promise<NotionAutoApproveSettings> {
  const response = await authenticatedFetch(
    "/api/v1/settings/notion-auto-approve",
    {
      method: "PUT",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify(payload),
    },
  );
  return readNotionAutoApprove(await response.json());
}
