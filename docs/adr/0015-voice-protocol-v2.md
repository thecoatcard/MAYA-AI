# ADR-0015: Voice WebSocket protocol v2 (one conversation, honest failures)

- **Status:** Accepted (owner approved on 2026-11-08)
- **Date:** 2026-11-08
- **Deciders:** Repository owner
- **Related:** [ADR-0009](0009-refresh-cookie-transport.md), [Voice API](../api/VOICE_SPECIFICATION.md), [Voice subsystem](../architecture/VOICE_SUBSYSTEM.md), plan section 6, decisions D3, D5, D12, D13

## Context

A read-only audit of the voice path found that it could not work against the real Gemini Live service and that chat and voice were two unconnected conversations. The Live receive loop ended after the first model turn. A built-in simulator replaced the model whenever the key was missing or the connection failed, invented replies and created real tasks, and the client could not tell. Authentication failures closed the socket before accept, which browsers report as an opaque failure. Transcripts were saved once at disconnect with database errors swallowed and random ids returned. Stale audio was not dropped after a barge-in, a refused tool call killed the session, and the stored resumption token was fabricated.

The owner asked for backend and frontend to be integrated per the plan and for chat and voice to be one conversation. The existing web client must keep working while it moves to the new protocol.

## Decision

1. Keep `WS /api/v1/voice/live` and add **protocol v2** additively. v1 frames and `?token=` keep working; new frames (`READY`, `LIVE_UNAVAILABLE`, `TURN_STATE`, `TRANSCRIPT`, `MESSAGE_PERSISTED`, `TASK_EVENT`, `GO_AWAY`, `ERROR`) are added, and an optional first `HELLO` frame carries `{token, session_id, protocol}`. Legacy `TRANSCRIPT_USER/MODEL` frames are still sent to clients that did not opt into v2.
2. **Accept, then authenticate**, and use application close codes 4401, 4403, 4404, 4429, 4503. Add an Origin allow-list (same origin, loopback, explicit list), a per-user concurrent-socket limit, idle and maximum-duration limits and frame size limits. The server never logs the URL, query or token.
3. **One conversation.** A voice connection attaches to a chat session (`channel='web'`, created when none is given) and always reports its id in `READY`. Each completed turn is saved as a `messages` row (`modality` `audio` or `text`, metadata `turn_id`, `source`, `interrupted`, `incomplete`). Save failures are reported, never swallowed. The model is seeded with the last 20 turns when resuming. No schema change.
4. **No simulator in production.** Without a usable key, or when the connection fails, the server sends `LIVE_UNAVAILABLE{reason}` and closes with 4503. A fake provider is injected only through an explicit connector argument used by tests.
5. **Receive loop per turn**, tool calls executed off the receive path, refused or failing tool calls answered to the model as errors, fatal provider errors surfaced as `ERROR` plus a close code.
6. **Barge-in correctness:** `AUDIO_INTERRUPT{epoch}` and server-side dropping of stale audio until the provider's turn boundary; mic audio is always forwarded.
7. **Resumption:** store the real provider handle only; on `GoAway` reconnect with it; if none exists, fail honestly. The model id comes from the registry alias `live` with its fallback chain (D13).
8. D5 is unchanged (voice never approves); D3 is amended by the addendum below (memory tools added). Originally: two tools. Task submission is fast (row created, planning in the background), idempotent per provider call, and never returns an invented task id.

## Alternatives considered

- **Replace the endpoint with a new path and a hard v2 cut-over:** rejected; it would break the shipped client before the web track lands.
- **Keep the simulator behind a feature flag in production:** rejected; it is what hid every failure and violates the rule against simulating.
- **Authenticate before accept (status quo):** rejected; clients get no usable error.
- **Token only in the query string:** kept for compatibility, but `HELLO` is preferred because URLs reach proxy and access logs.

## Consequences

- Positive: a failed or unavailable Live service is visible; turns survive a crash; chat and voice share history; a refused approval cannot end the session.
- Negative: the server carries two transcript shapes until v1 clients are gone. The socket limit is per process, not cluster-wide.
- Mitigations: legacy frames are removed in a later change once the web client uses v2; limits are module constants that a settings change can wire.

## Security and compliance impact

Token handling moves out of URLs (HELLO), is never logged, and the Origin check blocks cross-site WebSocket use from foreign browser origins. Session ownership is enforced through the existing row-level-security repository. Injected task text is sanitised and delimited because tool output is untrusted. Approvals remain REST and UI only. This is an authentication and API change, so owner approval is required before it is accepted.

## Implementation notes

`backend/app/api/v1/voice.py` (handshake), `backend/app/voice/connection.py`, `protocol.py`, `turns.py`, `gemini_live.py`, `persistence.py`, `live_session.py`, `event_bridge.py`, `task_wiring.py`. Tests use a fake Live session (`backend/tests/support/fake_live.py`). Behaviour against the real service needs the owner's live spike.

## Review trigger

Revisit after the live spike, when the web client has moved to v2 (remove legacy frames), or when voice is served to a non-browser or desktop client.


## Addendum (Task V): D3 amended for memory; ask-first policy; natural Live config

The owner asked for memory in voice. **D3 is amended, D5 is not.** Voice tools are now `submit_task`, `get_task_status`, `recall_memory(query)` (read-only) and `save_memory(text, kind?)` (low-risk write: no approval, never credentials, `metadata.source = "voice"`). The two memory tools are declared only when the user's `memory_in_chat` setting is on (the same toggle as chat). `forget_memory` is **not** exposed by voice: a misheard word could delete data, so the model sends the user to the Memory page. Any other tool name is still rejected (`unsupported_tool`), and voice can **never** approve (D5 unchanged). Rationale: the tools reuse the audited chat memory path (credential refusal, dedupe, size caps, data-not-instructions labelling), touch only the caller's own rows, and a wrong save is undoable from the Memory page.

**Second amendment (owner request, GitHub repository review).** Voice also declares six READ-tier repository tools (`github_get_repo`, `github_list_tree`, `github_read_file`, `github_list_commits`, `github_list_pull_requests`, `github_list_issues`) through `VoiceToolRegistry.set_repo_handler`, backed by the same `RepoToolkit` as chat and the MCP gateway (the caller's own GitHub connection, or unauthenticated public-only reads). They are not registered MCP tools: `register_tool` and `register_mcp_tool` still raise, any other tool name is still `unsupported_tool`, voice still never approves (D5), and there is no write tool. Results are untrusted data (wrapped, redacted, size capped); the voice instruction tells the model to give a short spoken review and offer the written one in chat.

Also recorded: `submit_task` now requires a full `instruction` and a `deliverable`; the connection rejects an empty, too-short or subject-less brief (for example "create a ppt") with a `needs_clarification` result and never calls the task submitter. The system instruction is "ask first, act second" (module `app/voice/prompts.py`). A connect-time memory block (at most 1,200 characters, no embedding call) is placed in the Live instruction and rebuilt on every reconnect. New additive frame `MEMORY_SAVED`. Live options (VAD silence 800 ms, low end-of-speech and high start-of-speech sensitivity, explicit interruption handling) are SDK-supported and **not verified against the live service**.
