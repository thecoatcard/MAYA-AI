# Voice WebSocket API Specification

> **Endpoint:** `WS /api/v1/voice/live`
> **Protocol:** Bidirectional WebSocket (binary PCM and JSON control frames), **protocol v2**, backward compatible with v1 ([ADR-0015](../adr/0015-voice-protocol-v2.md)).
> **Status:** Backend implemented and tested against a fake Live provider. Behaviour against the real Gemini Live service is **not yet verified** (see section 9).

Voice and text chat are **one conversation**. A voice connection attaches to a chat session (`sessions` row, channel `web`) and every completed turn is one `messages` row, so the same history appears in the text chat and is used to seed the model.

## 1. Connecting and authenticating

The server **accepts the WebSocket first** and then authenticates, so a rejected client receives a real close code instead of an opaque handshake failure.

Token, in order of preference:

1. **First frame `HELLO`** (preferred; keeps the token out of URLs and logs). The client must send it within 5 seconds of connecting when no token was given another way.
2. `?token=<ACCESS_TOKEN>` query parameter (still accepted for v1 clients; it can end up in proxy access logs, so new clients should not use it).
3. `Authorization: Bearer <ACCESS_TOKEN>` header (non-browser clients).

```json
{"type": "HELLO", "protocol": 2, "token": "<access jwt>", "session_id": "<uuid or omitted>", "client": "web"}
```

- `protocol: 2` opts into v2 only frames (see section 6). Without it (or `?protocol=2`) the server behaves as v1 and additionally sends the legacy transcript frames.
- `session_id` (or `?session_id=`) attaches to an existing chat session that the caller owns. Without it a new session is created. **The session id is always reported back in `READY`.**
- A `HELLO` after the first frame is ignored. When a token came from the query string or header, `HELLO` is not required.
- The server never logs the URL, query string or token.

**Origin check.** Browsers always send `Origin`. It is accepted when it is absent (non-browser client), the same origin as the `Host` header, a loopback host (`localhost`, `127.0.0.1`, `[::1]`, any port), or listed in the allow-list (`app/voice/protocol.py: VOICE_ALLOWED_ORIGINS`; a `voice_allowed_origins` setting is read when present). Otherwise close `4403`.

## 2. Close codes

| Code   | Meaning                                                                              |
| :----- | :----------------------------------------------------------------------------------- |
| `1000` | Normal close, including idle timeout and maximum duration (preceded by an `ERROR`).  |
| `1009` | Binary frame larger than 64 KiB.                                                     |
| `4401` | Unauthenticated (bad, expired or missing token, or no `HELLO` in time).              |
| `4403` | Forbidden origin.                                                                    |
| `4404` | `session_id` does not exist or is not the caller's.                                  |
| `4429` | Too many concurrent voice sockets for this user (default 2, per server process).     |
| `4503` | Live unavailable or the live session died (preceded by `LIVE_UNAVAILABLE`/`ERROR`). |

A fatal condition is always announced with an `ERROR` (or `LIVE_UNAVAILABLE`) frame before the close. There is no silent failure.

## 3. Limits

Idle timeout 300 s without any client frame; maximum duration 3600 s; binary frame at most 64 KiB; text frame at most 16 KiB; `USER_TEXT` at most 4000 characters. These are module constants in `app/voice/protocol.py` until a settings change wires them.

## 4. Client to server

| Frame                                | Meaning                                                                                  |
| :----------------------------------- | :--------------------------------------------------------------------------------------- |
| binary                               | PCM16 LE mono 16 kHz, 100 ms (3,200 byte) frames. **Accepted at all times, including while MAYA is speaking**, so the provider's own activity detection can interrupt the model. |
| `{"type":"HELLO",...}`               | See section 1.                                                                           |
| `{"type":"USER_TEXT","text":"..."}`  | A typed turn in the same conversation.                                                   |
| `{"type":"VAD_SPEECH_START"}`        | Client detected speech. Triggers a barge-in if MAYA is speaking.                         |
| `{"type":"VAD_SPEECH_STOP"}`         | Client detected end of speech; the server ends the provider audio stream.                |
| `{"type":"CLIENT_INTERRUPT"}`        | Immediate barge-in. Duplicate interrupts for one barge-in produce a single frame.        |

## 5. Server to client (all versions)

