# Phase 04 — Chat and Conversation System

## A. Phase Overview

- **Phase Number:** Phase 04
- **Phase Name:** Chat and Conversation System
- **Objective:** Build the MVP persisted text-chat path after resolving its public API contract, including workspace/user-isolated messages, streaming, bounded context, and the basic Next.js chat surface.
- **Why This Phase is Necessary:** Natural language conversation is the primary interface for users to instruct MAYA AI. Before asynchronous autonomous agents and background workers can be coordinated, a reliable, low-latency, and persistent conversation bus must exist to receive user intent, provide immediate streaming feedback, and store full interaction histories.
- **Expected Outcome:** Authenticated chat messages persist under Row-Level Security and stream according to an owner-approved contract; the context manager respects model/token budgets; and the web client uses authenticated fetch-based streaming.
- **Priority:** High
- **Estimated Complexity:** Medium
- **Estimated Duration:** 8 business days.
- **Implementation Blocker:** The master plan defines `POST /api/v1/chat/message` as an SSE stream of task events, while this phase draft streams model tokens and adds session CRUD endpoints that are absent from the master API table. The owner must approve the public API semantics before implementation; this draft is not approval.

---

## B. Prerequisites

- **Required Completed Phases:** Phase 02 (Authentication, Users, Workspaces, and Database Security) and Phase 03 (Gemini AI Integration and Model Gateway).
- **Required Infrastructure:** Running PostgreSQL 17 and Redis 7.4 instances.
- **Required Credentials or External Services:** Active `GEMINI_API_KEY` for live model calls. Any such external call requires explicit approval; deterministic tests must use mocks.
- **Required Architectural Decisions:** [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §14 (`sessions` and `messages` DDL), §15 (API Surface), and an owner-approved decision reconciling the stream contract and session lifecycle.
- **Required Documentation:** The data-model documentation and the API contract documentation created after the public contract is approved.

---

## C. Scope

### Included Features

- Database access layer and repositories for `sessions` and `messages`.
- Persist user and assistant messages to a session under workspace and user isolation.
- Real-time streaming chat endpoint using the owner-approved semantics for `POST /api/v1/chat/message`.
- Conversation context management bounded by the approved model/token budget; do not assume an unapproved turn count.
- Token calculation and message metadata recording (model alias, execution time, token metrics).
- Next.js chat canvas: message thread display, auto-scrolling, markdown rendering with syntax-highlighted code blocks, copy-to-clipboard, and loading indicators.
- Connection resilience: fetch-based streaming with bearer authentication, event/message IDs, and a client idempotency key. A reconnect must not re-submit or re-bill the same message.

### Excluded Features

- Autonomous multi-agent DAG decomposition and task creation (deferred to Phase 05).
- WebAudio voice streaming and Gemini Live bidirectional audio (deferred to Phase 12).
- Approval cards and action execution inside the chat feed (deferred to Phase 07 and Phase 14).
- Vector memory citations and semantic retrieval injection (deferred to Phase 11).

### Deferred Features

- Multi-user collaborative chat channels within a workspace (post-MVP).
- Export conversation transcript to PDF/Markdown (deferred to Phase 10).

### Dependencies on Future Phases

- Phase 05 hooks the LangGraph Orchestrator into the chat endpoint to parse instructions into DAG plans.
- Phase 12 connects real-time voice transcripts directly into the `messages` table initialized here.

---

## D. Implementation Breakdown

### Task P04-T001: Session and Message Repository Layer

- **Task ID:** P04-T001
- **Task Name:** Implement Async Repositories for Sessions and Messages
- **Detailed Description:** Create backend/app/db/repositories/chat.py (planned artifact) with the session and message persistence operations required by the owner-approved lifecycle contract. All queries must run under the authenticated session's `app.workspace_id` RLS context and enforce user-level ownership.
- **Expected Files:** backend/app/db/repositories/chat.py (planned artifact), backend/app/schemas/chat.py (planned artifact).
- **Dependencies:** Phase 02 completion.
- **Assigned Agent Role:** Backend Systems Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_chat_repository.py`.
- **Documentation Updates:** The data-model documentation, once created.
- **Acceptance Criteria:** Repository operations strictly enforce tenant isolation; queries return correctly typed Pydantic models; pagination functions as specified.

### Task P04-T002: Sliding Context Window and History Manager

- **Task ID:** P04-T002
- **Task Name:** Build Context Manager for Model History Truncation
- **Detailed Description:** Implement `ContextManager` in backend/app/services/context_manager.py (planned artifact). Retrieve historical messages for a session, format them for the Gemini API (`user` and `model` turns), and apply a sliding window bounded by the approved model/token budget while preserving system instructions and required initial context. Calculate total prompt tokens using the `ModelGateway` tokenizer.
- **Expected Files:** backend/app/services/context_manager.py (planned artifact), backend/tests/unit/test_context_manager.py (planned artifact).
- **Dependencies:** Phase 03 completion.
- **Assigned Agent Role:** AI Systems Engineer.
- **Required Tests:** `pytest backend/tests/unit/test_context_manager.py`.
- **Documentation Updates:** `docs/architecture/SYSTEM_OVERVIEW.md`.
- **Acceptance Criteria:** Context window never exceeds token thresholds; alternating user/model turn sequence is enforced; older turns are cleanly pruned without data loss in the database.

### Task P04-T003: Authenticated Streaming Chat Endpoint

- **Task ID:** P04-T003
- **Task Name:** Implement `POST /api/v1/chat/message` with Server-Sent Events
- **Detailed Description:** After the owner approves the endpoint contract, implement `POST /api/v1/chat/message` with bearer authentication, workspace/user checks, message persistence, context assembly, and the approved SSE event semantics. The model-token stream in this draft is only one option; it conflicts with the master plan's task-event stream and must not be implemented without that decision.
- **Expected Files:** backend/app/api/v1/chat.py (planned artifact), backend/app/services/chat_service.py (planned artifact).
- **Dependencies:** P04-T001, P04-T002.
- **Assigned Agent Role:** Backend Systems Engineer.
- **Required Tests:** Test authentication, user/workspace isolation, persistence, event IDs, idempotent retry/reconnect, stream interruption, upstream cancellation, and no duplicate billing.
- **Documentation Updates:** The API contract documentation created after owner approval.
- **Acceptance Criteria:** The approved stream contract is implemented; user and assistant records persist with usage metadata; reconnect does not duplicate work; disconnect cancels upstream work. Measure and report latency without asserting an unapproved target.

### Task P04-T004: Resolve Session Lifecycle and Public API Contract

- **Task ID:** P04-T004
- **Task Name:** Obtain Owner Decision on Chat API Shape
- **Detailed Description:** Before implementing any public API, record the owner's choice for (1) token-stream versus task-event SSE semantics at `/api/v1/chat/message`, (2) how the initial session is created and identified, and (3) whether session CRUD endpoints are in scope. The current master plan does not specify session CRUD. Do not silently add those routes.
- **Expected Files:** Approved API contract in the chat API documentation and corresponding phase acceptance criteria.
- **Dependencies:** P04-T001 and P04-T003 design.
- **Assigned Agent Role:** API Developer.
- **Required Tests:** Contract tests must be specified after the owner decision.
- **Documentation Updates:** Chat API contract documentation.
- **Acceptance Criteria:** A written owner-approved request, response, event, idempotency, and session-lifecycle contract exists before route implementation.

### Task P04-T005: Frontend Chat Canvas and Streaming Hook

- **Task ID:** P04-T005
- **Task Name:** Build Next.js Chat Canvas and SSE Hook
- **Detailed Description:** In `apps/web/`, implement custom hook `useChatStream` in `src/hooks/useChatStream.ts` using fetch with bearer authentication, buffer accumulation, stable event IDs, idempotent retry, and explicit error handling. Build components:
  - `MessageThread`: Scrollable list rendering user and assistant message bubbles.
  - `MarkdownRenderer`: Secure markdown parsing with syntax-highlighted code blocks.
  - `ChatInput`: Textarea with auto-resize, Enter-to-submit, Shift+Enter for newline, and submit button.
  - Session controls only if the owner approves session CRUD in P04-T004.
- **Expected Files:** Chat hook, message thread, input and page; session controls are conditional on owner approval.
- **Dependencies:** P04-T003 and owner approval of P04-T004.
- **Assigned Agent Role:** Frontend Systems Engineer.
- **Required Tests:** `npm --prefix apps/web test` and Playwright component test.
- **Documentation Updates:** `docs/architecture/REPOSITORY_STRUCTURE.md`.
- **Acceptance Criteria:** Browser uses authenticated fetch streaming and safely renders content; session controls are included only if the owner approves them. Do not claim browser acceptance before an authenticated UI path exists.

---

## E. File and Folder Changes

- **Files to Create:**
  - backend/app/api/v1/chat.py (planned artifact)
  - backend/app/schemas/chat.py (planned artifact)
  - backend/app/db/repositories/chat.py (planned artifact)
  - backend/app/services/chat_service.py (planned artifact)
  - backend/app/services/context_manager.py (planned artifact)
  - backend/tests/unit/test_context_manager.py (planned artifact)
  - backend/tests/integration/test_chat_repository.py (planned artifact)
  - backend/tests/integration/test_chat_streaming.py (planned artifact)
  - apps/web/src/hooks/useChatStream.ts (planned artifact)
  - apps/web/src/components/chat/MessageThread.tsx (planned artifact)
  - apps/web/src/components/chat/MarkdownRenderer.tsx (planned artifact)
  - apps/web/src/components/chat/ChatInput.tsx (planned artifact)
  - apps/web/src/app/chat/page.tsx (planned artifact)
  - approved chat API contract documentation
- **Files to Modify:**
  - backend/app/api/v1/router.py (planned artifact)
  - apps/web/src/lib/api-client.ts (planned artifact)
- **Files to Avoid Modifying:**
  - `MAYA_AI_FINAL_PLAN.md`
- **Relevant Directories:** `backend/app/api/v1/`, `backend/app/services/`, `apps/web/src/components/chat/`.

---

## F. API and Data Contracts

### 1. Proposed Chat Message Contract (Not Approved)

The token-stream contract below is retained only as a proposal. It conflicts with the master plan's task-event stream and must not be implemented until the owner selects and approves the public contract.

- **`POST /api/v1/chat/message`**
  - Headers: `Authorization: Bearer <jwt>`, `Accept: text/event-stream`
  - Request:
    ```json
    {
      "session_id": "8f8b80e8-0b53-4f9e-99f5-748a2784cf22",
      "content": "Analyze the repository structure and list top priorities.",
      "modality": "text"
    }
    ```
  - Response (`200 OK`, `text/event-stream`):
    ```
    event: message_start
    data: {"message_id": "d3b07384-d113-4f38-8c01-7b02c81d89e5", "session_id": "8f8b80e8-0b53-4f9e-99f5-748a2784cf22"}

    event: token
    data: {"text": "I "}

    event: token
    data: {"text": "will "}

    event: token
    data: {"text": "analyze "}

    event: message_end
    data: {"message_id": "d3b07384-d113-4f38-8c01-7b02c81d89e5", "finish_reason": "stop", "tokens": 42}
    ```

---

## G. Security Requirements

- **Tenant Isolation:** All session lookups, message queries, and streaming handlers verify both workspace and user ownership under RLS.
- **Input Sanitization:** Validate inputs against an owner-approved limit. Sanitize Markdown at render time; stripping script tags is not an XSS control.
- **Remote Content:** Block remote Markdown images to prevent untrusted content from initiating arbitrary browser requests.
- **Client Disconnection Handling:** When client aborts HTTP stream, the backend immediately cancels the upstream Gemini API streaming context to avoid wasted token costs.
- **Rate Limiting:** Select and document per-user limits from a measured baseline before implementation; the draft's numerical limit is not approved.

---

## H. Testing Strategy

- **Repository Integration Tests:** Test cross-workspace message isolation, verifying that User A cannot read User B's conversation history.
- **Streaming Mock Tests:** Test SSE parser resilience using simulated chunked streams with delays and unexpected terminations.
- **End-to-End Tests:** After an authenticated UI path exists, verify message submission, the owner-approved stream semantics, auto-scroll, and code-block rendering.

---

## I. Definition of Done

1. The owner-approved message/session contract is implemented without unapproved routes.
2. Authenticated messages persist under RLS with token/usage metadata.
3. Streaming, reconnect, idempotency, interruption, cancellation, and user/workspace isolation pass deterministic tests.
4. Context manager respects the approved model/token budget without dropping system instructions.
5. Markdown rendering is safe; remote images are blocked.
6. Browser acceptance is demonstrated only after an authenticated UI path exists.
7. The approved API contract documentation matches the implementation.

---

## J. Phase Exit Gate

- **Demonstrated Proof:** Owner-approved API contract; deterministic persistence, isolation, reconnect, and failure tests; browser evidence after an authenticated UI path exists.
- **Dependent Phase Unlocked:** Phase 05 (Orchestrator and Agent Framework) is cleared to begin.
