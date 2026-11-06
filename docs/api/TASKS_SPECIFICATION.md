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
    "budget_usd": 1.50
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
        "budget_usd": 1.50,
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
    "budget_usd": 1.50,
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
        "output_result": {"attachments": ["inv-1.pdf"]},
        "verification_proof": {"verified": true, "exit_code": 0},
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
- **Response:** `200 OK` (Stream)
  ```http
  HTTP/1.1 200 OK
  Content-Type: text/event-stream
  Cache-Control: no-cache
  Connection: keep-alive

  data: {"event_type": "connected", "task_id": "a548f074-b788-4f51-b844-0b1a0e9c991b"}

  data: {"event_type": "subtask_status_changed", "payload": {"subtask_id": "...", "status": "in_progress"}}

  data: {"event_type": "task_completed", "payload": {"task_id": "...", "status": "completed"}}
  ```
