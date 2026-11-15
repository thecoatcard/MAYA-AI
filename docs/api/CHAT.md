# API: chat

- **Base path:** `/api/v1/chat`
- **Authentication:** `Authorization: Bearer <access token>` on every endpoint (the Phase 02 access token)
- **Status:** Contract approved by the owner on 2026-11-05 (option "Hybrid typed-event SSE", task P04-T004). Implemented locally under the Phase 04 gate exception, with isolated fake-client tests and separate PostgreSQL and Redis integration tests. Live-provider and authenticated-browser verification remain pending.
- **Decision record:** [ADR-0008](../adr/0008-chat-api-contract.md)

Bodies are JSON with unknown fields rejected. Errors before a stream starts are `{"error": "<code>"}`;
validation errors are `{"error": "validation_error", "fields": ["<field>"]}` and never repeat submitted values.
Everything is scoped to the caller's workspace **and** user: another user's session in the same workspace is
indistinguishable from a session that does not exist (`404 session_not_found`).

The owner-approved message, rate, context, concurrency and reply-generation limits are configurable. Deferred
deployment and database decisions are listed under [Open decisions](#open-decisions).

## POST /message

Send one text message and stream the assistant's reply.

| Part                     | Rule                                                                                                                                                                                                                                                                  |
| :----------------------- | :-------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Header `Idempotency-Key` | Required. 8 to 128 characters of `A-Za-z0-9_-`. A client generates one per user message (a UUID is fine).                                                                                                                                                             |
| Header `Accept`          | `text/event-stream`                                                                                                                                                                                                                                                   |
| Body `content`           | Required string, 1 to `MAYA_CHAT_MAX_MESSAGE_CHARS` characters (owner-approved default 8000, configurable up to 16000). Control characters other than newline and tab are rejected, so a carriage return (CRLF text) is a `422`; whitespace-only text is rejected too |
| Body `session_id`        | Optional UUID. Omitted: the server creates a session (title: the first 60 characters of the message, whitespace collapsed) and returns its id in `message.accepted`.                                                                                                  |
| Body `source_message_id` | Optional UUID (additive, message right-click menu). The assistant message of THIS session the user means by "make this a PDF", "save this to Notion as it is" or "email this". It must be a stored assistant message of the caller's own `session_id` (`session_id` is then required): otherwise `422 validation_error` with `fields: ["source_message_id"]`; a foreign or unknown session is `404`. It becomes the content source: the model is told "the user means this message" (and to copy it exactly for "as it is"), the Notion save uses exactly that message (it wins over every other candidate, whatever its length), and an email's body is that text as it is (the subject, when missing, is its first line). The recipient must still be written by the user, no file is made unless a format is named, and approvals are still a click. |
| Body `modality`          | Optional, only `"text"` in Phase 04                                                                                                                                                                                                                                   |

```json
{
  "content": "Summarize the open risks.",
  "session_id": "8f8b80e8-0b53-4f9e-99f5-748a2784cf22"
}
```

Failures before the stream starts (JSON, no events were sent and no model call was made):

| Status | `error`                                      | Meaning                                                                                                                                                                                                                                                |
| :----- | :------------------------------------------- | :----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| 400    | `idempotency_key_required`                   | Missing or malformed `Idempotency-Key`                                                                                                                                                                                                                 |
| 401    | `not_authenticated`, `invalid_token`         | As for every endpoint                                                                                                                                                                                                                                  |
| 404    | `session_not_found`                          | Unknown session, or one owned by another user                                                                                                                                                                                                          |
| 409    | `request_in_progress`                        | The same key is still being processed                                                                                                                                                                                                                  |
| 413    | `payload_too_large`                          | The request body is over 64 KiB. This global limit applies before authentication and bounds the real maximum length: a client that escapes every non-ASCII character in JSON sends 12 bytes per emoji, so about 5,000 of them already exceed it        |
| 422    | `validation_error`, `idempotency_key_reused` | Bad body (an unknown field is reported as `body`, never by its name); a message too large for the context budget (`content`); or the key was used earlier with a different `content` or `session_id`                                                   |
| 429    | `rate_limited`                               | Owner-approved fixed-window limit of 20 message starts per user per minute; `Retry-After` header                                                                                                                                                       |
| 429    | `concurrent_stream_limit`                    | The owner's limit of 2 active replies per user has been reached; `Retry-After: 1`; the idempotency claim is released so the same key can be retried                                                                                                    |
| 503    | `model_unavailable`                          | A new model-backed request cannot start because no Gemini client is configured. An existing key can still be checked for replay.                                                                                                                       |
| 503    | `chat_unavailable`                           | Redis (idempotency cache or the rate limiter) or the database is unreachable. Before the PostgreSQL claim commits, Redis cleanup is best effort. After it commits, the durable claim is retained on failures so a retry cannot invoke the model again. |

Success is `200 text/event-stream; charset=utf-8` with `Cache-Control: no-store`. Each event has a numeric
`id:` (1, 2, 3, ... within this response), an `event:` name and one JSON `data:` line.

| Event              | `data`                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                   |
| :----------------- | :--------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `message.accepted` | `{"session_id", "user_message_id", "assistant_message_id", "replayed": false}`. Sent once, first. The user message is already stored.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                    |
| `delta`            | `{"text": "<next piece of the reply>"}`. Zero or more. Append to the reply.                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                                              |
| `task.created`     | `{"task_id", "title", "status"}`. Sent **at most once** per response, when this message started a background task (the model called `submit_task`, or the optional intent classifier decided the message is a work request). `status` is `executing` for a new task. It can appear anywhere between `message.accepted` and `done`: after the text that announced the task and before the confirmation, or first when the classifier created the task. A replay of the response (same `Idempotency-Key`) sends it once, right after `message.accepted`, from the stored result; no task is created again. The task appears in `GET /api/v1/tasks` and is linked to this chat session. Absent when no task was started.                    |
| `memory.saved`     | `{"memory_id", "text", "updated"}`. Sent once per memory MAYA saved or updated for this message (at most 3), after the tool runs and before the confirmation text and `done`. `memory_id` is what `DELETE /api/v1/memory/items/{id}` takes (the UI's Undo). `updated` is true when a near-identical memory was changed instead of a new one added. A replay sends the stored events right after `message.accepted` (and after `task.created`, if any). Absent when nothing was saved. Clients must ignore it if they do not know it.                                                                                                                                                                                                     |
| `agent.draft`      | `{"name", "description", "path", "content", "capabilities", "instructions"}`. Sent when MAYA drafted a custom agent (the `create_custom_agent` tool, only offered when file features are installed, and answered with `needs_folder` when no working folder is connected). `content` is the exact file that Approve writes through `POST /api/v1/agents/custom` with `confirm: true` ([Agents API](AGENTS.md)); the chat itself writes nothing. Stored with the reply (`metadata.agent_drafts`), so a reload and a replay show the same card. Clients must ignore it if they do not know it.                                                                                                                                             |
| `input.requested`  | `{"ask_id", "questions": [{"question", "header", "options": [{"label", "description"}], "multi_select", "allow_text"}], "source", "state"}`. Sent when MAYA calls the `ask_user` tool (a short question with 1 to 4 questions, 2 to 6 options each or none for a short text answer). The turn then ends normally (`done`, no model continuation): nothing is run and no function response is invented. Stored with the reply (`metadata.input_request`), so a reload and a replay show the same card. `state` is `awaiting_input` on the stream and `answered` or `dismissed` in the message list once a later user message exists (derived, never rewritten). It is **not an approval**. Clients must ignore it if they do not know it. |
| `done`             | `{"assistant_message_id", "finish_reason", "usage": {"prompt_tokens", "candidates_tokens", "total_tokens", "cost_usd", "is_estimate", "cached_tokens"}}`. Last event on success. `cost_usd` is a decimal string, or `null` when the cost is unknown (never `"0"` for an unknown cost). `finish_reason` is `stop`, `max_tokens` or `interrupted`.                                                                                                                                                                                                                                                                                                                                                                                         |
| `error`            | `{"code", "retryable"}`. Last event on failure. `code` is `content_blocked`, `reply_deadline_exceeded`, `stream_interrupted`, `model_unavailable` or `internal_error`. `reply_deadline_exceeded` is stored when the 120-second reply deadline expires before any reply text; after text has arrived, a deadline ends as `stream_interrupted` and the partial reply is stored. `retryable` says whether **asking again** (a new user action with a **new** key) may help; it never means that the same key will call the model again. `content_blocked` and `model_unavailable` are only reported when no text was sent. The text of provider errors is never sent.                                                                       |

`task.created` (above) is the only task event on this stream.

**Memory in chat (ADR-0018, Proposed).** When memory is on for the user (default; see below) the model also has `save_memory(text, kind?)` and `forget_memory(query)`. The real result (`saved`, `updated`, `refused` for credential-looking text, `error`, `skipped` past 3 per reply, and for forget `deleted`, `ambiguous`, `not_found`, `error`) is sent back to the model, so the reply reflects what happened. Before the model call, if the user has any memory, the user message is embedded (`embed` alias) and up to 5 memories with raw similarity of at least 0.65 are added as a labelled, size-bounded (1,500 characters) data block that is the first part of the last user turn; the system instruction, tool declarations and history are unchanged. A retrieval failure or timeout (2.5 seconds) only means the reply is written without memory; recall is skipped for a trivial message (at most 4 words, all greetings or acknowledgements such as "hi", "thanks", "ok", "namaste", and no question mark). With the setting off there is no recall and neither tool is offered, and the system instruction instead states that memory is turned off in Settings, so the model must say it cannot keep anything and never promise to remember. After a save the tool result carries a ready-made `confirmation` ("Saved: <text>") that the model echoes in one short sentence in the user's language.

**Asking the user (`ask_user`).** MAYA calls `ask_user` for a genuinely ambiguous choice, a missing detail it would otherwise invent (a short-text question, no options) or a non-destructive preference; never for permission to write or send (that is the approval card) and not for things already answered or remembered. Arguments are clamped on the server (at most 4 questions, 6 options, 300-character questions, 12-character headers, control characters and secret-shaped text removed) and rendered as plain text by clients. The answer is the next `POST /chat/message` with an additional optional body field `answer: {"ask_id", "answers": [{"selected": ["<option label>"], "text": ""}]}` (one item per question, at most 4; unknown fields are `422`). The server checks it against the stored card, keeps only real option labels (one for single choice), scrubs and caps the text (500 characters), stores the readable text (for example `Language: Java; Theme: glass-aurora`) as the user message with `metadata.answers_to` and `metadata.answers`, and feeds the answer to the model as the function response of the pending call so the original request continues. An answer with nothing chosen or typed is `422` (`fields: ["answer"]`). Any other user message while a card waits dismisses it, and an answer to a card that no longer waits is an ordinary message. The tool is offered with the inline modes (it needs the task submitter wiring, like the other inline tools) and in the live voice model. Whether a real model asks well is model judgment and is not covered by automated tests.

**Task tool.** `submit_task` is described to the model with explicit triggers (pdf, ppt/pptx/deck/slides, docx, xlsx/spreadsheet/excel, report, notes document, multi-step research, and Hinglish "banao" / "bana do") so deliverable requests start a task, while quick questions stay inline. When the task service refuses a request (`ok: false` with `reason` and `hint`) no task is created, no `task.created` event or task metadata is produced, and the refusal is returned to the model as the function response (`{ok:false, status:"refused", reason, hint, message}`) so it tells the user why. A task that was just started is only "started": the model is told never to call it done in the same turn. Ask first (Task V): the prompt and declaration tell the model to ask ONE short question when the topic is missing (for example "create a ppt"), restate the plan and call the tool after the user agrees (a complete order may start at once), never to use a generic instruction, and when the result is `{ok:false, status:"needs_clarification", question, missing}` to ask that question and not retry. Whether a real model follows these instructions is model judgment and is not covered by automated tests.

**Timing.** Every turn logs one `chat_turn_timing` line (outcome, recall outcome, `recall_ms`, `first_chunk_ms`, `first_delta_ms`, `total_ms`, chunks, tools; no user content) and records the Prometheus histogram `maya_chat_turn_phase_seconds{phase,outcome}` (phases recall, first_chunk, first_delta, total). Deltas are forwarded as each provider chunk arrives; a regression test drives the full ASGI app and releases each provider chunk only after the client received the previous delta. Stored assistant messages that saved memories carry `metadata.memories_saved = [{memory_id, text, updated}]`. Clients must ignore event names they do not know.

**Tasks from chat (ADR-0016).** The model has one tool, `submit_task(instruction, title?)`, and is told to use it only for requests that need several steps, tools, files or background work, never for simple questions. At most one task is started per message. After the tool runs the model streams a short confirmation in the same response, so the stored reply holds the text before and after the call (separated by a blank line) and `done.usage` is the sum of both model calls. If the task cannot be created the model is told nothing was started and says so; there is no `task.created` event. The task uses an idempotency key derived from the user message id, so a retried message never creates a second task. Stored assistant messages that started a task carry `metadata.task = {task_id, title, status}`.

**Optional intent classifier (default off).** When `MAYA_CHAT_INTENT_CLASSIFIER=true` **and** the user's `chat_intent_classifier` preference is true, one extra cheap `lite` structured call runs before the reply; a work request is submitted as a task without the model calling the tool, `task.created` is the first event after `message.accepted`, and the reply is a short acknowledgement. With either switch off there is **no** extra model call. A classifier failure never fails the message. The preference is stored per user (migration `0015`, table `user_settings`).

### GET and PUT /api/v1/settings/chat

Authenticated (bearer token), scoped to the caller, rate limited (60 requests per user per minute, `429` with `Retry-After`).

- `GET` returns `200 {"intent_classifier": bool, "classifier_available": bool, "memory_in_chat": bool}` (`memory_in_chat`: default `true`, see Memory in chat above; stored in the same `user_settings` JSON, no migration). `intent_classifier` is the caller's own preference (default `false`); `classifier_available` mirrors the deployment switch `MAYA_CHAT_INTENT_CLASSIFIER`.
- `PUT` takes `{"intent_classifier"?: bool, "memory_in_chat"?: bool}` with at least one field (an empty body or unknown fields are a `422`); only the fields sent change, upserts it (idempotent) and returns the same shape as `GET`. Enabling while the deployment switch is off is stored but has no effect; the response then says `classifier_available: false`.
- Errors: `401 not_authenticated` or `invalid_token`, `422 validation_error`, `429 rate_limited`.

### Idempotency, reconnect and replay

- The first request with a key claims it atomically in Redis (per workspace and user) and records a keyed hash
  (HMAC) of the workspace, user, key, `session_id` and `content`; no message text is kept in Redis. The claim is
  `in_progress` for 15 minutes. Renewal is attempted after chunks are processed, not independently while a
  provider or client is stalled. A successfully written terminal result has a 24-hour expiry. The user message is
  stored **before** any model call.
- While the matching, unexpired Redis record is retained, a repeat with the same key and body does not call the
  model. Existing records can be replayed without a configured provider client, subject to the same rate and
  session checks:
  - after it **completed** (or was interrupted), the stored result is replayed: `message.accepted` with
    `"replayed": true`, one `delta` holding the **complete** stored reply, then `done`. A client that already holds
    partial text for that message must **replace** it, not append. `Last-Event-ID` is accepted and ignored in Phase 04.
  - after it **failed** (`model_unavailable`, `content_blocked`, `reply_deadline_exceeded`, `internal_error`, or a
    disconnect before any text):
    `message.accepted` with `"replayed": true` and the **same** `error` event, while that result is retained.
    A client that wants another attempt after an `error` event must send a **new** key (a second stored user
    message). Reusing the key is for the cases where the client does not know the outcome: the connection dropped,
    the stream ended without `done` or `error`, `409`, `429` (after `Retry-After`) or `503 chat_unavailable`.
  - while the first request is **still running**: `409 request_in_progress`.
- A reply cut short (provider failure after the first chunk, or the client closing the connection) is stored with
  `finish_reason: "interrupted"` and an estimated usage when visible text was received. While the terminal record
  is retained, retrying with the same key replays that partial reply; asking again is a new user action with a new
  key.
- On a client disconnect, the service starts terminal persistence and upstream closure concurrently. Closure is
  best effort and gateway cleanup can delay it. If no visible text arrived, no empty assistant message is stored;
  the request is recorded as failed with `stream_interrupted` if its terminal write succeeds.
- The final write of the result is retried three times. If Redis stays down for all of them, an assistant row may
  already be stored and the service can send the reply, but the key can stay `in_progress` until its 15-minute claim
  expires. Expiry during a stalled stream, Redis data loss, a failed terminal write, or process termination can also
  leave a retry without a matching record. Ambiguous database commits are not durably reconciled and may produce a
  duplicate user message on retry; a lost or expired key can also allow another model call. No crash-safe or
  unconditional no-duplicate guarantee is established.

## GET /sessions

The caller's own sessions: pinned first (most recently pinned first), then newest first.

Query: `limit` (1 to 50, default 20), `cursor` (opaque, from `next_cursor`), `archived` (`false` default: archived sessions hidden; `true`: only archived; `all`).
Every item also carries `pinned` and `archived` (the caller's own flags, `false` when never set).

```json
{
  "items": [
    {
      "id": "...",
      "title": "Summarize the open risks.",
      "channel": "web",
      "status": "active",
      "created_at": "2026-11-05T10:00:00Z",
      "pinned": false,
      "archived": false
    }
  ],
  "next_cursor": null
}
```

## GET /sessions/{session_id}/messages

Messages of one session, oldest first.

Query: `limit` (1 to 100, default 50), `cursor` (opaque, from `next_cursor`).

```json
{
  "items": [
    { "id": "...", "role": "user", "content": "...", "created_at": "..." },
    {
      "id": "...",
      "role": "assistant",
      "content": "...",
      "created_at": "...",
      "finish_reason": "stop",
      "model_id": "gemini-4-hydrogen"
    }
  ],
  "next_cursor": null
}
```

`role` is `user` for stored sender `user` and `assistant` for `orchestrator`. `finish_reason` and `model_id` are always
present and are `null` on user messages (the example above omits them for brevity). Each message also carries the additive
fields `modality` (`text`, `audio` for a spoken voice turn, or `multimodal`; default `text`), and `turn_id` and `source`
(voice turn metadata, `null` on typed messages). Other stored fields (agent names, usage details, other metadata such as
`interrupted`) are not exposed, except `metadata`: `{"task": {task_id, title, status} | null, "memories_saved": [{memory_id, text, updated}], "input_request": {ask_id, questions, source, state} | null, "answer": {ask_id, answers} | null}` (unreadable stored data reads as none). Unknown or foreign session: `404 session_not_found`.

Assistant messages also carry `feedback`: the caller's own thumbs, `"up"`, `"down"` or `null` (additive; see POST /messages/{message_id}/feedback). User messages always read `null`.

Both list endpoints answer with `Cache-Control: no-store`.

## GET /sessions/{session_id}/messages/{message_id}

The state of one assistant reply by id (additive). Returns `{"state": "stored", "message": MessageOut}` once the reply is stored (completed or interrupted; `message.metadata.progress` holds the work log and `finish_reason` tells which) and `{"state": "pending", "message": null}` while it is not stored yet (still running, or an unknown id). A client that left while a reply ran polls this with the `assistant_message_id` from `message.accepted` until it is `stored`. Always `Cache-Control: no-store`; only the caller's own session.

### Seamless continuation and no output limit

A reply is not cut by a stream failure or the output cap. When the model stream dies after some text (503, reset) or sends nothing for 45 s, the server continues the same message on another key or model (up to 3 recoveries): the text so far becomes the previous model turn plus a continue instruction, the continuation is stitched to the same message with a repeated overlap trimmed, usage is summed, and the work log shows a short "Reconnecting" step. A tool call in flight restarts its turn. When a turn ends with `finish_reason=MAX_TOKENS` it is continued the same way (up to 8 times; after that `finish_reason` is `max_tokens`). Only when every recovery fails does the stream end with `error` `stream_interrupted` and the stored reply is `interrupted`, as before. The output budget is the orchestrator model's registered maximum (65,536), never a smaller configured value (`MAYA_CHAT_MAX_OUTPUT_TOKENS` can only raise it).

### Citations

A web-search reply cites as `[n]` only. After the answer the server appends a `**Sources**` list (`n. [title](url) · host`) built from the real search results (cited numbers; all when none is cited). Numbers are global to the reply. The web client repairs stray markup (`"[2]" (www.msn.com)`, `["[4]"](url`) to `[n]` and links `[n]` to the list; an unknown `[n]` stays plain text.

## DELETE /sessions/{session_id}

Delete one session and all its messages.

- **Status code:** `204 No Content` on success.
- **Headers:** `Cache-Control: no-store`
- **Query:** `delete_files_and_tasks=true` (default false) also deletes the session's finished tasks and the files MAYA stored for it; unfinished tasks stay. Without it the tasks and files are kept (they have no foreign key to the session). A copy saved in the user's own working folder is never deleted.
- **Messages:** deleted with the session.
- **Errors:**
  - `401 not_authenticated` or `invalid_token`
  - `404 session_not_found` for an unknown or foreign session

## PATCH /sessions/{session_id}

Rename, pin or archive one of the caller's sessions (right-click menu). Body (at least one field; unknown fields are rejected):

```json
{ "title": "Q3 plan", "pinned": true, "archived": false }
```

`title` is 1 to 120 characters after whitespace is collapsed and trimmed; control characters are rejected. A rename changes the title the list shows and locks it (nothing sets a title after the first message, and nothing may replace a user title). `pinned` and `archived` are the caller's own flags, kept in the per-user settings store (key `chat_session_flags`, at most 5000 ids, pruned when a session is deleted or no longer listed). Answers `200` with the session as the list shows it:

```json
{
  "id": "...",
  "title": "Q3 plan",
  "channel": "web",
  "status": "active",
  "created_at": "2026-11-05T10:00:00Z",
  "pinned": true,
  "archived": false
}
```

Errors: `404 session_not_found` (unknown or another user's id), `422 validation_error` (`fields`), `503 chat_unavailable` (pin and archive need the settings store). While something is pinned or archived the list is paged with a `flags:<n>` cursor instead of the keyset cursor (clients just pass `next_cursor` back).

## POST /sessions/{session_id}/duplicate

Branch a conversation. Optional body `{"from_message_id": "<uuid>"}`: copy the turns up to and including that message (all when omitted; the last 500 at most). Only plain text turns (role and content, in order) are copied: no task cards, approvals, files, progress, usage or other metadata. The new session is titled `<original title> (copy)`. Optional `Idempotency-Key` header (same pattern as POST /message): a repeat with the same key returns the same session instead of making another. Answers `201` with the new session in the shape of PATCH above (`pinned` and `archived` false).

Errors: `404 session_not_found`, `422 validation_error` with `fields: ["from_message_id"]` when that message is not in this session, `400 idempotency_key_required` for a malformed key.

## GET /sessions/{session_id}/export

Query: `format=md` (the only format; anything else is `422`). Answers `200` `text/markdown; charset=utf-8` with `Content-Disposition: attachment; filename="<safe-title>.md"` (ASCII letters, digits, `_` and `-` only; `chat.md` when nothing usable is left), `X-Content-Type-Options: nosniff` and `Cache-Control: no-store`. The body is `# <title>`, an export line, then one `## You - <UTC time>` or `## MAYA - <UTC time>` heading per turn with the stored text (a Sources list stays) and `Attached: <name>` lines for attachments and created files. Progress steps, tool calls, task cards, usage and other metadata are left out. `404 session_not_found` for another user's session.

## POST /messages/{message_id}/feedback

Body `{"value": "up" | "down" | null, "note": "<=500 chars"}` (`value` is required; `null` clears the thumb). Answers `204`. Only the caller's own assistant messages: anything else is `404 {"error": "message_not_found"}`; a bad body is `422`. Stored per user (settings key `message_feedback`, newest 2000 kept): the message id, session id, value, time, note and the model id when known, never the message text. It is kept for later calibration and is never put into a prompt. `GET .../messages` and `GET .../messages/{message_id}` return the caller's value as `feedback`.

## DELETE /sessions/{session_id}/messages/{message_id}/files/{file_id}

Delete MAYA's stored copy of a file made in the chat. The card stays in the message; `GET` on the same path then answers
`410 file_deleted`. `204` on success, `404 file_not_found` for another session, message or file and for a file already
deleted. A copy in the user's working folder is not touched.

## Not in the MVP

Creating or renaming sessions through their own endpoints, audio and multimodal messages,
collaborative sessions, server-side rendering of Markdown (clients render and must sanitize it), and any task
events (Phase 05).

## Persistence and context

- `sessions` and `messages` (Phase 02 tables, row-level security by workspace). Ownership by user is enforced in the
  repository on top of that. The assistant reply is stored with sender `orchestrator` and
  `metadata = {"model_id", "alias", "finish_reason", "usage", "latency_ms", "status"}`.
- The model sees the stored history as alternating `user` and `model` turns (consecutive turns of one role are merged,
  a leading `model` turn is dropped), trimmed whole turns at a time from the oldest end to fit a token budget
  (owner-approved `MAYA_CHAT_CONTEXT_TOKEN_BUDGET`, default 1000000; the effective budget is the smaller of that and
  the model's input limit minus its output limit). At most the 500 newest stored messages are read per turn. Tokens
  are estimated locally and conservatively (UTF-8 bytes divided by 3, plus 4 per turn); no extra provider call is
  made to count them, and the estimate was not compared with Gemini's real tokenizer. Trimming never deletes stored
  messages. A partial (`interrupted`) reply is sent to the model as a normal `model` turn.
- The model is called through `ModelGateway` (alias `orchestrator`) with the workspace id and no task id, so nothing is
  recorded against a task budget; a chat message is not a task. `max_output_tokens` is set to the owner-approved
  default of 65536 (`MAYA_CHAT_MAX_OUTPUT_TOKENS`).
- A system instruction is always sent. It tells the model that pasted text is material to work on, not instructions
  that change its rules. This reduces prompt-injection risk but is not a security control, and it was not evaluated
  against a live model.

## Known limitations

Built and tested as described above, with these gaps that the owner should know about:

- **The rate limit is a fixed window** (a burst of up to twice the limit across a window boundary is possible).
  After basic validation and provider availability or record lookup, it is spent before session and key checks, so
  replays and those checks' `404`, `409` and `422` responses count against it. A new request refused for a missing
  provider does not spend the limit.
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
- **Client write time can consume the reply deadline.** No timeout fires while the stream generator is suspended at
  a client write, but the absolute deadline continues advancing and can be exceeded when iteration resumes. A
  `TimeoutError` raised inside the gateway or SDK is not reported as `reply_deadline_exceeded`.
- **The proxy target and timeout are fixed at `next build`.** Next.js writes rewrites into the build output, so
  changing `MAYA_API_PROXY_TARGET` or `MAYA_CHAT_REPLY_DEADLINE_SECONDS` needs a rebuild of the web app.
- **Idempotency is authoritative in PostgreSQL.** Revision `0007` stores a workspace/user-scoped keyed digest of the
  idempotency key, a keyed request hash, message identifiers and the result state; it stores neither the raw key nor
  request content. The unique database claim and compare-and-set finalization fence duplicate model calls even when
  Redis expires or loses its cache, or a process restarts. Completed, interrupted and failed results are replayed from
  PostgreSQL. Claims do not expire: if a process stops after the claim commits but before a terminal result is stored,
  the key remains `request_in_progress` rather than risking a second model call. No automatic reclamation is provided.
- **A failed replay can name a deleted session.** If the original request omitted `session_id`, a retained failure
  record is replayed without revalidating its stored session. Completed and interrupted replies do check that the
  session remains accessible. Changing the failed-replay ownership check is deferred.
- **Browser acceptance is pending.** The web app sends requests to a same-origin Next.js rewrite configured by the
  server-only `MAYA_API_PROXY_TARGET`, avoiding cross-origin preflight and `Retry-After` exposure requirements. The
  authenticated browser flow has not been exercised against a running API; no CORS policy is configured.
- **Per-address authentication limits collapse behind the same-origin proxy.** The API sees the proxy peer for every
  forwarded request. It ignores client-supplied `X-Forwarded-For` because Next.js preserves that header, so trusting it
  would be spoofable. This is deferred until the hosting target is chosen; only a front reverse proxy that overwrites
  `X-Forwarded-For` can later be the trusted address source. No behavior changes before that decision.

## Open decisions

The following items remain deferred or are gated:

| Item                     | Status                 | Why open                                                                                                                                 |
| :----------------------- | :--------------------- | :--------------------------------------------------------------------------------------------------------------------------------------- |
| Content-Security-Policy  | Deferred               | The hosting/runtime target must be confirmed before selecting a policy                                                                   |
| History and list indexes | Deferred               | Migration requires query-volume evidence and an approved rollback plan                                                                   |
| Browser acceptance       | Pending                | Requires an owner-run live Gemini check and browser evidence for sign-in, send, streamed reply and reconnect without a second model call |
| Playwright package       | Approved but not added | The task's hard limit prohibits changing package manifests; available browser tooling is used without a new dependency                   |