- binary: PCM16 LE mono **24 kHz**.
- `{"type":"AUDIO_INTERRUPT","epoch":1}`: flush playback. `epoch` increases on every barge-in. The server also **drops stale audio** of the interrupted generation until the provider reports a turn boundary, so nothing from the old answer follows this frame.
- `{"type":"TASK_UPDATE_SPOKEN","task_id","milestone","text","catch_up"}`: additive. MAYA just spoke a task update on her own (`milestone`: `completed`, `failed`, `cancelled`, `approval`, `needs_input`) or gave the catch-up for what happened while voice was off (`catch_up: true`, `task_id` null). Clients show `text` as a status entry; it works with an empty chat and clients may ignore it. Turned off by `MAYA_VOICE_TASK_ANNOUNCE=false`.
- `{"type":"MEMORY_SAVED","memory_id","text","updated"}`: additive (Task V). A `save_memory` call stored (or updated) a memory by voice. The web may show a chip with Undo (delete `memory_id` through the Memory API); clients may ignore it. The same entry is stored as `metadata.memories_saved` on the next assistant message, in the chat shape, so the chip also appears after a reload.
- `{"type":"CHAT_MESSAGE","message_id","role":"assistant","content","tool"}`: additive. A voice tool put an assistant message into the chat: the `show_in_chat` tool (code, a list, notes, a table or steps that MAYA composed), `show_code`, or a long read-tool result. The message is already stored under `message_id` (metadata `{"source":"voice_tool","tool":...}`), so the web adds it to the live thread at once as Markdown (fenced code gets the copy button) and dedupes by `message_id` against the reloaded history. Clients may ignore it; the message then appears after the conversation is reopened. `show_in_chat(content, title?)` is declared whenever the conversation can store messages (no connector needed); it rejects empty content, caps the text, and answers `{"ok":false}` when nothing was saved, so MAYA never claims it showed something that is not in the chat.
- `{"type":"TASK_STATUS","task_id","status","intent","tool_name","args","result"}`: a task was accepted by `submit_task` (only when a real task id exists).
- `{"type":"SESSION_RENEWED"}`: the provider session was transparently resumed.
- Legacy (v1 clients only): `{"type":"TRANSCRIPT_USER","text"}` and `{"type":"TRANSCRIPT_MODEL","text"}` fragments.

## 6. Server to client (protocol v2 additions)

```json
{"type":"READY","protocol":2,"session_id":"<uuid>","mode":"live","input":{"rate":16000,"frame_bytes":3200},"output":{"rate":24000},"resumable":true,"resumed":false,"persisted":true,"title":"Voice conversation"}
{"type":"LIVE_UNAVAILABLE","reason":"no_key|connect_failed|quota|disabled","session_id":null}
{"type":"TURN_STATE","turn_id":"<id or null>","state":"listening|thinking|speaking|interrupted"}
{"type":"TRANSCRIPT","turn_id":"<id>","role":"user|assistant","source":"spoken|typed","delta":"text","final":false}
{"type":"TRANSCRIPT","turn_id":"<id>","role":"assistant","source":"spoken","delta":"","final":true,"text":"full turn text"}
{"type":"MESSAGE_PERSISTED","turn_id":"<id>","message_id":"<uuid>","role":"user|assistant"}
{"type":"TASK_EVENT","task_id":"<id>","event_type":"task_plan_created","subtask_id":null,"title":null,"status":null}
{"type":"GO_AWAY","retry_ms":50000,"resumable":true}
{"type":"ERROR","code":"<code>","message":"<safe text>","fatal":false}
```

- `READY` is the first frame after a successful setup. `persisted:false` means the server runs without a database (development); nothing is saved.
- `LIVE_UNAVAILABLE` is followed by close `4503`. There is **no simulator**: without a usable key, with a placeholder key (`example-...`), or when the connection to Gemini fails, the client is told and should use the text chat (`/api/v1/chat`) with the same `session_id` if one was given.
- `TRANSCRIPT`: render all frames with the same `turn_id` in **one** message bubble: append `delta` while `final` is false, then replace with `text` when `final` is true. A user turn becomes final when MAYA starts answering; an assistant turn on `turn_complete` or interruption.
- `MESSAGE_PERSISTED` follows each saved turn. A failed save produces `ERROR{code:"persist_failed",fatal:false}`; the turn is not reported as saved.
- `TURN_STATE` drives the orb: `thinking` after the user finished, `speaking` on first model output, `interrupted` then `listening` on barge-in.
- `TASK_EVENT` is forwarded for tasks submitted on this socket **and** for any task of the same conversation (`Task.session_id`), for example one started from the UI. Only milestones (plan created, step completed or failed, approval needed, task completed or failed) are spoken.
- Error codes: `unauthenticated`, `forbidden_origin`, `session_not_found`, `too_many_sessions`, `persistence_unavailable`, `live_session_error`, `live_session_closed`, `go_away_not_resumable`, `idle_timeout`, `max_duration`, `frame_too_large`, `bad_frame`, `send_failed`, `persist_failed`, `tool_refused`, `tool_failed`, `tasks_unavailable`, `task_submission_failed`, `internal_error`.

