# Voice Subsystem (`app.voice`)

The Voice Subsystem provides real-time bidirectional streaming voice capabilities connecting the web client to the Gemini Live API (`gemini-3.8-live`), adhering to Decisions D3, D5, and D12 in `MAYA_AI_FINAL_PLAN.md`.

## Modules

- `audio_converter.py`: PCM16 format conversions, byte-order handling, chunking (100 ms / 1600 samples at 16 kHz), and linear interpolation resampling between 16 kHz and 24 kHz.
- `vad.py`: Voice Activity Detection (VAD) using normalized Root Mean Square (RMS) and energy calculations with hangover smoothing to prevent clipped speech.
- `tools.py`: `VoiceToolRegistry` declaring `submit_task`, `get_task_status` and, when memory is on, `recall_memory` and `save_memory` (amended D3). Validates the task brief (ask-first). Enforces Decision D3 (Zero MCP tools in voice context) and Decision D5 (Voice approval shielding; all approvals must be confirmed in the secure web interface).
- `web_voice.py`: the live `web_search` tool (read-only). It calls the same `app/search` `WebSearchService` as chat, always at `quick` depth within a 14 s budget, and returns at most 3 sources named by outlet and age (no URLs), short page excerpts kept inside their `<<<UNTRUSTED_EXTERNAL_INPUT>>>` markers, and a plain failure message on timeout or error. The declaration is derived from the chat `WEB_SEARCH_TOOL`. Declared only when the app wires the search service (`VoiceDeps.web_search`). Parity guard: `tests/unit/test_voice_web_search.py` fails when a chat tool is neither declared to live nor on its documented chat-only list. Not verified against the real Live model (no live call was possible).
- `task_watch.py`: `TaskAnnouncer` watches ALL tasks of the user while voice is on (resolver reads the task from the database, so chat-started tasks count), claims each state change once, queues it until the model and user are quiet, and injects one short server-initiated turn (completed with file name, failed, cancelled, approval, needs input). `find_catch_up` and `AnnouncedStore` give the one-sentence catch-up after voice was off. Progress is never spoken; a spoken yes never approves (approval stays on the task card (Approve button)). Kill switch `MAYA_VOICE_TASK_ANNOUNCE`.
- `event_bridge.py`: `VoiceEventBridge` subscribing to Redis workspace task events, filtering for the session's active tasks, and translating lifecycle transitions into spoken natural language updates.
- `live_session.py`: `LiveVoiceSession` managing audio frame queues, transcripts, tool dispatch, and sub-150 ms barge-in interruption handling (Decision D12).
- `session_manager.py`: `VoiceSessionManager` storing the real provider resumption handle in Redis (`voice:resumption:{workspace_id}:{session_id}`), and rotating sessions transparently at 10 minutes before the 15-minute provider hard limit.

## Protocol v2 modules

- `protocol.py`: close codes, limits, Origin policy, per-user socket limiter and the v2 frame builders (`READY`, `TURN_STATE`, `TRANSCRIPT`, ...). Pure functions; the wire format lives here.
- `connection.py`: `VoiceConnection`, the per-socket supervisor. Resolves or creates the chat session, connects Gemini, sends `READY` or `LIVE_UNAVAILABLE`, runs the sender, receive, announcement and persistence loops, enforces idle and duration limits, and ends every failure with an `ERROR` frame and a close code.
- `gemini_live.py`: `GeminiLiveBridge` around one provider session: receive per turn, tool calls off the receive path (errors answered to the model), real resumption handle, GoAway reconnect, history seeding, sanitised task-update injection. No simulator: `LiveUnavailable` is raised instead. Tests pass a fake provider through the `connector` argument (`tests/support/fake_live.py`).
- `turns.py`: `TurnTracker`, the pure state machine behind `TRANSCRIPT` and `TURN_STATE` and the per-turn persistence hand-off.
- `persistence.py`: `VoicePersistenceService` using the shared chat tables (`sessions` channel `web`, `messages` modality `audio`/`text`). Errors raise; no random ids.

Protocol, close codes and limits: `docs/api/VOICE_SPECIFICATION.md`, ADR-0015. Real-service behaviour is unverified until the live spike.

## API Route

- `WS /api/v1/voice/live`: WebSocket bridge for bidirectional audio streaming and JSON control frames (`app.api.v1.voice`).

- `prompts.py`: the voice system instruction (ask-first policy, natural speech rules, memory on/off addenda).
- `live_options.py`: Live VAD, interruption and speech options as named constants (SDK-supported, not verified live).
- `memory.py`: connect-time memory block, `recall_memory` and `save_memory` handlers (reuse `app/memory/chat_memory.py`).

## Voice scope: master tracker vs conversation-bound

- `scope` is chosen at connect time (HELLO `scope`, or the `?scope=` query). `protocol.normalize_scope`:
  only the exact value `master` selects the master scope; anything else (missing, unknown) is the
  conversation-bound default, so older clients are unchanged.
- **master** (top-bar voice, one per app): the task watcher already covers every task of the
  authenticated user (RLS and an owner check in the resolver; never another user's). In master
  scope the digests also name the conversation a task belongs to (`TaskFacts.conversation`), the
  prompt adds `MASTER_VOICE_INSTRUCTION` (short updates, what finished and what is left, what
  needs the user, never approve, silence when nothing changed), non-urgent spoken updates are at
  least 10 s apart (`MASTER_SPACING_SECONDS`; failures, questions, approvals and final results
  still skip the spacing), and the idle timeout is off (a muted, quiet tracker is normal; the max
  duration still ends it and the client reconnects).
- **conversation**: unchanged. The client decides when to end it (different conversation opened).
- Not implemented: re-pointing a running master session at another conversation for persistence
  (it keeps storing its spoken turns in the conversation it started in; typed text elsewhere goes
  through normal chat). Service/health events (Docker, mail, Notion) have no event feed yet, so the
  master voice does not announce them.

## Spoken task progress (exact, live model only)

- `task_digest.py` builds, without any model call, the facts of a task from its rows: step "N of M",
  the real step title (as the task card shows it), done / running / waiting / failed lists, a cleaned
  output excerpt (marked untrusted), the verifier result, the file, and a rough time left only when
  at least two measured gaps between step completions exist.
- `task_watch.TaskAnnouncer` queues step updates per step (one claim key per step), coalesces steps
  finishing within 8 s into one digest, spaces non-urgent updates 20 s apart, and lets failures,
  approvals, questions and the final result go out as soon as nobody is speaking. The digest is
  injected through `GeminiLiveBridge.inject_task_digest` into the configured live session only; no
  gateway or other model is called. The old generic per-event lines are never spoken.
- Review: the digest asks the live model to check a finished step against the user's request and to
  call `report_step_review(step_id, verdict, note)`; the verdict is stored in the step's
  `output_result.voice_review` and shown on the task card. Not verified live: that the real model
  makes the call on a server-initiated turn. With voice off no review happens; the catch-up lists
  exactly what is done and left.
- Notion by voice is propose-only (`notion_voice.py`): the four write tools create the same
  approval-gated task as chat; the card appears in the chat; a spoken yes never approves. Database
  names are read once per connection into the instruction so misheard names can be mapped. After a
  Notion note is really written, the completion digest asks for one `save_memory` line about where
  it lives (memory on only).
