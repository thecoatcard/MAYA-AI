# Agents and Orchestrator

The central LangGraph orchestrator and multi-agent coordination framework (Phase 05).

- `state.py`: defines `SubTask` and `AgentExecutionState` models with Pydantic v2 and reducer annotations.
- `planner.py`: decomposes compound instructions into validated Directed Acyclic Graphs (DAGs) using ModelGateway
  (`orchestrator` alias), structural checks, topological sort with Kahn's algorithm, cycle detection (`PlanCycleError`),
  missing/repeated-edge validation, a 190-edge bound, and 1 cycle-repair turn supplied with the prior plan and JSON
  schema.
- `checkpointer.py`: `AsyncPostgresSaver` on a dedicated PostgreSQL connection bound to server-provided workspace and
  user UUIDs; Alembic provisions its RLS-protected tables and runtime setup never creates schema. The `python -m app`
  entrypoint installs and verifies Windows `SelectorEventLoopPolicy` before Uvicorn starts; checkpointer creation
  rejects an incompatible already-running loop rather than trying to change policy late.
- `orchestrator.py`: compiled LangGraph `StateGraph(AgentExecutionState)` with nodes `plan`, `dispatch`, `await_approval`,
  and `synthesize`. A dependency is ready only when both the task's current status is `completed` and its ID appears in
  the completion record. Failed prerequisites do not unblock dependents, and empty or failed plans do not synthesize as
  successful. The graph is not wired into production routes; approval isolation is not verified, and checkpoint owner
  isolation has an offline test plus a PostgreSQL test that has not been run against a live database.

Specification: [docs/architecture/ORCHESTRATOR_SPECIFICATION.md](../../../docs/architecture/ORCHESTRATOR_SPECIFICATION.md).

## Unified and custom agents (ADR-0023)

- `unified.py`: the three agents the planner and UI see (Research, Builder, Operator), the alias table for every old role name, and `resolve_member`, which `plan_repair` uses to turn `builder`/`operator` into one concrete member before anything is stored.
- `custom/`: custom agents defined by `<folder>/.maya/agents/<name>.md`. `definition.py` parses and validates (closed READ-only capability vocabulary, no extra keys), `store.py` reads and writes through SafePath, `runner.py` runs a text-only step with the instructions as untrusted data. API: [docs/api/AGENTS.md](../../../docs/api/AGENTS.md).
