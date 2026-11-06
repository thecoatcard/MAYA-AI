# Tasks

Background tasks and lifecycle event streaming (Phase 05).

- `events.py`: defines `TaskEvent` schema and `TaskEventPublisher` for Redis Pub/Sub channels
  (`maya:events:{workspace_id}`). Publishes state transition events (`task_plan_created`, `subtask_dispatched`,
  `subtask_status_changed`, `task_paused_approval`, `task_completed`, `task_failed`, with backward-compatible aliases
  `planning_started` and `awaiting_approval`). Event types are restricted to the supported enum and
  timestamps must be timezone-aware (normalized to UTC). Helper-supplied payload values cannot override the trusted
  subtask identifiers, assigned agent, status, or failure message.

Specification: [docs/architecture/ORCHESTRATOR_SPECIFICATION.md](../../../docs/architecture/ORCHESTRATOR_SPECIFICATION.md).
