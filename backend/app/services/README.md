# Services

Application logic that sits between the API routes and the data and model layers.

- `chat_service.py` (Phase 04): one text message in, a stream of typed events out. It validates, rate-limits, checks
  session ownership, claims the idempotency key, stores the user message, streams the reply through the model
  gateway and writes one terminal record (the reply, completed or interrupted, plus the final idempotency state)
  behind `asyncio.shield` to resist cancellation. Terminal persistence can still fail or have an ambiguous outcome.
  On disconnect, upstream closure and the terminal write proceed concurrently. Message text, replies, idempotency
  keys and provider error text never reach a log.
- `chat_idempotency.py`: the Redis-backed idempotency record (`SET NX` claim, keyed hash of the request, 15-minute
  in-progress claim, 24-hour result). Existing values are updated or deleted with an atomic comparison; saving an
  absent value uses `SET NX`. Lookup, claim and save failures raise `IdempotencyUnavailable`; release is best effort.
  These operations do not reconcile Redis and database writes after expiry, loss or an ambiguous commit.
- `context_manager.py`: a pure function of the stored history, the new message and a token budget. It keeps the
  system instruction, keeps `user` and `model` turns alternating, and drops the oldest whole turns when the estimate
  exceeds the budget. It never deletes stored messages.
- `projection_service.py` (Phase 05): projects LangGraph `AgentExecutionState` into relational `tasks` and `subtasks`
  tables when explicitly called. A supplied idempotency key deterministically identifies a workspace-scoped task
  across session retries; without an explicit key, task identity falls back to the explicit/context task ID or the
  workspace/session pair. JSON encoding converts supported non-JSON values and scalar keys, and rejects unsupported or
  non-finite values before writes. It is not automatically invoked by graph transitions. A supplied connection must
  already be scoped; otherwise the service opens a workspace tenant transaction.

The contract is [docs/api/CHAT.md](../../../docs/api/CHAT.md); the decision is
[ADR-0008](../../../docs/adr/0008-chat-api-contract.md).
