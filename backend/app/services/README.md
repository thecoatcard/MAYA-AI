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
- `audit_service.py`: `record_audit(session, *, workspace_id, user_ref, status, action=None, server_name=None,
  tool_name=None, permission_level=None, task_id=None, subtask_id=None, approval_id=None, input=None, output=None,
  duration_ms=None) -> int` appends a redacted, hash-chained row (advisory lock per workspace, SAVEPOINT) and raises
  `AuditWriteError` on failure (security decisions fail closed). `verify_chain` returns `(valid, first_bad_row_id,
  row_count)`. `pseudonymise_user_ref` and `purge_user_ref` support GDPR purge. Not yet called by other services.

The contract is [docs/api/CHAT.md](../../../docs/api/CHAT.md); the decision is
[ADR-0008](../../../docs/adr/0008-chat-api-contract.md).
- `task_submission.py` (ADR-0016): `TaskSubmissionService.submit` plans, persists tasks and subtasks, writes the outbox event and a `task.submitted` audit row in one tenant transaction and enqueues after commit; used by `POST /api/v1/tasks` and the chat `submit_task` tool. `chat_task_idempotency_key` derives a stable key from the chat message id.
- `chat_service.py` also runs the chat `submit_task` function-calling turn (at most one task per message, `task.created` event) and the optional intent classifier (off unless `MAYA_CHAT_INTENT_CLASSIFIER` and the user preference are both on; stored in `user_settings`, ADR-0016). `context_manager.py` holds both system prompts.
