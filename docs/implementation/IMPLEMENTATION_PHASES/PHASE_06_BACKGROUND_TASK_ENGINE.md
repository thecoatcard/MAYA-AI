# Phase 06 — Background Task Execution Engine

## A. Phase Overview

- **Phase Number:** Phase 06
- **Phase Name:** Background Task Execution Engine
- **Objective:** Build the asynchronous, fault-tolerant background task execution engine coordinating subtask workers, enforcing per-tenant idempotency, guaranteeing durable task events via a transactional outbox, managing the 13-stage execution lifecycle, and executing sagas with compensating rollbacks.
- **Why This Phase is Necessary:** As detailed in [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §10 and Decision D8, long-running agent workflows must never block HTTP connections or worker processes while awaiting external API responses or human decisions. Furthermore, message queues that redeliver unacknowledged messages after visibility timeouts can cause catastrophic duplicate side effects unless idempotent handling, short jobs, and transactional outboxes are rigorously enforced.
- **Expected Outcome:** An asynchronous worker cluster executing subtask jobs; per-tenant idempotency deduplication; transactional outbox recording events in PostgreSQL before Redis relay; a saga compensation engine rolling back failed partial workflows; and task lifecycle management endpoints (`GET /api/v1/tasks`, `POST /api/v1/tasks/{id}/abort`).
- **Priority:** Critical
- **Estimated Complexity:** High
- **Estimated Duration:** 12 business days (Weeks 4–6 of master schedule).

---

## B. Prerequisites

- **Required Completed Phases:** Phase 02 (Authentication, Users, Workspaces, and Database Security) and Phase 05 (Orchestrator and Agent Framework).
- **Required Infrastructure:** PostgreSQL 17 with `tasks`, `subtasks`, and `compensations` tables; Redis 7.4 with AOF persistence.
- **Required Architectural Decisions:** [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) Decision D8 (Short jobs, no worker blocking on humans, Celery/ARQ spike resolution from ADR-003) and Decision D6.
- **Required Documentation:** `docs/architecture/DATA_MODEL.md`, `docs/adr/0003-task-queue-selection.md`.

---

## C. Scope

### Included Features

- Background worker execution runtime based on ADR-003 queue selection (ARQ or Celery).
- Worker rule enforcement (Decision D8):
  - Exactly one subtask per queued job message.
  - Job timeout set strictly greater than longest individual subtask execution limit.
  - Workers never sleep or block waiting for human approval; upon hitting `awaiting_approval`, state is checkpointed and the worker exits immediately.
- Per-tenant idempotency enforcement (`UNIQUE (workspace_id, idempotency_key)`): duplicate task submissions return the existing task immediately without re-executing.
- Transactional Outbox pattern: task events written to PostgreSQL outbox table within the same transaction as state updates, then relayed to Redis Pub/Sub by an outbox worker.
- Saga and compensation manager (backend/app/tasks/saga.py (planned artifact)):
  - Automatic registration of forward actions in `compensations` table.
  - Step-by-step compensation rollback on task failure or user cancellation.
  - Verification of compensation permission tier (compensations inherit action tier and may require approval).
- Task management endpoints:
  - `POST /api/v1/tasks`: Submit task with required idempotency key.
  - `GET /api/v1/tasks`: List tasks with status, agent, and date filters.
  - `GET /api/v1/tasks/{id}`: Detailed DAG status, subtasks, and artifacts.
  - `GET /api/v1/tasks/{id}/events`: SSE stream of live progress events.
  - `POST /api/v1/tasks/{id}/abort`: Cancel active task and trigger compensation sequence.

### Excluded Features

- Direct execution of specialized agent logic (deferred to Phase 09).
- Human approval token issuance and consumption (deferred to Phase 07).
- Third-party MCP server calls (deferred to Phase 08).
- Real-time voice interruption bridge (deferred to Phase 12).

### Deferred Features

- Cross-region worker clustering and distributed dead-letter re-routing (post-MVP).

### Dependencies on Future Phases

- Phase 07 injects approval interceptors into the saga workflow.
- Phase 08 and Phase 09 run their tool and agent execution within the subtask worker harness built here.

---

## D. Implementation Breakdown

### Task P06-T001: Task Worker Harness and Job Lifecycle

- **Task ID:** P06-T001
- **Task Name:** Implement Async Worker Runner for Subtask Dispatch
- **Detailed Description:** Create backend/app/tasks/worker.py (planned artifact) configuring the queue worker pool. Implement job handler `execute_subtask_job(subtask_id: UUID)`:
  - Load subtask from PostgreSQL under tenant context.
  - Verify dependencies are satisfied in LangGraph checkpointer.
  - Transition status to `in_progress`.
  - Execute assigned agent handler within bounded timeout (default 300s).
  - On completion, record output result and verification proof; update checkpointer; trigger dependent subtasks.
  - If approval is needed, transition to `awaiting_approval`, persist checkpoint, and exit job cleanly.
- **Expected Files:** backend/app/tasks/worker.py (planned artifact), backend/app/tasks/queue.py (planned artifact), backend/tests/integration/test_worker_lifecycle.py (planned artifact).
- **Dependencies:** Phase 05.
- **Assigned Agent Role:** Distributed Systems Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_worker_lifecycle.py`.
- **Documentation Updates:** docs/operations/WORKER_ARCHITECTURE.md (planned artifact).
- **Acceptance Criteria:** Workers process subtasks asynchronously; `awaiting_approval` stops the worker immediately without holding thread or connection; job timeout terminates runaway processes.

### Task P06-T002: Per-Tenant Idempotency Enforcement

- **Task ID:** P06-T002
- **Task Name:** Build Idempotency Key Gate for Task Submission
- **Detailed Description:** Implement backend/app/tasks/idempotency.py (planned artifact). On task submission, check `tasks` table for existing `(workspace_id, idempotency_key)`. If found, return HTTP 200 with existing task ID and status. If not found, insert new task record within a database transaction and enqueue the root subtasks.
- **Expected Files:** backend/app/tasks/idempotency.py (planned artifact), backend/app/api/v1/tasks.py (planned artifact).
- **Dependencies:** Phase 02, P06-T001.
- **Assigned Agent Role:** Backend Systems Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_task_idempotency.py` (concurrent identical submissions).
- **Documentation Updates:** docs/api/TASKS_SPECIFICATION.md (planned artifact).
- **Acceptance Criteria:** 10 concurrent requests with the identical idempotency key result in exactly 1 task created; 9 requests return the existing task without duplicate execution.

### Task P06-T003: Transactional Outbox and Event Dispatcher

- **Task ID:** P06-T003
- **Task Name:** Implement Transactional Outbox for Durable Task Events
- **Detailed Description:** Create migration `0003_transactional_outbox.py` creating table `task_outbox_events(id, workspace_id, event_type, payload, status, created_at)`. Whenever task or subtask state changes, write the event to `task_outbox_events` in the same database transaction. Implement background dispatcher `outbox_dispatcher.py` polling pending events with `SELECT ... FOR UPDATE SKIP LOCKED`, publishing to Redis Pub/Sub, and marking rows `dispatched`.
- **Expected Files:** backend/app/db/alembic/versions/0003_transactional_outbox.py (planned artifact), backend/app/tasks/outbox.py (planned artifact), backend/tests/integration/test_outbox_delivery.py (planned artifact).
- **Dependencies:** Phase 02.
- **Assigned Agent Role:** Distributed Systems Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_outbox_delivery.py`.
- **Documentation Updates:** docs/architecture/EVENT_ARCHITECTURE.md (planned artifact).
- **Acceptance Criteria:** Task state updates and events are atomically committed; Redis disconnect does not lose events; events are reliably republished upon Redis reconnection.

### Task P06-T004: Saga Compensation Engine and Rollback Manager

- **Task ID:** P06-T004
- **Task Name:** Build Saga Compensation Registration and Execution Service
- **Detailed Description:** Implement `SagaManager` in backend/app/tasks/saga.py (planned artifact). Before or immediately after any mutating action (e.g. creating remote branch, drafting email), register a record in `compensations` table (`forward_tool`, `forward_ref`, `compensating_tool`, `compensating_args`, `permission_level`). On task failure or cancellation, execute compensating actions in reverse topological order. If a compensating action is labelled irreversible (e.g. `gmail_send`), create a notification record and skip automated deletion.
- **Expected Files:** backend/app/tasks/saga.py (planned artifact), backend/tests/integration/test_saga_compensation.py (planned artifact).
- **Dependencies:** Phase 02, P06-T001.
- **Assigned Agent Role:** Senior Software Architect.
- **Required Tests:** `pytest backend/tests/integration/test_saga_compensation.py`.
- **Documentation Updates:** docs/architecture/SAGA_COMPENSATION.md (planned artifact).
- **Acceptance Criteria:** Failure triggers reverse rollback of registered compensations; irreversible actions are flagged without crashing the engine; compensation statuses update in database.

### Task P06-T005: Task Lifecycle Management API

- **Task ID:** P06-T005
- **Task Name:** Implement REST Endpoints for Tasks and SSE Event Stream
- **Detailed Description:** Create backend/app/api/v1/tasks.py (planned artifact) implementing:
  - `POST /api/v1/tasks`: Submit task with `instruction`, `idempotency_key`, and optional `budget_usd`.
  - `GET /api/v1/tasks`: List tasks with pagination, filtering by `status` and `agent`.
  - `GET /api/v1/tasks/{id}`: Detailed status including DAG nodes, subtask proofs, and spent budget.
  - `GET /api/v1/tasks/{id}/events`: SSE stream relaying live outbox/Redis progress updates.
  - `POST /api/v1/tasks/{id}/abort`: Mark task `cancelled`, terminate active worker processes, and trigger `SagaManager.rollback()`.
- **Expected Files:** backend/app/api/v1/tasks.py (planned artifact), backend/app/schemas/tasks.py (planned artifact).
- **Dependencies:** P06-T002, P06-T003, P06-T004.
- **Assigned Agent Role:** API Developer.
- **Required Tests:** `pytest backend/tests/integration/test_tasks_api.py`.
- **Documentation Updates:** docs/api/TASKS_SPECIFICATION.md (planned artifact).
- **Acceptance Criteria:** Full CRUD lifecycle functions; abort endpoint stops subtask dispatch within 500 ms and begins compensation; SSE streams progress smoothly.

---

## E. File and Folder Changes

- **Files to Create:**
  - backend/app/db/alembic/versions/0003_transactional_outbox.py (planned artifact)
  - backend/app/tasks/queue.py (planned artifact)
  - backend/app/tasks/worker.py (planned artifact)
  - backend/app/tasks/idempotency.py (planned artifact)
  - backend/app/tasks/outbox.py (planned artifact)
  - backend/app/tasks/saga.py (planned artifact)
  - backend/app/schemas/tasks.py (planned artifact)
  - backend/app/api/v1/tasks.py (planned artifact)
  - backend/tests/integration/test_worker_lifecycle.py (planned artifact)
  - backend/tests/integration/test_task_idempotency.py (planned artifact)
  - backend/tests/integration/test_outbox_delivery.py (planned artifact)
  - backend/tests/integration/test_saga_compensation.py (planned artifact)
  - backend/tests/integration/test_tasks_api.py (planned artifact)
  - docs/architecture/SAGA_COMPENSATION.md (planned artifact)
  - docs/api/TASKS_SPECIFICATION.md (planned artifact)
- **Files to Modify:**
  - backend/app/api/v1/router.py (planned artifact)
  - `backend/app/main.py`
- **Files to Avoid Modifying:**
  - `MAYA_AI_FINAL_PLAN.md`
- **Relevant Directories:** `backend/app/tasks/`, `backend/app/api/v1/`, `docs/architecture/`.

---

## F. API and Data Contracts

### 1. Task Submission Contract

- **`POST /api/v1/tasks`**
  - Headers: `Authorization: Bearer <jwt>`, `Idempotency-Key: <unique-client-key>`
  - Request:
    ```json
    {
      "title": "Quarterly Financial Analysis",
      "instruction": "Download invoices from email, compute summary in Excel, and generate PDF report.",
      "idempotency_key": "fin-q3-2026-run-01",
      "budget_usd": 1.5
    }
    ```
  - Response (`202 Accepted`):
    ```json
    {
      "task_id": "a548f074-b788-4f51-b844-0b1a0e9c991b",
      "status": "pending",
      "idempotency_key": "fin-q3-2026-run-01",
      "created_at": "2026-11-05T14:00:00Z"
    }
    ```

### 2. Task Cancellation Contract

- **`POST /api/v1/tasks/{id}/abort`**
  - Response (`200 OK`):
    ```json
    {
      "task_id": "a548f074-b788-4f51-b844-0b1a0e9c991b",
      "status": "compensating",
      "compensations_triggered": 2
    }
    ```

---

## G. Security Requirements

- **Tenant Isolation:** Outbox events, idempotency keys, and task queries must explicitly match the authenticated `workspace_id`.
- **Compensation Authorization:** Compensating actions inherit the security tier of their operation; automated rollbacks cannot perform RESTRICTED actions without user re-authorization.
- **Resource Limits:** Maximum execution duration per subtask is 300 seconds; tasks running longer are cancelled with `ExecutionTimeoutError`.

---

## H. Testing Strategy

- **Concurrency & Idempotency Testing:** Run 50 concurrent task submissions with identical idempotency keys; verify only one task executes and database unique constraints hold.
- **Worker Crash Recovery Testing:** Kill worker process with SIGKILL while a subtask is in progress; assert that upon worker restart, the task resumes from the last checkpoint without duplicate side effects.
- **Outbox Durability Testing:** Terminate Redis container; trigger task updates; verify all events persist in `task_outbox_events`; restart Redis and assert zero dropped events.

---

## I. Definition of Done

1. Background workers process subtasks asynchronously across worker pools.
2. Duplicate submissions with identical `(workspace_id, idempotency_key)` are refused.
3. Transactional outbox commits state and events atomically, relaying cleanly to Redis.
4. Sagas register compensations and execute rollbacks upon task abort or failure.
5. Task API endpoints provide comprehensive lifecycle control and live SSE event streams.
6. Documentation in docs/architecture/SAGA_COMPENSATION.md (planned artifact) is complete.

---

## J. Phase Exit Gate

- **Demonstrated Proof:** `pytest backend/tests/integration/test_worker_lifecycle.py` and `pytest backend/tests/integration/test_task_idempotency.py` pass cleanly; crash-recovery chaos test verifies zero duplicate task execution.
- **Dependent Phase Unlocked:** Phase 07 (Security, Permissions, and Human Approval) is cleared to begin.
