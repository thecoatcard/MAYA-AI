# ADR-0016: Chat starts tasks by function calling, with an optional classifier

- **Status:** Accepted (owner decision, 2026-11-08), including migration 0015 for the per-user preference
- **Date:** 2026-11-08
- **Deciders:** Repository owner
- **Related:** [ADR-0008](0008-chat-api-contract.md), [Chat API](../api/CHAT.md), [Model gateway](../architecture/MODEL_GATEWAY.md), [ADR-0012](0012-in-process-task-queue.md)

## Context

Chat was text-only: it told the user to use "+ New Task" for background work. Tasks were created only by `POST /api/v1/tasks` and by voice. The owner asked for the text chat to create tasks itself, without making every message pay for an extra model call.

## Decision

1. **Function calling is the default path.** `ModelGateway.generate_stream` accepts optional `tools` (`ToolDefinition`). Function calls arrive on `GatewayStreamChunk.tool_calls` (`ToolCallRequest`: `call_id`, `tool_name`, `arguments`, opaque `thought_signature`). The gateway never executes a tool. Continuing after a call is a second `generate_stream` call whose contents end with `tool_call_content` and `tool_response_content` items. With `tools=None` nothing changes. A call with tools is admitted and counted as a tool turn.
2. **One tool, `submit_task(instruction, title?)`**, at most once per message (extra calls get a "skipped" response). The system prompt (`CHAT_TOOLS_SYSTEM_INSTRUCTION`) limits it to multi-step, tool, file or background work. The original text-only prompt is kept for deployments without a database.
3. **Shared submission.** `app/services/task_submission.py` plans, writes tasks, subtasks, the `task.plan_created` outbox event and an audit row (`task.submitted`) in one tenant transaction, and enqueues only after the commit. `POST /api/v1/tasks` now uses it. Voice keeps its own wiring (it must answer before planning finishes) and the same tables. The task is linked to the **current chat session**; no hidden session is created. The idempotency key derives from the user message id.
4. **New additive SSE event `task.created`** `{task_id, title, status}`, stored in the assistant message metadata and replayed.
5. **Optional classifier, off by default.** It runs only when the deployment switch `MAYA_CHAT_INTENT_CLASSIFIER` **and** the user preference `chat_intent_classifier` are both true: one `lite` structured call, then a deterministic submit and a short acknowledgement. Fails open.

## Per-user preference storage (migration 0015)

No existing storage fitted: `users` has no settings column, and `memory_items` (category `preference`) would put an operational flag in the user-visible, searchable, purgeable memory list. The owner approved migration `0015`: table `user_settings(workspace_id, user_id, settings jsonb, updated_at)`, primary key `(workspace_id, user_id)`, composite foreign key to `users`, forced row-level security (`ws_isolation`, workspace only; the repository also filters by user), and `SELECT, INSERT, UPDATE` for `maya_app` (no DELETE). `GET` and `PUT /api/v1/settings/chat` read and write `settings.chat_intent_classifier`; `get_chat_preferences` reads it from the database. The classifier runs only when this preference and the deployment switch are both true.

## Consequences

- Positive: simple questions cost nothing extra; tasks started from chat are real, audited, idempotent and visible in the Task Ledger without hidden sessions; the gateway gains a reusable streaming function-calling primitive.
- Negative: a task start adds a second model call (the confirmation) and planning time inside the reply deadline; `done.usage` excludes the classifier and planner calls (the gateway meters them).
- Not tested against the real Gemini API: real function calling, thought signatures and stream shapes are unverified.

## Alternatives considered

- **Classifier on every message:** rejected as the default (cost and latency for every message).
- **Store the flag in memory items or Redis:** rejected as above.
- **Execute the call inside the gateway:** rejected; the gateway never executes tools.

## Addendum (2026-11-09)

The chat tool set is no longer only `submit_task`: [ADR-0018](0018-chat-memory.md) adds `save_memory` and `forget_memory` (on by default, per-user switch `memory_in_chat`), and the `GET /sessions/{id}/messages` response now exposes a narrowed `metadata` (`task`, `memories_saved`).

## Addendum (2026-11-09, latency and honesty)

- The `submit_task` declaration and the tools system instruction now list explicit deliverable triggers (pdf, ppt/pptx/deck/slides, docx, xlsx/spreadsheet/excel, report, notes document, and Hinglish "banao" / "bana do") and multi-step research, after a live run answered "a slide deck of 5 tips" inline.
- A structured refusal from the task service (`ok: false`, `reason`, `hint`) is passed back to the model as the function response; no task event or card is produced. A just-started task must not be described as completed.