## 7. Tools, tasks and approvals (Decisions D3, D5)

**Amended D3 (Task V, owner decision):** the model has `submit_task`, `get_task_status` and, only when the user's memory setting is on, `recall_memory(query)` (read-only) and `save_memory(text, kind?)` (low-risk write, no approval, never credentials, stored with `metadata.source="voice"`). `forget_memory` is not exposed (use the Memory page). Any other tool is rejected. Memory off: the memory tools are not declared and the instruction says memory is off. The toggle is `memory_in_chat`, labelled **"Memory in chat and voice"** in Settings. Voice can never approve: a call to any approval-like or unknown tool is answered to the model as an error response (`approval_prohibited`, `unsupported_tool`) and reported to the client as `ERROR{code:"tool_refused",fatal:false}`; **the session continues**. Approvals stay in the authenticated web UI.

`submit_task` creates the task row and returns immediately with `status:"planning"`; DAG planning and dispatch run in the background. The idempotency key is derived from the provider call id (stable on retry). If the task cannot be created the model receives `{"status":"error",...}` with no task id, and the client gets `ERROR` (`tasks_unavailable` or `task_submission_failed`). A task id is never invented.

Task progress text reaches the model as a user-role turn that starts with `[Automated task update ...]`, with the task text sanitised and delimited so tool output cannot act as an instruction.

## 8. Session resumption

The server asks Gemini for `session_resumption` updates and stores the **real** handle (Redis, 900 s TTL, key `voice:resumption:{workspace}:{session}`; process memory without Redis). On `GoAway` it sends `GO_AWAY` and reconnects to Gemini with the handle, then sends `SESSION_RENEWED`. If no handle exists the session cannot be resumed transparently: the client receives `ERROR{code:"go_away_not_resumable",fatal:true}` and close `4503`, and should reconnect with the same `session_id`, which re-seeds the model with the last 20 turns. Reconnecting a new socket with the same `session_id` also tries the stored handle first. Context window compression is enabled.

## 9. Not verifiable offline (needs the owner's live spike)

Real `receive()` behaviour across turns, `GoAway` timing and handle validity, whether `history_config` seeding and `send_client_content` task updates make the model speak on `gemini-3.8-live`, NON_BLOCKING tool scheduling, barge-in latency, and the quota error mapping. Tests use a fake provider session built from real SDK message types.


## 10. Conversation policy and Live options (Task V)

**Ask first, act second.** The instruction (`app/voice/prompts.py`) tells the model to answer questions and chit-chat itself, and for a deliverable or multi-step job to ask ONE short question when the topic or other essentials are missing, restate the plan in one sentence, and call `submit_task` only after a clear yes (a complete imperative order may start at once). `submit_task(instruction, deliverable)` requires a full self-contained brief; the server rejects an empty brief, one under 3 words, or one that names a deliverable but no subject ("create a ppt") with `{ok:false, status:"needs_clarification", reason, hint, question, missing}` and does not create a task. The task service's own refusals (`needs_clarification`, `unsupported` with `reason`/`hint`) are relayed to the model unchanged plus a "no task was started" message. Typed `USER_TEXT` frames use the same instruction and tools, which is how the policy is tested with fakes. Whether the live model follows the policy needs a live typed-turn check.

**Natural-speech rules** in the instruction: 1 to 2 sentences (3 max), no lists, Markdown or emoji, match the user's language (Hinglish stays Hinglish), spoken numbers, never spell URLs, acknowledge before long actions, stop at once when interrupted, repeat ambiguous names once, be honest about uncertainty.

**Live options** (`app/voice/live_options.py`; SDK-supported, not verified against the live service): automatic activity detection with `silence_duration_ms=800`, `prefix_padding_ms=100`, end-of-speech sensitivity LOW, start-of-speech sensitivity HIGH; `activity_handling=START_OF_ACTIVITY_INTERRUPTS`; input/output transcription; session resumption; sliding-window compression; `speech_config.language_code` only when set (default automatic; not yet wired to a user setting); a female voice, `Leda` by default (`MAYA_VOICE_NAME` may pick another female voice from a fixed list in `live_options.py`; anything else falls back to `Leda`). Not applied: affective dialog, proactive audio, thinking config, explicit VAD signal.
