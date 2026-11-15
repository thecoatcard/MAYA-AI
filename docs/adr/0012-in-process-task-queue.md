# ADR-0012: In-process asyncio task queue (amends ADR-0003)

- **Status:** Accepted (owner approval given in the conversation that produced this change)
- **Date:** 2026-11-08
- **Deciders:** Repository owner
- **Related:** [ADR-0003](0003-task-queue-selection.md), [ADR-0006](0006-tenant-isolation-with-row-level-security.md), [MAYA_AI_FINAL_PLAN.md D8, section 10.2](../../MAYA_AI_FINAL_PLAN.md)

## Context

ADR-0003 chose a Celery-compatible queue. The implemented task engine instead runs an in-process
`asyncio` queue (`app/tasks/queue.py`), consumed by a worker loop inside the FastAPI process. A
live audit found the pipeline dead because the worker, outbox relay and startup reconciler opened
database sessions with no tenant context: every tenant table is FORCE row-level security and the
API runs as `maya_app`, so those loops saw zero rows.

## Decision

1. Keep the in-process asyncio queue for the current single-node, single-user product. Do not add
   Celery or another broker now. ADR-0003's Celery recommendation is not implemented; this ADR
   supersedes it for the current stage.
2. Every background unit of work runs inside a tenant transaction (`app.workspace_id` set in each
   transaction) through `app/tasks/runtime.py`. Work is never enqueued before the transaction that
   created it has committed.
3. Cross-workspace discovery: `workspaces` is itself FORCE RLS, so the runtime lists tenants with
   open work through the SECURITY DEFINER function `public.list_workspaces_with_open_work()`
   (migration 0014, EXECUTE granted to `maya_app`). It is called with no tenant context; each
   returned workspace is then processed in its own tenant transaction. After a restart the
   reconciler re-enqueues pending work and returns interrupted `in_progress` subtasks to `pending`
   (bounded by the retry limit).
4. Run exactly one API worker process (`uvicorn --workers 1` in `backend/Dockerfile`). Each worker
   process would own its own queue, outbox relay and reconciler, causing duplicate relay and
   competing reconciliation. Scale out only after replacing the in-process queue with a shared one.
5. Failures are bounded and visible: a failing handler retries with exponential backoff (2 s, 4 s,
   capped at 30 s) up to three attempts, then marks the subtask and task `failed` with the real
   error message. A subtask is never left `in_progress`.

6. Amendment (task concurrency): the in-process queue is consumed by a bounded pool of N worker
   loops (`MAYA_TASK_WORKER_CONCURRENCY`, default 3, minimum 1, maximum 8), still inside the single
   API process. A new task never waits behind, fails or restarts a running one. Rules:
   - Fair dequeue: `SubtaskQueue` holds jobs per workspace and per task and serves the workspace
     with the fewest running jobs first, rotating over workspaces and over a workspace's tasks, so
     one busy workspace or one task with many ready subtasks cannot starve another. It stays work
     conserving.
   - Exactly-once claim: every job runs in its own tenant session and a job for a subtask that is
     already being processed is dropped. The "is the task done / what is unblocked" step locks the
     parent task row and reads sibling subtasks fresh, so parallel siblings (a fork) cannot both
     miss each other's completion.
   - DAG order inside a task is unchanged (dependencies are checked before a step starts).
   - A provider quota error (HTTP 429/408, also behind the gateway's exhausted-retry error) retries
     the subtask with a longer pause (20 s, 40 s) up to the same three attempts, then fails with
     "rate limited, try again in a minute". Quota never fails a task on the first error, and a
     failure, timeout or abort of one task never changes another.
   - A job that crashes outside its handler is recovered (retry or fail), never left `in_progress`.
   - Stopping the runtime cancels every worker loop and delayed retry.

## Consequences

- Positive: no new infrastructure, one process to run, tenant isolation is preserved for
  background work, a restart no longer strands tasks.
- Negative: queue contents are lost on restart (recovered from the database by the reconciler, so
  only in-flight handler progress is lost); no horizontal scaling.
- Redis Pub/Sub event delivery remains fire-and-forget; clients must refetch task state on
  reconnect.
