# Tasks API Specification (Phase 06)

## 1. Overview

The Tasks API provides endpoints to submit, monitor, and cancel long-running agent workflows with per-tenant idempotency, live SSE progress streaming, and saga rollbacks.

- **Base Path:** `/api/v1/tasks`
- **Authentication:** Bearer token (`Authorization: Bearer <token>`)
- **Tenant Scope:** Scoped automatically to `workspace_id` of the authenticated principal.

---

## 2. Endpoints

### 2.1 Submit Task

- **Method:** `POST /api/v1/tasks`
- **Headers:**
  - `Authorization: Bearer <jwt>`
  - `Idempotency-Key: <unique-string>` (or passed in body)
- **Request Body:**
  ```json
  {
    "instruction": "Download invoices from email, compute summary in Excel, and generate PDF report.",
    "title": "Quarterly Financial Analysis",
    "idempotency_key": "fin-q3-2026-run-01",
    "budget_usd": 1.5
  }
  ```
- **Responses:**
  - `201 Created`: Newly accepted task with DAG plan generated.
    ```json
    {
      "task_id": "a548f074-b788-4f51-b844-0b1a0e9c991b",
      "status": "executing",
      "idempotency_key": "fin-q3-2026-run-01",
      "created_at": "2026-11-06T12:00:00Z",
      "is_new": true
    }
    ```
  - `200 OK`: Duplicate submission with same idempotency key (idempotent replay, no second execution).
    ```json
    {
      "task_id": "a548f074-b788-4f51-b844-0b1a0e9c991b",
      "status": "executing",
      "idempotency_key": "fin-q3-2026-run-01",
      "created_at": "2026-11-06T12:00:00Z",
      "is_new": false
    }
    ```
  - `400 Bad Request`: Missing idempotency key.

---

### 2.2 List Tasks

- **Method:** `GET /api/v1/tasks`
- **Query Parameters:**
  - `status` (optional): Filter by task status (`pending`, `executing`, `awaiting_approval`, `completed`, `failed`, `cancelled`).
  - `limit` (default: 20, max: 100)
  - `offset` (default: 0)
- **Response:** `200 OK`
  ```json
  {
    "items": [
      {
        "id": "a548f074-b788-4f51-b844-0b1a0e9c991b",
        "workspace_id": "8488e147-19be-41bd-9524-7f61ad2231ab",
        "title": "Quarterly Financial Analysis",
        "instruction": "Download invoices...",
        "status": "executing",
        "plan_status": "executing",
        "budget_usd": 1.5,
        "spent_usd": 0.12,
        "created_at": "2026-11-06T12:00:00Z",
        "completed_at": null,
        "subtask_count": 4
      }
    ],
    "total": 1,
    "limit": 20,
    "offset": 0
  }
  ```

---

### 2.3 Get Task Details

- **Method:** `GET /api/v1/tasks/{task_id}`
- **Response:** `200 OK`
  ```json
  {
    "id": "a548f074-b788-4f51-b844-0b1a0e9c991b",
    "workspace_id": "8488e147-19be-41bd-9524-7f61ad2231ab",
    "user_id": "2bc35166-51b7-4a6c-9a4f-5606d20359b8",
    "session_id": "a548f074-b788-4f51-b844-0b1a0e9c991b",
    "title": "Quarterly Financial Analysis",
    "instruction": "Download invoices...",
    "idempotency_key": "fin-q3-2026-run-01",
    "status": "executing",
    "plan_status": "executing",
    "budget_usd": 1.5,
    "spent_usd": 0.12,
    "tool_turns": 4,
    "created_at": "2026-11-06T12:00:00Z",
    "updated_at": "2026-11-06T12:04:00Z",
    "completed_at": null,
    "active_plan": [],
    "subtasks": [
      {
        "id": "b3040224-b5b4-4f0e-b7e6-14ab61bdf968",
        "task_id": "a548f074-b788-4f51-b844-0b1a0e9c991b",
        "assigned_agent": "email",
        "description": "Download invoice attachments",
        "dependencies": [],
        "status": "completed",
        "input_data": {},
        "output_result": { "attachments": ["inv-1.pdf"] },
        "verification_proof": { "verified": true, "exit_code": 0 },
        "error_message": null,
        "retry_count": 0,
        "created_at": "2026-11-06T12:00:00Z",
        "updated_at": "2026-11-06T12:02:00Z",
        "completed_at": "2026-11-06T12:02:00Z"
      }
    ]
  }
  ```

