/**
 * Typed client for the agents API (`/api/v1/agents`, ADR-0023): the three built-in agents and
 * the custom agents stored as files in the connected folder. Same auth handling as the other
 * clients: bearer token, one refresh and retry on 401, and `ApiError` for every non-2xx answer.
 * Nothing is written until the caller sends `confirm: true` for an agent it has previewed.
 */

import { ApiError, NetworkError, apiErrorFromResponse } from "./api-client";
import { getAccessToken, refreshSession } from "./auth-session";

const BASE = "/api/v1/agents";

export interface BuiltinAgent {
  readonly id: string;
  readonly label: string;
  readonly summary: string;
  readonly capabilities: readonly string[];
  readonly available: boolean;
}

export interface CustomAgent {
  readonly name: string;
  readonly file: string;
  readonly valid: boolean;
  readonly enabled: boolean;
  readonly description: string;
  readonly instructions: string;
  readonly capabilities: readonly string[];
  readonly model: string | null;
  readonly error: string | null;
}

export interface CapabilityInfo {
  readonly tools: readonly string[];
  readonly allowed: boolean;
}

export interface AgentsOverview {
  readonly agents: readonly BuiltinAgent[];
  readonly custom: readonly CustomAgent[];
  readonly folderConnected: boolean;
  readonly capabilities: Readonly<Record<string, CapabilityInfo>>;
}

export interface AgentDraftInput {
  readonly name: string;
  readonly description: string;
  readonly instructions: string;
  readonly capabilities: readonly string[];
}

export interface AgentPreview {
  readonly path: string;
  readonly content: string;
  readonly agent: CustomAgent;
}

export function describeAgentsError(error: unknown): string {
  if (error instanceof ApiError) {
    switch (error.status) {
      case 401:
        return "You are signed out. Sign in again and retry.";
      case 404:
        return "That agent no longer exists.";
      case 409:
        return error.serverMessage !== null && error.serverMessage.trim() !== ""
          ? error.serverMessage
          : "Connect a working folder first (Settings, Files).";
      case 422:
        return error.serverMessage !== null && error.serverMessage.trim() !== ""
          ? error.serverMessage
          : "That was not accepted. Check it and try again.";
      case 429:
        return "Too many requests. Wait a moment and try again.";
      default:
        if (error.status >= 500) {
          return "The server could not do that right now. Try again in a moment.";
        }
        return "That could not be done. Try again.";
    }
  }
  if (error instanceof NetworkError) {
    return "The connection was lost. Check your network and try again.";
  }
  return "That could not be done. Try again.";
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
  if (token) headers.set("Authorization", `Bearer ${token}`);
  let response: Response;
  try {
    response = await fetch(path, { ...init, headers, cache: "no-store" });
    if (response.status === 401) {
      const refreshed = await refreshSession();
      const retryToken = refreshed ? getAccessToken() : null;
      if (retryToken) {
        headers.set("Authorization", `Bearer ${retryToken}`);
        response = await fetch(path, { ...init, headers, cache: "no-store" });
      }
    }
  } catch (error) {
    throw new NetworkError(error);
  }
  if (!response.ok) throw await apiErrorFromResponse(response);
  return response;
}

function isRecord(value: unknown): value is Record<string, unknown> {
  return typeof value === "object" && value !== null && !Array.isArray(value);
}

function strings(value: unknown): string[] {
  return Array.isArray(value)
    ? value.filter((v): v is string => typeof v === "string")
    : [];
}

function parseCustom(value: unknown): CustomAgent {
  if (!isRecord(value) || typeof value.name !== "string") {
    throw new ApiError(200, "unexpected_response");
  }
  return {
    name: value.name,
    file: typeof value.file === "string" ? value.file : "",
    valid: value.valid !== false,
    enabled: value.enabled === true,
    description: typeof value.description === "string" ? value.description : "",
    instructions:
      typeof value.instructions === "string" ? value.instructions : "",
    capabilities: strings(value.capabilities),
    model: typeof value.model === "string" ? value.model : null,
    error: typeof value.error === "string" ? value.error : null,
  };
}

