# Orchestrator & Agent Framework Specification (Phase 05)

## 1. Overview & Architectural Goals

The central Orchestrator is the hierarchical coordinator of MAYA AI's multi-agent system. As established in `MAYA_AI_FINAL_PLAN.md` §8 and Architectural Decision D6:
- A LangGraph PostgreSQL checkpointer (`AsyncPostgresSaver`) is provided for the intended authoritative runtime state.
- The planner can decompose compound instructions into Directed Acyclic Graphs (DAGs) of typed subtasks.
- Dependency graphs are validated with Kahn's algorithm; circular dependencies are deterministically rejected with one self-repair retry turn.
- A projection service can upsert execution state into relational `tasks` and `subtasks` tables when called explicitly.
- A publisher can emit events to Redis Pub/Sub (`maya:tasks:{workspace_id}`) when called explicitly.
- Graph nodes return execution-state updates; the graph alone does not establish action authorization or isolation.

The graph, checkpointer, projector, and event publisher are not wired into production routes or lifecycle, and transitions do not automatically invoke projection or publishing. This integration gap is distinct from the planned Phase 06 workers/outbox and Phase 07 approval-resolution work. Phase 05 acceptance has not been established.

---

## 2. Pydantic Execution State Models

Location: `backend/app/agents/state.py`

### 2.1 SubTask

```python
class SubTask(BaseModel):
    id: str
    assigned_agent: Literal["research", "coding", "document", "email", "pm", "browser", "file"]
    description: str
    dependencies: list[str] = Field(default_factory=list)
    status: Literal["pending", "in_progress", "awaiting_approval", "completed", "failed"] = "pending"
    input_data: dict[str, Any] = Field(default_factory=dict)
    output_result: dict[str, Any] | None = None
    verification_proof: dict[str, Any] | None = None
    error_message: str | None = None
    retry_count: int = 0
```

### 2.2 AgentExecutionState

```python
class AgentExecutionState(BaseModel):
    session_id: str
    user_id: str
    workspace_id: str
    user_instruction: str
    interaction_modality: Literal["voice", "text"]
    active_plan: list[SubTask] = Field(default_factory=list)
    plan_status: Literal["planning", "executing", "paused", "completed", "failed"] = "planning"
    completed_task_ids: Annotated[list[str], operator.add] = Field(default_factory=list)
    pending_approval: dict[str, Any] | None = None
    shared_context: dict[str, Any] = Field(default_factory=dict)
    execution_trace: Annotated[list[dict[str, Any]], operator.add] = Field(default_factory=list)
```

---

## 3. DAG Planning & Cycle Detection

Location: `backend/app/agents/planner.py`

1. **Model Decomposition**: Instruction is classified and decomposed using the `orchestrator` model alias (`gemini-3.8-flash`) via `ModelGateway.generate_structured`.
2. **Kahn's Topological Sort**:
   - Nodes with in-degree 0 (no unresolved dependencies) are dispatched first.
   - A dependent is unblocked only when each prerequisite is both marked `completed` in the current plan and present in `completed_task_ids`.
   - Direct sorting rejects malformed graph entries, duplicate task IDs, repeated dependency edges and missing dependency IDs. Missing edges raise `PlanValidationError`; cycles raise `PlanCycleError`.
3. **Cycle Detection**:
   - Self-loops (`task.dependencies = [task.id]`) and multi-node cycles are caught.
   - Raises `PlanCycleError`.
   - On a detected cycle, one planner repair request includes the previous plan as data. The planning prompt includes the expected JSON schema.
4. **Limits**:
   - Maximum 20 subtasks and 190 unique dependency edges per DAG plan (`MAX_SUBTASKS_PER_PLAN = 20`) to prevent runaway graph expansion. One subtask can have at most 19 dependencies.
   - The response schema enforces a non-empty plan, the subtask maximum, and per-subtask edge maximum; validation also rejects repeated edges and enforces the total edge bound.

---

## 4. LangGraph StateGraph Architecture

Location: `backend/app/agents/orchestrator.py`

