# API: chat

- **Base path:** `/api/v1/chat`
- **Authentication:** `Authorization: Bearer <access token>` on every endpoint (the Phase 02 access token)
- **Status:** Contract approved by the owner on 2026-11-05 (option "Hybrid typed-event SSE", task P04-T004). Implemented locally under the Phase 04 gate exception and tested with fake Gemini clients, real PostgreSQL and real Redis; it has never run against the real Gemini API. The web app has a minimal login UI and a same-origin Next.js API proxy, but the authenticated browser flow has not been accepted end to end.
- **Decision record:** [ADR-0008](../adr/0008-chat-api-contract.md)

Bodies are JSON with unknown fields rejected. Errors before a stream starts are `{"error": "<code>"}`;
validation errors are `{"error": "validation_error", "fields": ["<field>"]}` and never repeat submitted values.
Everything is scoped to the caller's workspace **and** user: another user's session in the same workspace is
indistinguishable from a session that does not exist (`404 session_not_found`).

The owner-approved message, rate, context, concurrency and reply-generation limits are configurable. Deferred
deployment and database decisions are listed under [Open decisions](#open-decisions).

## POST /message

Send one text message and stream the assistant's reply.

| Part | Rule |
| :--- | :--- |
| Header `Idempotency-Key` | Required. 8 to 128 characters of `A-Za-z0-9_-`. A client generates one per user message (a UUID is fine). |
| Header `Accept` | `text/event-stream` |
| Body `content` | Required string, 1 to `MAYA_CHAT_MAX_MESSAGE_CHARS` characters (owner-approved default 8000, configurable up to 16000). Control characters other than newline and tab are rejected, so a carriage return (CRLF text) is a `422`; whitespace-only text is rejected too |
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
| 413 | `payload_too_large` | The request body is over 64 KiB. This global limit applies before authentication and bounds the real maximum length: a client that escapes every non-ASCII character in JSON sends 12 bytes per emoji, so about 5,000 of them already exceed it |
| 422 | `validation_error`, `idempotency_key_reused` | Bad body (an unknown field is reported as `body`, never by its name); a message too large for the context budget (`content`); or the key was used earlier with a different `content` or `session_id` |
| 429 | `rate_limited` | Owner-approved fixed-window limit of 20 message starts per user per minute; `Retry-After` header |
| 429 | `concurrent_stream_limit` | The owner's limit of 2 active replies per user has been reached; `Retry-After: 1`; the idempotency claim is released so the same key can be retried |
| 503 | `model_unavailable` | No Gemini key is configured |
| 503 | `chat_unavailable` | Redis (idempotency or the rate limiter) or the database is unreachable. Chat fails closed because it could not promise "no duplicate billing". The key is released, so the same key may be sent again |

Success is `200 text/event-stream; charset=utf-8` with `Cache-Control: no-store`. Each event has a numeric
`id:` (1, 2, 3, ... within this response), an `event:` name and one JSON `data:` line.

| Event | `data` |
| :--- | :--- |
| `message.accepted` | `{"session_id", "user_message_id", "assistant_message_id", "replayed": false}`. Sent once, first. The user message is already stored. |
| `delta` | `{"text": "<next piece of the reply>"}`. Zero or more. Append to the reply. |
| `done` | `{"assistant_message_id", "finish_reason", "usage": {"prompt_tokens", "candidates_tokens", "total_tokens", "cost_usd", "is_estimate"}}`. Last event on success. `cost_usd` is a decimal string, or `null` when the cost is unknown (never `"0"` for an unknown cost). `finish_reason` is `stop`, `max_tokens` or `interrupted`. |
| `error` | `{"code", "retryable"}`. Last event on failure. `code` is `content_blocked`, `reply_deadline_exceeded`, `stream_interrupted`, `model_unavailable` or `internal_error`. `reply_deadline_exceeded` is stored when the 120-second reply deadline expires before any reply text; after text has arrived, a deadline ends as `stream_interrupted` and the partial reply is stored. `retryable` says whether **asking again** (a new user action with a **new** key) may help; it never means that the same key will call the model again. `content_blocked` and `model_unavailable` are only reported when no text was sent. The text of provider errors is never sent. |

Phase 05 adds `task.*` events to the same stream. Clients must ignore event names they do not know.

### Idempotency, reconnect and replay

- The first request with a key claims it atomically in Redis (per workspace and user) and records a keyed hash
  (HMAC) of the workspace, user, key, `session_id` and `content`; no message text is kept in Redis. The claim is
  `in_progress` for 15 minutes and is renewed while chunks keep arriving; once the request ends, the result is kept
  for 24 hours. The user message is stored **before** any model call.
- A repeated request with the same key and the same body never calls the model again:
  - after it **completed** (or was interrupted), the stored result is replayed: `message.accepted` with
    `"replayed": true`, one `delta` holding the **complete** stored reply, then `done`. A client that already holds
    partial text for that message must **replace** it, not append. `Last-Event-ID` is accepted and ignored in Phase 04.
  - after it **failed** (`model_unavailable`, `content_blocked`, `reply_deadline_exceeded`, `internal_error`, or a
    disconnect before any text):
    `message.accepted` with `"replayed": true` and the **same** `error` event, for 24 hours, with no model call.
    A client that wants another attempt after an `error` event must send a **new** key (a second stored user
    message). Reusing the key is for the cases where the client does not know the outcome: the connection dropped,
    the stream ended without `done` or `error`, `409`, `429` (after `Retry-After`) or `503 chat_unavailable`.
  - while the first request is **still running**: `409 request_in_progress`.
- A reply cut short (provider failure after the first chunk, or the client closing the connection) is stored with
  `finish_reason: "interrupted"` and an estimated usage. Retrying with the same key replays that partial reply;
  asking again is a new user action with a new key. Nothing is silently re-billed.
- A client disconnect cancels the upstream Gemini stream at once. If it happens before any text arrived, no
  (empty) assistant message is stored and the request is recorded as failed with `stream_interrupted`.
- The final write of the result is retried three times. If Redis stays down for all of them, the reply is still stored
  and delivered, but the key stays `in_progress` until its 15-minute claim expires; after that the same key would be
  treated as new. This double fault is the one place where "no duplicate billing" is not guaranteed.

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

`role` is `user` for stored sender `user` and `assistant` for `orchestrator`. `finish_reason` and `model_id` are always
present and are `null` on user messages (the example above omits them for brevity). Other stored fields (agent names,
usage details, metadata) are not exposed in Phase 04. Unknown or foreign session: `404 session_not_found`.

Both list endpoints answer with `Cache-Control: no-store`.

## Not in the MVP

Creating, renaming and deleting sessions through their own endpoints, audio and multimodal messages,
collaborative sessions, server-side rendering of Markdown (clients render and must sanitize it), and any task
events (Phase 05).

## Persistence and context

- `sessions` and `messages` (Phase 02 tables, row-level security by workspace). Ownership by user is enforced in the
  repository on top of that. The assistant reply is stored with sender `orchestrator` and
  `metadata = {"model_id", "alias", "finish_reason", "usage", "latency_ms", "status"}`.
- The model sees the stored history as alternating `user` and `model` turns (consecutive turns of one role are merged,
  a leading `model` turn is dropped), trimmed whole turns at a time from the oldest end to fit a token budget
  (owner-approved `MAYA_CHAT_CONTEXT_TOKEN_BUDGET`, default 32000; the effective budget is the smaller of that and
  the model's input limit minus its output limit). At most the 500 newest stored messages are read per turn. Tokens
  are estimated locally and conservatively (UTF-8 bytes divided by 3, plus 4 per turn); no extra provider call is
  made to count them, and the estimate was not compared with Gemini's real tokenizer. Trimming never deletes stored
  messages. A partial (`interrupted`) reply is sent to the model as a normal `model` turn.
- The model is called through `ModelGateway` (alias `orchestrator`) with the workspace id and no task id, so nothing is
  recorded against a task budget; a chat message is not a task. `max_output_tokens` is set to the owner-approved
  default of 2048 (`MAYA_CHAT_MAX_OUTPUT_TOKENS`).
- A system instruction is always sent. It tells the model that pasted text is material to work on, not instructions
  that change its rules. This reduces prompt-injection risk but is not a security control, and it was not evaluated
  against a live model.

## Known limitations

Built and tested as described above, with these gaps that the owner should know about:

- **The rate limit is a fixed window** (a burst of up to twice the limit across a window boundary is possible) and
  it is spent before the session and key checks, so replays, `404`, `409` and `422` requests count against it.
- **Token estimation is local and conservative.** The context budget is approved and enforced, but its UTF-8-byte
  estimate (bytes divided by 3 plus 4 per turn) has not been compared with Gemini's real tokenizer.
- **No database index** exists for the history and list queries (a migration is Level 3 and was not requested). It
  does not change results; it matters once histories are large.
- **Streaming behavior is assumed from the SDK code, not observed:** nothing here ran against the real Gemini API, and
  only uvicorn with h11 on loopback was exercised for real disconnects (not httptools or HTTP/2). The web app's
  Next.js rewrite proxy sits in the browser path; it has not been exercised with a real stream. Two of its defaults
  are overridden because they would break streaming: the stream answers `Cache-Control: no-store, no-transform` so
  the proxy's gzip compression leaves events unbuffered, and the proxy timeout (30 s by default) is set to the reply
  deadline plus 30 seconds (`MAYA_CHAT_REPLY_DEADLINE_SECONDS`, read by both the API and `next.config.ts`).
- **The reply deadline counts only time spent waiting for the model**, measured from the start of the stream. Time
  the server spends writing to a slow client does not trigger it. A `TimeoutError` raised inside the gateway or SDK is
  not reported as `reply_deadline_exceeded`.
- **The proxy target and timeout are fixed at `next build`.** Next.js writes rewrites into the build output, so
  changing `MAYA_API_PROXY_TARGET` or `MAYA_CHAT_REPLY_DEADLINE_SECONDS` needs a rebuild of the web app.
- **Idempotency lives in Redis for 24 hours.** After that, or if Redis loses the key, the same key counts as new.
- **Browser acceptance is pending.** The web app sends requests to a same-origin Next.js rewrite configured by the
  server-only `MAYA_API_PROXY_TARGET`, avoiding cross-origin preflight and `Retry-After` exposure requirements. The
  authenticated browser flow has not been exercised against a running API; no CORS policy is configured.
- **Per-address authentication limits collapse behind the same-origin proxy.** The API sees the proxy peer for every
  forwarded request. It ignores client-supplied `X-Forwarded-For` because Next.js preserves that header, so trusting it
  would be spoofable. This is deferred until the hosting target is chosen; only a front reverse proxy that overwrites
  `X-Forwarded-For` can later be the trusted address source. No behavior changes before that decision.

## Open decisions

The following items remain deferred or are gated:

| Item | Status | Why open |
| :--- | :--- | :--- |
| Content-Security-Policy | Deferred | The hosting/runtime target must be confirmed before selecting a policy |
| History and list indexes | Deferred | Migration requires query-volume evidence and an approved rollback plan |
| Browser acceptance | Pending | Requires an owner-run live Gemini check and browser evidence for sign-in, send, streamed reply and reconnect without a second model call |
| Playwright package | Approved but not added | The task's hard limit prohibits changing package manifests; available browser tooling is used without a new dependency |
