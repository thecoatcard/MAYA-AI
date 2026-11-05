# Phase 05 — Orchestrator and Agent Framework

## A. Phase Overview

- **Phase Number:** Phase 05
- **Phase Name:** Orchestrator and Agent Framework
- **Objective:** Implement the central LangGraph-based Orchestrator, hierarchical `StateGraph`, Pydantic execution state models (`AgentExecutionState`, `SubTask`), DAG plan decomposition engine with cycle detection, and unified PostgreSQL state persistence via the LangGraph checkpointer (enforcing Decision D6).
- **Why This Phase is Necessary:** As established in [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §8 and Decision D6, multi-agent systems fail when execution state is fragmented across ad-hoc database columns and memory stores. A single authoritative LangGraph checkpointer in PostgreSQL guarantees that compound instructions are safely decomposed into acyclic dependency graphs, subtask states project accurately to relational tables, and execution can resume deterministically after interruptions or system restarts.
- **Expected Outcome:** A compiled LangGraph `StateGraph` powered by the `orchestrator` model alias; a plan decomposition engine converting instructions into DAGs of typed subtasks; cycle detection rejecting recursive dependencies; transactional checkpointing in PostgreSQL; and relational projections to the `tasks` and `subtasks` tables.
- **Priority:** Critical
- **Estimated Complexity:** High
- **Estimated Duration:** 12 business days (Weeks 4–6 of master schedule).

---

## B. Prerequisites

- **Required Completed Phases:** Phase 02 (Authentication, Users, Workspaces, and Database Security), Phase 03 (Gemini AI Integration and Model Gateway), and Phase 04 (Chat and Conversation System).
- **Required Infrastructure:** PostgreSQL 17 with Alembic schema migrated; Redis 7.4.
- **Required Credentials or External Services:** Active `GEMINI_API_KEY`.
- **Required Architectural Decisions:** [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) Decision D6 (One source of truth: LangGraph Postgres checkpointer with projection tables) and Decision D3 (Decoupled execution).
- **Required Documentation:** [`docs/architecture/SYSTEM_OVERVIEW.md`](../../architecture/SYSTEM_OVERVIEW.md), `docs/architecture/DATA_MODEL.md`.

---

## C. Scope

### Included Features

- Implementation of core state schemas: `SubTask` and `AgentExecutionState` using Pydantic v2.
- Central LangGraph `StateGraph` definition in backend/app/agents/orchestrator.py (planned artifact).
- Directed Acyclic Graph (DAG) generation from user instructions:
  - Intent classification.
  - Decomposition into discrete subtasks.
  - Topological sorting and cycle detection (Kahn's algorithm).
- PostgreSQL checkpointing using `langgraph.checkpoint.postgres.aio.AsyncPostgresSaver`.
- Relational projection service syncing LangGraph state into `tasks` and `subtasks` tables.
- Orchestrator tool suite:
  - `create_plan_dag`
  - `dispatch_subtask`
  - `request_human_approval` (initiates request only; never resolves it)
  - `synthesize_final_report`
- Orchestrator boundary constraints: read-only access to state; zero direct external network, filesystem, or shell execution capabilities.
- Live event streaming over Redis Pub/Sub for state transitions (`planning`, `executing`, `awaiting_approval`, `completed`, `failed`).

### Excluded Features

- Background worker execution of subtasks via Celery/ARQ (deferred to Phase 06).
- Cryptographic approval token minting and consumption (deferred to Phase 07).
- Specialized agent logic for Coding, Research, and Documents (deferred to Phase 09).
- Third-party MCP tool invocation (deferred to Phase 08).

### Deferred Features

- Autonomous plan dynamic re-planning during execution (deferred to Phase 06).
- Multi-orchestrator swarm coordination (post-MVP).

### Dependencies on Future Phases

- Phase 06 (Task Engine) consumes the DAG generated here and schedules subtasks onto queue workers.
- Phase 07 (Security & Approvals) intercepts `awaiting_approval` state nodes and manages human authorization.

---

## D. Implementation Breakdown

### Task P05-T001: Pydantic State Models and Action Schemas

- **Task ID:** P05-T001
- **Task Name:** Define `SubTask` and `AgentExecutionState` Models
- **Detailed Description:** Implement backend/app/agents/state.py (planned artifact) declaring the exact state structures specified in [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §8. Ensure `SubTask` includes fields for `id`, `assigned_agent`, `description`, `dependencies`, `status`, `input_data`, `output_result`, `verification_proof`, `error_message`, and `retry_count`. Implement `AgentExecutionState` with list reducers (`operator.add`) for `completed_task_ids` and `execution_trace`.
- **Expected Files:** backend/app/agents/state.py (planned artifact), backend/tests/unit/test_agent_state.py (planned artifact).
- **Dependencies:** None.
- **Assigned Agent Role:** AI Framework Engineer.
- **Required Tests:** `pytest backend/tests/unit/test_agent_state.py`.
- **Documentation Updates:** docs/architecture/ORCHESTRATOR_SPECIFICATION.md (planned artifact).
- **Acceptance Criteria:** Pydantic models validate input types, provide serialization/deserialization to dict and JSON, and enforce valid status literal values.

### Task P05-T002: Plan DAG Generation and Cycle Detection

- **Task ID:** P05-T002
- **Task Name:** Implement DAG Plan Generator and Topological Validator
- **Detailed Description:** Implement backend/app/agents/planner.py (planned artifact). Create structured prompt invoking `ModelGateway` under the `orchestrator` alias to decompose a compound instruction into a list of `SubTask` items with explicit dependency IDs. Implement deterministic cycle detection using Kahn's algorithm or Depth-First Search. If a cycle is detected, reject the plan and trigger one self-repair prompt turn. If repair fails, return a planning failure error.
- **Expected Files:** backend/app/agents/planner.py (planned artifact), backend/tests/unit/test_dag_planner.py (planned artifact).
- **Dependencies:** P05-T001, Phase 03.
- **Assigned Agent Role:** AI Algorithms Engineer.
- **Required Tests:** `pytest backend/tests/unit/test_dag_planner.py` (testing valid DAGs, cyclic dependencies, disconnected nodes, and single-task instructions).
- **Documentation Updates:** docs/architecture/ORCHESTRATOR_SPECIFICATION.md (planned artifact).
- **Acceptance Criteria:** Decomposes compound instructions into valid DAGs; cycles are caught 100% of the time before execution; topological order correctly identifies independent parallel roots.

### Task P05-T003: PostgreSQL LangGraph Checkpointer Integration

- **Task ID:** P05-T003
- **Task Name:** Configure `AsyncPostgresSaver` Checkpoint Storage (Decision D6)
- **Detailed Description:** Configure `AsyncPostgresSaver` in backend/app/agents/checkpointer.py (planned artifact) connecting through the async connection pool. Ensure the checkpointer runs its migration scripts creating `checkpoints` and `checkpoint_blobs` tables within PostgreSQL. Implement checkpoint retrieval by `session_id` and `thread_id`.
- **Expected Files:** backend/app/agents/checkpointer.py (planned artifact), backend/tests/integration/test_langgraph_checkpointer.py (planned artifact).
- **Dependencies:** Phase 02, P05-T001.
- **Assigned Agent Role:** Database & Distributed Systems Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_langgraph_checkpointer.py`.
- **Documentation Updates:** `docs/architecture/DATA_MODEL.md`.
- **Acceptance Criteria:** Graph execution state persists to PostgreSQL at each state transition; state can be reloaded across process restarts without data corruption.

### Task P05-T004: Relational State Projection Engine

- **Task ID:** P05-T004
- **Task Name:** Implement Projection Sync to `tasks` and `subtasks` Tables
- **Detailed Description:** Implement `StateProjectionService` in backend/app/services/projection_service.py (planned artifact). Listen to LangGraph state transitions or checkpointer hooks and transactionally project `AgentExecutionState` into the relational `tasks` and `subtasks` tables. Update task status, tool turns, completed timestamps, and subtask output results.
- **Expected Files:** backend/app/services/projection_service.py (planned artifact), backend/tests/integration/test_state_projection.py (planned artifact).
- **Dependencies:** P05-T003.
- **Assigned Agent Role:** Backend Systems Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_state_projection.py`.
- **Documentation Updates:** `docs/architecture/DATA_MODEL.md`.
- **Acceptance Criteria:** `tasks` and `subtasks` tables reflect LangGraph state accurately; projection updates are idempotent; queries for task status do not need to parse LangGraph binary checkpoints.

### Task P05-T005: Orchestrator StateGraph Compilation

- **Task ID:** P05-T005
- **Task Name:** Build and Compile LangGraph `StateGraph`
- **Detailed Description:** Implement backend/app/agents/orchestrator.py (planned artifact) constructing the `StateGraph(AgentExecutionState)`:
  - Nodes: `plan_instruction`, `evaluate_dependencies`, `dispatch_node`, `wait_for_approval`, `synthesize_results`, `handle_failure`.
  - Conditional Edges: Route to `dispatch_node` when runnable subtasks exist; route to `wait_for_approval` when subtasks require approval; route to `synthesize_results` when all subtasks succeed; route to `handle_failure` when fatal errors occur.
  - Compile graph with `checkpointer` and interrupt points before approval actions.
- **Expected Files:** backend/app/agents/orchestrator.py (planned artifact), backend/tests/integration/test_orchestrator_graph.py (planned artifact).
- **Dependencies:** P05-T002, P05-T003, P05-T004.
- **Assigned Agent Role:** Principal AI Architect.
- **Required Tests:** `pytest backend/tests/integration/test_orchestrator_graph.py`.
- **Documentation Updates:** docs/architecture/ORCHESTRATOR_SPECIFICATION.md (planned artifact).
- **Acceptance Criteria:** Orchestrator compiles without error; state steps through nodes deterministically; interrupt occurs before approval nodes; final report synthesized upon task graph completion.

### Task P05-T006: Redis State Event Bus Publisher

- **Task ID:** P05-T006
- **Task Name:** Implement Redis Event Publishing for Orchestrator Lifecycle
- **Detailed Description:** Implement backend/app/tasks/events.py (planned artifact) publishing JSON event notifications to Redis channels (`workspace:{ws_id}:events`) on every graph node transition:
  - `TASK_PLAN_CREATED`
  - `SUBTASK_DISPATCHED`
  - `SUBTASK_STATUS_CHANGED`
  - `TASK_COMPLETED`
  - `TASK_FAILED`
- **Expected Files:** backend/app/tasks/events.py (planned artifact), backend/tests/unit/test_task_events.py (planned artifact).
- **Dependencies:** P05-T005, Phase 01.
- **Assigned Agent Role:** Backend Systems Engineer.
- **Required Tests:** `pytest backend/tests/unit/test_task_events.py`.
- **Documentation Updates:** `docs/architecture/SYSTEM_OVERVIEW.md`.
- **Acceptance Criteria:** Events publish with standardized schemas and timestamp headers; Redis subscribers receive events within 10 ms of state change.

---

## E. File and Folder Changes

- **Files to Create:**
  - backend/app/agents/__init__.py (planned artifact)
  - backend/app/agents/state.py (planned artifact)
  - backend/app/agents/planner.py (planned artifact)
  - backend/app/agents/checkpointer.py (planned artifact)
  - backend/app/agents/orchestrator.py (planned artifact)
  - backend/app/services/projection_service.py (planned artifact)
  - backend/app/tasks/events.py (planned artifact)
  - backend/tests/unit/test_agent_state.py (planned artifact)
  - backend/tests/unit/test_dag_planner.py (planned artifact)
  - backend/tests/unit/test_task_events.py (planned artifact)
  - backend/tests/integration/test_langgraph_checkpointer.py (planned artifact)
  - backend/tests/integration/test_state_projection.py (planned artifact)
  - backend/tests/integration/test_orchestrator_graph.py (planned artifact)
  - docs/architecture/ORCHESTRATOR_SPECIFICATION.md (planned artifact)
- **Files to Modify:**
  - `backend/app/main.py`
  - `docs/architecture/SYSTEM_OVERVIEW.md`
  - `docs/architecture/DATA_MODEL.md`
- **Files to Avoid Modifying:**
  - `MAYA_AI_FINAL_PLAN.md`
- **Relevant Directories:** `backend/app/agents/`, `backend/app/services/`, `docs/architecture/`.

---

## F. API and Data Contracts

### 1. SubTask Pydantic Contract

```python
class SubTask(BaseModel):
    id: str
    assigned_agent: Literal["research", "coding", "document", "email", "pm", "browser", "file"]
    description: str
    dependencies: list[str] = []
    status: Literal["pending", "in_progress", "awaiting_approval", "completed", "failed"] = "pending"
    input_data: dict[str, Any] = {}
    output_result: dict[str, Any] | None = None
    verification_proof: dict[str, Any] | None = None
    error_message: str | None = None
    retry_count: int = 0
```

### 2. Agent Execution State Contract

```python
class AgentExecutionState(BaseModel):
    session_id: str
    user_id: str
    workspace_id: str
    user_instruction: str
    interaction_modality: Literal["voice", "text"]
    active_plan: list[SubTask] = []
    plan_status: Literal["planning", "executing", "paused", "completed", "failed"] = "planning"
    completed_task_ids: Annotated[list[str], operator.add] = []
    pending_approval: dict[str, Any] | None = None
    shared_context: dict[str, Any] = {}
    execution_trace: Annotated[list[dict[str, Any]], operator.add] = []
```

---

## G. Security Requirements

- **Privilege Separation:** The Orchestrator agent has no filesystem, shell, or network write access. It only plans, routes, and aggregates.
- **Untrusted Input Treatment:** User instructions are treated as untrusted text; instructions cannot directly modify graph topology without passing the schema validation gate.
- **State Isolation:** All checkpointer operations and relational projections include `workspace_id` to guarantee tenant isolation during execution.
- **Execution Limits:** Maximum 20 subtasks per DAG plan to prevent runaway graph expansion.

---

## H. Testing Strategy

- **Topological Sorting Tests:** Verify cycle detection against self-referencing nodes, diamond dependencies, circular loops, and disconnected subgraphs.
- **Crash Recovery Tests:** Execute graph to intermediate step, simulate abrupt process termination, re-instantiate graph from checkpointer, and assert clean continuation from the exact interrupted state.
- **Projection Consistency Tests:** Assert that relational `tasks` and `subtasks` tables match LangGraph checkpoint state exactly at each transition.

---

## I. Definition of Done

1. Graph models (`AgentExecutionState`, `SubTask`) serialize and deserialize cleanly.
2. Planner decomposes natural language instructions into validated, cycle-free DAGs.
3. LangGraph checkpoints persist to PostgreSQL via `AsyncPostgresSaver`.
4. Relational projections populate `tasks` and `subtasks` tables consistently.
5. Interrupts successfully pause execution before approval-required operations.
6. State transition events publish to Redis Pub/Sub channels.

---

## J. Phase Exit Gate

- **Demonstrated Proof:** `pytest backend/tests/integration/test_orchestrator_graph.py` passes; crash recovery test demonstrates 100% state restoration from PostgreSQL checkpoint.
- **Dependent Phase Unlocked:** Phase 06 (Background Task Execution Engine) is cleared to begin.
