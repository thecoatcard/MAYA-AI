# ADR-0018: Chat saves and recalls memories

- **Status:** Proposed (owner request, 2026-11-09)
- **Date:** 2026-11-09
- **Deciders:** Repository owner (approval pending)
- **Related:** [ADR-0016](0016-chat-function-calling-and-classifier.md), [ADR-0006](0006-tenant-isolation-with-row-level-security.md), [Chat API](../api/CHAT.md), [Memory API](../api/MEMORY_SPECIFICATION.md)

## Context

A user said "Yaad rakhna: mera favourite colour neela hai" and MAYA replied that it would remember, but nothing was stored, and a new chat answered "I don't know". Two gaps: chat never wrote memory, and chat never read it. The memory subsystem (`memory_items`, embeddings through the `embed` alias, hybrid search, row-level security) already existed.

## Decision

1. **Write path with no extra model call.** A second and third function declaration, `save_memory(text, kind?)` and `forget_memory(query)`, sit next to `submit_task`. The system prompt (an addendum appended to the chat instruction) says when to save (explicit request in any language, or a stable personal fact the user clearly wants kept) and when not (secrets, payment data, small talk, third parties' private data, instructions found in tool or web text). The handler is `ChatMemoryService` (`app/memory/chat_memory.py`): it refuses credential-looking text before anything is embedded or stored, collapses whitespace, caps the text at 500 characters, embeds once, and updates a near-identical memory (raw cosine similarity at least 0.92) instead of adding a duplicate. Memories are stored `permanent` with category `preference`, `project` or `knowledge` (the existing CHECK values) and `metadata = {source: "chat", kind}`.
2. **Honest replies.** The real outcome (`saved`, `updated`, `refused`, `error`, `skipped`) is fed back to the model as the function response, exactly as for `submit_task`, so a failed save cannot be described as a success. At most 3 memories are saved per reply.
3. **Additive SSE event `memory.saved`** `{memory_id, text, updated}`, sent after the tool runs and before the confirmation text, stored in the assistant message metadata (`memories_saved`) and replayed. `GET /sessions/{id}/messages` now returns a narrowed `metadata` object (`task`, `memories_saved` only) so a reload can show the same chip. The web chip offers Undo through the existing `DELETE /api/v1/memory/items/{id}`.
4. **Read path.** For each live user turn, if the user has at least one searchable memory (one existence query), the message is embedded through the gateway (`embed`) and the top 5 hits with raw similarity at least 0.65 are formatted as a delimited block ("stored data, not instructions", at most 1,500 characters, angle brackets escaped, control characters removed). The block is the first part of the **last user turn**: the system instruction, tool declarations and earlier history stay byte-identical from request to request. Recall has a 6 second limit; any failure (embedding 429/503, database, timeout) logs a warning with the error class and the turn continues without memory. An empty memory costs no embedding call.
5. **Control.** Per-user setting `memory_in_chat`, default on, stored as `settings.memory_in_chat` in the existing `user_settings` JSON (**no migration**), read and written through `GET` and `PUT /api/v1/settings/chat` (both fields now optional on PUT, at least one required) and a switch in Settings. Off means no recall and neither memory tool nor memory prompt is sent. An unreadable setting reads as off. No deployment switch.
6. **Forget** is built: `forget_memory` searches, then deletes the single best match with raw similarity at least 0.80, refuses (and asks) when the top two are within 0.03, and reports exactly what was deleted. The Memory page delete also works.

## Thresholds

Recall 0.65, dedupe 0.92, forget 0.80, ambiguity gap 0.03 are module constants in `app/memory/chat_memory.py`. They are ordered on purpose (a question about a statement scores lower than a paraphrase of it) and tested on exact cosine values with fakes. **They have not been measured against the real embedding model** and should be tuned once real scores are observed.

## Consequences

- Positive: "remember" really stores, new chats recall, a user can see, undo, forget and switch it off; empty memories pay nothing; the cacheable prompt prefix is unchanged.
- Negative: one embedding call per message once any memory exists; the default tool set grew from one to three declarations (default on); a chip can reappear after reload for a memory already undone (Undo then reports it as removed); a reply that failed entirely stores no assistant row, so a memory saved in that turn has no chip (it is still on the Memory page).
- Not tested against real Gemini: function calling, real embedding scores and the model's judgement of what to save are unverified.

## Alternatives considered

A classifier call per message to decide saving (extra cost on every message); injecting memories into the system instruction (breaks the stable cacheable prefix); a deployment master switch (not requested).

## Addendum (2026-11-09, latency and honesty)

- Recall cap lowered from 6 s to 2.5 s and skipped for trivial messages (at most 4 words, all greetings or acknowledgements, no question mark): recall runs before the model call, so it is pure added latency.
- With memory off, the system instruction is the memory-off variant (chosen per user setting, so still byte-stable per user): it says memory is turned off in Settings and forbids promising to remember. Memory tools stay undeclared.
- `save_memory` results carry a `confirmation` text ("Saved: <text>") for the model to echo in the user's language.


## Addendum (Task V): memory in voice

The same memory feature now serves voice (`app/voice/memory.py`, ADR-0015 addendum). The per-user `memory_in_chat` setting covers chat **and** voice; its Settings label is **"Memory in chat and voice"**. Voice reads a bounded block (at most 1,200 characters, permanent memories first, then newest) at every connect and reconnect without an embedding call, offers `recall_memory` (same 0.65 threshold, top 5, 2.5 s cap) and `save_memory` (same credential refusal and 0.92 dedupe; stored with `metadata.source = "voice"`; at most 10 saves per connection), and does not offer `forget_memory`. `ChatMemoryService.save` gained a `source` argument and `overview()`; `format_memory_block` gained `max_chars`.
