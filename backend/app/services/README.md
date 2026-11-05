# Services

Application logic that sits between the API routes and the data and model layers.

- `chat_service.py` (Phase 04): one text message in, a stream of typed events out. It validates, rate-limits, checks
  session ownership, claims the idempotency key, stores the user message, streams the reply through the model
  gateway and writes one terminal record (the reply, completed or interrupted, plus the final idempotency state)
  behind `asyncio.shield`, so a disconnect cannot lose a reply that was generated and billed. Message text, replies,
  idempotency keys and provider error text never reach a log.
- `chat_idempotency.py`: the Redis-backed idempotency record (`SET NX` claim, keyed hash of the request, 15-minute
  in-progress claim, 24-hour result). Redis failures raise `IdempotencyUnavailable` and chat fails closed.
- `context_manager.py`: a pure function of the stored history, the new message and a token budget. It keeps the
  system instruction, keeps `user` and `model` turns alternating, and drops the oldest whole turns when the estimate
  exceeds the budget. It never deletes stored messages.

The contract is [docs/api/CHAT.md](../../../docs/api/CHAT.md); the decision is
[ADR-0008](../../../docs/adr/0008-chat-api-contract.md).
