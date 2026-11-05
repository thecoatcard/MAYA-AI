# API: chat

- **Base path:** `/api/v1/chat`
- **Authentication:** `Authorization: Bearer <access token>` on every endpoint (the Phase 02 access token)
- **Status:** Contract approved by the owner on 2026-11-05 (option "Hybrid typed-event SSE", task P04-T004); implementation in progress under the Phase 04 gate exception. Browser UI: planned, and no login UI exists yet.
- **Decision record:** [ADR-0008](../adr/0008-chat-api-contract.md)

Bodies are JSON with unknown fields rejected. Errors before a stream starts are `{"error": "<code>"}`;
validation errors are `{"error": "validation_error", "fields": ["<field>"]}` and never repeat submitted values.
Everything is scoped to the caller's workspace **and** user: another user's session in the same workspace is
indistinguishable from a session that does not exist (`404 session_not_found`).

Values marked **provisional** are not owner-approved and are configurable; they are listed under
[Open decisions](#open-decisions).

## POST /message

Send one text message and stream the assistant's reply.

| Part | Rule |
| :--- | :--- |
| Header `Idempotency-Key` | Required. 8 to 128 characters of `A-Za-z0-9_-`. A client generates one per user message (a UUID is fine). |
| Header `Accept` | `text/event-stream` |
| Body `content` | Required string, 1 to `MAYA_CHAT_MAX_MESSAGE_CHARS` characters (**provisional** default 8000), control characters other than newline and tab rejected |
| Body `session_id` | Optional UUID. Omitted: the server creates a session (title: the first 60 characters of the message, whitespace collapsed) and returns its id in `message.accepted`. |
| Body `modality` | Optional, only `"text"` in Phase 04 |

```json
{ "content": "Summarize the open risks.", "session_id": "8f8b80e8-0b53-4f9e-99f5-748a2784cf22" }
```

Failures before the stream starts (JSON, no events were sent and no model call was made):

| Status | `error` | Meaning |
| :--- | :--- | :--- |
| 400 | `idempotency_key_required` | Missing or malformed `Idempotency-Key` |
| 401 | `not_authenticated`, `invalid_token` | As for every endpoint |
| 404 | `session_not_found` | Unknown session, or one owned by another user |
| 409 | `request_in_progress` | The same key is still being processed |
| 422 | `validation_error`, `idempotency_key_reused` | Bad body; or the key was used earlier with a different `content` or `session_id` |
| 429 | `rate_limited` | Per-user limit (**provisional**); `Retry-After` header |
| 503 | `model_unavailable` | No Gemini key is configured |
| 503 | `chat_unavailable` | Redis (idempotency) is unreachable. Chat fails closed because it could not promise "no duplicate billing" |

Success is `200 text/event-stream; charset=utf-8` with `Cache-Control: no-store`. Each event has a numeric
`id:` (1, 2, 3, ... within this response), an `event:` name and one JSON `data:` line.

| Event | `data` |
| :--- | :--- |
| `message.accepted` | `{"session_id", "user_message_id", "assistant_message_id", "replayed": false}`. Sent once, first. The user message is already stored. |
| `delta` | `{"text": "<next piece of the reply>"}`. Zero or more. Append to the reply. |
| `done` | `{"assistant_message_id", "finish_reason", "usage": {"prompt_tokens", "candidates_tokens", "total_tokens", "cost_usd", "is_estimate"}}`. Last event on success. `cost_usd` is a decimal string, or `null` when the cost is unknown (never `"0"` for an unknown cost). `finish_reason` is `stop`, `max_tokens` or `interrupted`. |
| `error` | `{"code", "retryable"}`. Last event on failure. `code` is `content_blocked`, `stream_interrupted`, `model_unavailable` or `internal_error`. The text of provider errors is never sent. |

Phase 05 adds `task.*` events to the same stream. Clients must ignore event names they do not know.

### Idempotency, reconnect and replay

- The first request with a key claims it atomically in Redis (24 hours, per workspace and user) and records a hash of
  `session_id` and `content`. The user message is stored **before** any model call.
- A repeated request with the same key and the same body never calls the model again:
  - after it **completed** (or was interrupted), the stored result is replayed: `message.accepted` with
    `"replayed": true`, one `delta` holding the **complete** stored reply, then `done`. A client that already holds
    partial text for that message must **replace** it, not append. `Last-Event-ID` is accepted and ignored in Phase 04.
  - while the first request is **still running**: `409 request_in_progress`.
- A reply cut short (provider failure after the first chunk, or the client closing the connection) is stored with
  `finish_reason: "interrupted"` and an estimated usage. Retrying with the same key replays that partial reply;
  asking again is a new user action with a new key. Nothing is silently re-billed.
- A client disconnect cancels the upstream Gemini stream at once.

## GET /sessions

The caller's own sessions, newest first.

Query: `limit` (1 to 50, default 20), `cursor` (opaque, from `next_cursor`).

```json
{ "items": [ { "id": "...", "title": "Summarize the open risks.", "channel": "web", "status": "active", "created_at": "2026-11-05T10:00:00Z" } ], "next_cursor": null }
```

## GET /sessions/{session_id}/messages

Messages of one session, oldest first.

Query: `limit` (1 to 100, default 50), `cursor` (opaque, from `next_cursor`).

```json
{ "items": [ { "id": "...", "role": "user", "content": "...", "created_at": "..." },
             { "id": "...", "role": "assistant", "content": "...", "created_at": "...", "finish_reason": "stop", "model_id": "gemini-3.8-flash" } ],
  "next_cursor": null }
```

`role` is `user` for stored sender `user` and `assistant` for `orchestrator`. Other stored fields (agent names, usage
details, metadata) are not exposed in Phase 04. Unknown or foreign session: `404 session_not_found`.

## Not in the MVP

Creating, renaming and deleting sessions through their own endpoints, audio and multimodal messages,
collaborative sessions, server-side rendering of Markdown (clients render and must sanitize it), and any task
events (Phase 05).

## Persistence and context

- `sessions` and `messages` (Phase 02 tables, row-level security by workspace). Ownership by user is enforced in the
  repository on top of that. The assistant reply is stored with sender `orchestrator` and
  `metadata = {"model_id", "alias", "finish_reason", "usage", "latency_ms", "status"}`.
- The model sees the stored history as alternating `user` and `model` turns, trimmed from the oldest end to fit a
  token budget (**provisional**: `MAYA_CHAT_CONTEXT_TOKEN_BUDGET`, default 32000, always below the model's context
  limit). Tokens are estimated locally (conservatively); no extra provider call is made to count them. Trimming never
  deletes stored messages.
- The model is called through `ModelGateway` (alias `orchestrator`) with the workspace id and no task id, so nothing is
  recorded against a task budget; a chat message is not a task.

## Open decisions

Not approved by the owner; configurable, with the provisional defaults above:

| Setting | Provisional default | Why open |
| :--- | :--- | :--- |
| `MAYA_CHAT_MAX_MESSAGE_CHARS` | 8000 | The phase requires an owner-approved input limit |
| `MAYA_CHAT_RATE_LIMIT_PER_MINUTE` | 20 per user | The phase requires limits chosen from a measured baseline; none exists because no live call has run |
| `MAYA_CHAT_CONTEXT_TOKEN_BUDGET` | 32000 tokens | The phase requires an approved token budget; no turn count is assumed |
