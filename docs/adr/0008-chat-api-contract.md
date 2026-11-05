# ADR-0008: Chat API contract, idempotency and persistence

- **Status:** Accepted (the owner chose the contract on 2026-11-05; limits and rates stay provisional, see the API document)
- **Date:** 2026-11-05
- **Deciders:** Repository owner
- **Related:** [Phase 04](../implementation/IMPLEMENTATION_PHASES/PHASE_04_CHAT_CONVERSATIONS.md), [Chat API](../api/CHAT.md), [ADR-0007](0007-gemini-model-gateway-architecture.md), [ADR-0006](0006-tenant-isolation-with-row-level-security.md)

## Context

The master plan defines `POST /api/v1/chat/message` as an SSE stream of task events. The Phase 04 draft streamed raw
model tokens and added session CRUD endpoints that are not in the master API table. The phase forbids writing routes
before the owner picks a contract (P04-T004). The three open questions were token-stream versus task-event semantics,
how the first session is created, and whether session CRUD is in scope.

## Decision

1. **Typed event stream on the master-plan path.** `POST /api/v1/chat/message` returns SSE with named events
   (`message.accepted`, `delta`, `done`, `error`), each with a numeric id. Phase 05 adds `task.*` events to the same
   stream; clients ignore unknown events, so the addition is not a breaking change.
2. **Sessions.** `session_id` is optional; the server creates a session when it is omitted and returns the id. Only two
   read endpoints are added: list the caller's sessions and read a session's messages. Create, rename and delete are
   out of the MVP.
3. **Idempotency.** An `Idempotency-Key` header is required. The key is claimed atomically in Redis (24 hours) with a hash
   of the request. A repeat never calls the model again: a finished or interrupted request is replayed, a running one is
   refused with 409. If Redis is unreachable, chat fails closed.
4. **No migration.** `sessions` and `messages` already exist with full access for the runtime role and row-level
   security by workspace. User-level ownership is enforced in the repository. Idempotency state lives in Redis, not in a
   new column, which avoids a schema change.
5. **Persistence order.** The user message is stored before the model call; the assistant reply is stored when the
   stream ends, completed or interrupted, in a transaction shielded from cancellation, so a disconnect cannot lose the
   record of what was generated and billed.
6. **Gateway use.** Replies come from `ModelGateway.generate_stream` (alias `orchestrator`). A chat message is not a task,
   so no task budget is recorded; the gateway's own per-call metering still applies and the usage is returned in `done`.

## Consequences

### Positive

- One stream shape now and after the orchestrator exists; no client rewrite in Phase 05.
- A reconnect cannot cause a second model call or a second charge.
- No schema change, so no migration approval or rollback plan is needed for this phase.

### Negative / trade-offs

- Replay returns the whole stored reply as one `delta`, not the original token pieces; clients must replace, not
  append, when `replayed` is true.
- Idempotency is only as durable as Redis for 24 hours; after that, or if Redis loses the key, the same key is treated
  as new.
- Input limit, rate limit and context budget are provisional until the owner approves values.
- Until a login UI exists the chat page cannot be exercised in a browser end to end.
