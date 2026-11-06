# Tasks

Background tasks and lifecycle event streaming (Phase 05).

- `events.py`: defines `TaskEvent` schema and `TaskEventPublisher` for Redis Pub/Sub channels
  (`maya:tasks:{workspace_id}`). Publishes state transition events (`planning_started`, `subtask_dispatched`,
  `awaiting_approval`, `task_completed`, `task_failed`). Event types are restricted to the supported enum and
  timestamps must be timezone-aware (normalized to UTC). Helper-supplied payload values cannot override the trusted
  subtask identifiers, assigned agent, or failure message.

Specification: [docs/architecture/ORCHESTRATOR_SPECIFICATION.md](../../../docs/architecture/ORCHESTRATOR_SPECIFICATION.md).