function parseOverview(value: unknown): AgentsOverview {
  if (!isRecord(value) || !Array.isArray(value.agents)) {
    throw new ApiError(200, "unexpected_response");
  }
  const agents: BuiltinAgent[] = value.agents.map((raw: unknown) => {
    if (!isRecord(raw) || typeof raw.id !== "string") {
      throw new ApiError(200, "unexpected_response");
    }
    return {
      id: raw.id,
      label: typeof raw.label === "string" ? raw.label : raw.id,
      summary: typeof raw.summary === "string" ? raw.summary : "",
      capabilities: strings(raw.capabilities),
      available: raw.available !== false,
    };
  });
  const capabilities: Record<string, CapabilityInfo> = {};
  if (isRecord(value.capabilities)) {
    for (const [name, info] of Object.entries(value.capabilities)) {
      if (isRecord(info)) {
        capabilities[name] = {
          tools: strings(info.tools),
          allowed: info.allowed === true,
        };
      }
    }
  }
  return {
    agents,
    custom: Array.isArray(value.custom) ? value.custom.map(parseCustom) : [],
    folderConnected: value.folder_connected === true,
    capabilities,
  };
}

function parsePreview(value: unknown): AgentPreview {
  if (
    !isRecord(value) ||
    typeof value.path !== "string" ||
    typeof value.content !== "string"
  ) {
    throw new ApiError(200, "unexpected_response");
  }
  return {
    path: value.path,
    content: value.content,
    agent: parseCustom(value.agent),
  };
}

async function json(response: Response): Promise<unknown> {
  try {
    return await response.json();
  } catch {
    throw new ApiError(response.status, "unexpected_response");
  }
}

const JSON_HEADERS = {
  Accept: "application/json",
  "Content-Type": "application/json",
};

function body(input: AgentDraftInput, confirm: boolean): string {
  return JSON.stringify({
    name: input.name,
    description: input.description,
    instructions: input.instructions,
    capabilities: input.capabilities,
    confirm,
  });
}

export async function getAgents(): Promise<AgentsOverview> {
  const response = await authenticatedFetch(BASE, {
    headers: { Accept: "application/json" },
  });
  return parseOverview(await json(response));
}

/** The exact file that would be written. Nothing is written. */
export async function previewAgent(
  input: AgentDraftInput,
): Promise<AgentPreview> {
  const response = await authenticatedFetch(`${BASE}/custom/preview`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: body(input, false),
  });
  return parsePreview(await json(response));
}

/** Ask MAYA to draft an agent from a sentence. The draft is only a preview. */
export async function draftAgent(request: string): Promise<AgentPreview> {
  const response = await authenticatedFetch(`${BASE}/custom/draft`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: JSON.stringify({ request }),
  });
  return parsePreview(await json(response));
}

/** Write a NEW agent file (the user confirmed the preview). */
export async function createAgent(input: AgentDraftInput): Promise<void> {
  await authenticatedFetch(`${BASE}/custom`, {
    method: "POST",
    headers: JSON_HEADERS,
    body: body(input, true),
  });
}

/** Save an edit to an existing agent (the previous text is kept as .bak). */
export async function updateAgent(input: AgentDraftInput): Promise<void> {
  await authenticatedFetch(`${BASE}/custom/${encodeURIComponent(input.name)}`, {
    method: "PUT",
    headers: JSON_HEADERS,
    body: body(input, true),
  });
}

export async function setAgentEnabled(
  name: string,
  enabled: boolean,
): Promise<void> {
  await authenticatedFetch(
    `${BASE}/custom/${encodeURIComponent(name)}/enabled`,
    {
      method: "POST",
      headers: JSON_HEADERS,
      body: JSON.stringify({ enabled }),
    },
  );
}