---

### 2.4 Abort Task (Trigger Saga Rollback)

- **Method:** `POST /api/v1/tasks/{task_id}/abort`
- **Behaviour:** emits a `task_cancelled` event (payload `status: "cancelled"`), not `task_failed`.
  Workers skip subtasks of a cancelled task and never flip it back to `completed`.
- **Response:** `200 OK`
  ```json
  {
    "task_id": "a548f074-b788-4f51-b844-0b1a0e9c991b",
    "status": "cancelled",
    "compensations_triggered": 2,
    "executed": 2,
    "failed": 0,
    "notifications": []
  }
  ```

---

### 2.5 Live Task Events Stream (SSE)

- **Method:** `GET /api/v1/tasks/{task_id}/events`
- **Headers:** `Accept: text/event-stream`
- **Errors:** `404` when the task does not exist in the caller's workspace.
- **Response:** `200 OK` (Stream; `Cache-Control: no-cache, no-transform`, `X-Accel-Buffering: no`)
  ```http
  HTTP/1.1 200 OK
  Content-Type: text/event-stream
  Cache-Control: no-cache, no-transform
  Connection: keep-alive

  data: {"event_type": "connected", "task_id": "a548f074-b788-4f51-b844-0b1a0e9c991b"}

  data: {"event_type": "subtask_status_changed", "payload": {"subtask_id": "...", "status": "in_progress"}}

  data: {"event_type": "task_completed", "payload": {"task_id": "...", "status": "completed"}}
  ```

---

## 3. Execution semantics (ADR-0012)

- `POST /tasks` enqueues root subtasks only after its transaction has committed.
- The in-process worker, outbox relay and reconciler run in a tenant context per workspace.
- A subtask handler error retries with backoff (max 3 attempts) and then fails the subtask and task
  with the real `error_message`. No canned output is ever substituted: with no model gateway or on
  a gateway error the subtask fails.
- `output_result` is always a JSON object. Text-only model output carries
  `verification_proof = {"status": "unverified", "kind": "llm_text", "content_sha256": ...}`;
  generated files carry a verified proof (`storage_uri`, `sha256`, ...).
- The signed approval token is never passed to handlers or LLM prompts.

## Delete tasks from history

- **`DELETE /api/v1/tasks/{task_id}`**: `204` when deleted. `404` for an unknown, another user's or already deleted task (idempotent for the UI). `409 task_not_finished` ("Cancel the task first") unless the task is completed, failed or cancelled.
- **`POST /api/v1/tasks/delete`** with `{"ids": [...]}` (1 to 100, else `422`): `{deleted, not_found, not_finished, files_removed}`. Unfinished tasks are left alone.
- **Removed:** the title, instruction, plan, results and trace; the steps (subtasks); the task's file records and MAYA's stored copies of those files. Pending approvals are closed (`revoked`). Deleted tasks leave lists, detail, events, voice catch-up and chat session activity.
- **Kept:** the audit log (a `task.deleted` row is added) and the outbox history, both keeping the task id; copies saved in the user's own working folder. The task row stays as a tombstone (`shared_context.deleted_at`) because `task_outbox_events` cascades from `tasks`; a real row delete would need a migration (not written).

## Delete files

`DELETE /api/v1/artifacts/{id}` and `POST /api/v1/artifacts/delete` (`{"ids": [...]}`, at most 100) remove the record and MAYA's stored copy (only paths that resolve inside the artifact storage root; `..` and links leading outside are never deleted). A file in the user's connected folder is never deleted.
