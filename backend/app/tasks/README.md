# Tasks

Background tasks and lifecycle event streaming (Phase 05).

- `events.py`: defines `TaskEvent` schema and `TaskEventPublisher` for Redis Pub/Sub channels
  (`maya:events:{workspace_id}`). Publishes state transition events (`task_plan_created`, `subtask_dispatched`,
  `subtask_status_changed`, `task_paused_approval`, `task_completed`, `task_failed`, with backward-compatible aliases
  `planning_started` and `awaiting_approval`). Event types are restricted to the supported enum and
  timestamps must be timezone-aware (normalized to UTC). Helper-supplied payload values cannot override the trusted
  subtask identifiers, assigned agent, status, or failure message.

- Task result message: when the outbox relay publishes a terminal event (`task_completed`, `task_failed`, `task_cancelled`), `TaskRuntime` calls `app.services.task_result.TaskResultService.ensure` in the background. It writes ONE assistant message into the task's conversation (metadata `kind: task_result`, `task_id`, `status`, `artifact_ids`, `spoken_summary`), idempotent per task and state, from the task's real outputs only (lite model call, deterministic template fallback). Live voice reads the same stored `spoken_summary` (`app/voice/task_watch.py`).

- `healing.py`, `doc_healing.py`: self-healing decisions (re-route, skip, retry without a tool, non-critical tolerance) and the document fallback built from earlier step outputs; the worker applies them. See `docs/architecture/AGENT_REQUEST_FLOW.md` section 7.

- `runtime.py`: `TaskRuntime` runs the queue consumer, the outbox relay and the startup reconciler in
  the API process (ADR-0012). Each unit of work uses `workspace_session`, which sets
  `app.workspace_id` on every transaction because tenant tables are FORCE row-level security.
  Workspaces to serve come from `public.list_workspaces_with_open_work()` (SECURITY DEFINER,
  migration 0014). Follow-up jobs are enqueued after commit. Worker lifecycle and tool
  executions, approval decisions and task aborts are written to the audit chain.
- Crash recovery (never wedged in `executing`): a job that raises is rolled back (no
  `PendingRollbackError` left behind) and logged as `subtask_worker_loop_error error=<class>
  where=<file:line:function < ...>` (no values). `_recover_failed_job` then works in a fresh
  session: an `in_progress` step takes the bounded retry path; a `pending` step (the crash rolled
  its work back) is queued once more, and on the second crash `SubtaskWorker.fail_step_plainly`
  fails it with a plain reason. The Notion book step reads "Could not prepare the Notion page:
  <reason>" and the task ends "completed with errors" (the finished book is kept; the step carries
  the worker-written `verification_proof.recovered_failure` mark, which a planner cannot set); any
  other step fails as a handler failure does. A finished step whose dependents were not queued has
  them queued at once. `TaskRuntime.sweep_stalled` (loop every 60 s, `reconcile` at start) finds
  `executing` tasks with a `pending` step whose dependencies are done and no queue entry (queued,
  in flight or scheduled), or an `in_progress` step nobody holds, and re-enqueues it (bounded:
  50 per pass, 5 per step; a step must be seen stalled on two passes so work being enqueued right
  now is never doubled). Tasks waiting on a person (`awaiting_approval`) are not swept.
- A step that needs a detail (`needs_input.py`) pauses the task as `awaiting_approval`:
  `tasks.status` is CHECK-constrained (migration 0002) and has no `awaiting_input`; writing it was
  the IntegrityError that wedged a book task. The `needs_input` marker on the step and the card in
  the chat tell a question from an approval.
- Concurrency: `TaskRuntime` runs `MAYA_TASK_WORKER_CONCURRENCY` (default 3, 1 to 8) worker loops.
  `queue.py` dequeues fairly across workspaces and tasks; a subtask already in flight is never
  claimed twice; quota errors retry with a pause (`rate_limit.py`). See ADR-0012. A task
  submitted while every worker is busy queues (never fails) and shows a `Queued: N tasks ahead`
  progress step (`SubtaskQueue.waiting_ahead`, closed by the worker when its first job starts).
  Research steps share no mutable state across tasks: search rate buckets are per task, the
  Gemini grounding quota cooldown is one process-wide bucket on purpose
  (`app/agents/grounding_cooldown.py`).
- `worker.py`: executes one subtask per job; bounded retry with backoff; never leaves a subtask
  `in_progress`; no success without a handler (no mock handler, no fallback signing key).
- `executor.py`: single gateway call per subtask; gateway errors fail the subtask; credential-like
  keys are stripped from prompts.
- `dispatch.py`: `FleetSupport` builds the structured context a specialist agent needs (document
  and email specs come from the model gateway) and turns an agent result into the persisted
  proof; artifacts carry `storage_uri` and `sha256` so they register as `FileArtifact`, and are
  `verified` only after the verifier passed.
- Dispatch order in the worker: explicit tool step (`input_data.tool`, through `McpGateway`) ->
  fleet agent for the role -> generic executor (roles without an agent only). Tool tiers:
  unclassified = denied, `RESTRICTED` = blocked, `APPROVAL_REQUIRED` = pause until the approve
  endpoint resumes with a payload-bound token; rejecting fails the subtask.
- Run the API with one worker process only (`--workers 1`).

Specification: [docs/architecture/ORCHESTRATOR_SPECIFICATION.md](../../../docs/architecture/ORCHESTRATOR_SPECIFICATION.md).

- `acceptance` and the long-document pipeline (ADR-0020): `pipeline.py` (pure helpers the worker uses), `long_document.py` (outline, chapter, assemble and expand/trim stages); the plan is built in `app/agents/long_document_plan.py`; the length check is `app/automation/acceptance.py`. The worker scales the document agent budget with the requested pages (`execution_timeout_for`) and renames chapter steps once the outline exists.
- The learning book (ADR-0031): the same stages in `learning_book` mode (`pipeline["mode"]`), planned in `app/agents/learning_book_plan.py` with the prompts of `app/agents/prompts/learning_book.py`; a chapter's Markdown travels in its output under `draft_markdown`; for Notion `assemble_context` builds one Markdown file and the final `notion_page` step (`notion_create_page_from_job`) is prepared by the worker (`_prepare_notion_book`) so its approval binds the exact content hash.