```
  +---------+
  |  START  |
  +----+----+
       |
       v
   +---+----+
   |  plan  |
   +---+----+
       |
       +------------+ (plan_status == "failed")
       |            |
       |            v
       |     +--------------+
       |     |  synthesize  |-----> END
       |     +--------------+
       |
       +------------+ (pending_approval or approval required)
       |            |
       |            v
       |     +------------------+
       |     |  await_approval  |-----> END (Paused; resolution not implemented here)
       |     +------------------+
       |
       v (runnable subtasks exist)
 +-----+------+
 |  dispatch  |<----+
 +-----+------+     | (more runnable subtasks)
       |            |
       +------------+
       |
       +------------> await_approval (if action requires human sign-off)
       |
       +------------> synthesize (if terminal or no runnable work remains)
       |
       +------------> END (waiting for worker completion)
```

### 4.1 Orchestrator Tools
- `create_plan_dag`: Invokes planner decomposition.
- `dispatch_subtask`: Transitions subtask status from `pending` to `in_progress`.
- `request_human_approval`: Sets `pending_approval` payload; never resolves or approves it autonomously.
- `synthesize_final_report`: Aggregates completed results. An empty plan or an existing failed status cannot be reported as successful.
- Dependency release requires agreement between the active subtask's `completed` status and its ID in `completed_task_ids`; stale or partial completion state does not unblock work.

Approval isolation and authorized resolution have not been verified by this graph; the pause is not an approval token or a complete approval workflow.

---

## 5. Persistence & Checkpointer (Decision D6)

Location: `backend/app/agents/checkpointer.py`

- Built on `langgraph.checkpoint.postgres.aio.AsyncPostgresSaver`.
- Converts app database URL to standard PostgreSQL scheme for Psycopg 3.
- On Windows, `python -m app` installs and verifies `WindowsSelectorEventLoopPolicy` before calling `uvicorn.run`.
  Checkpointer creation validates the actual running loop and raises if an alternate entrypoint started an incompatible
  loop; it does not try to mutate policy after startup.
- `create_postgres_checkpointer(settings, workspace_id=..., user_id=...)` requires server-owned UUID identities from the
  authenticated request context. It opens one dedicated connection for that identity and sets `app.workspace_id` and
  `app.user_id`; every saver read/write uses that same owner-bound connection. Never derive either identity from graph
  state, a client-selected thread ID, or checkpoint payloads.
- `checkpoints`, `checkpoint_blobs`, and `checkpoint_writes` have forced RLS requiring both owner settings. Schema is
  provisioned by Alembic revision `0006`; runtime setup does not call `checkpointer.setup()` and the `maya_app` role
  cannot create schema objects.
- Legacy rows without an owner are not backfilled. They remain NULL and inaccessible under RLS until a separate,
  trusted recovery decision is made.
- `create_memory_checkpointer()` provided for lightweight tests and in-memory workflows.
- The PostgreSQL owner-isolation test is authored but was not run for this change; no live cross-tenant database
  verification is claimed.

---

## 6. Relational State Projection Engine

Location: `backend/app/services/projection_service.py`

- Projects `AgentExecutionState` into `tasks` and `subtasks` only when explicitly called; graph transitions do not invoke it automatically.
- Uses upserts (`ON CONFLICT (workspace_id, id) DO UPDATE ...`), but terminal timestamps can change on repeated projections.
- Opens `tenant_transaction(engine, workspace_id)` when no connection is supplied; a supplied connection must already be appropriately scoped.
- Explicit idempotency keys generate a stable workspace-scoped task UUID when neither the `task_id` argument nor state context supplies a task ID; explicit/context task IDs take precedence. Without an explicit key, task identity falls back to the workspace/session pair.
- Existing UUID-form subtask IDs are preserved; other IDs use `uuid5(task_id, subtask_id)`.
- Supported non-JSON values and scalar keys (such as UUIDs, datetimes, and decimals) are converted for the DAG and subtask JSON fields. Unsupported values and non-finite numbers fail JSON encoding before database writes.

---

## 7. Redis Event Bus

Location: `backend/app/tasks/events.py`

- The publisher is not invoked automatically by graph transitions or production routes.
- Channel: `maya:tasks:{workspace_id}`.
- Payload schema: `{event_type, session_id, workspace_id, timestamp, payload}`.
- Event types are validated against the supported enum. Timestamps must include a timezone and are normalized to UTC.
- Helper payloads cannot override their trusted subtask ID, assigned agent, or failure message.
- Supported transition events:
  - `planning_started`
  - `subtask_dispatched`
  - `awaiting_approval`
  - `task_completed`
  - `task_failed`
