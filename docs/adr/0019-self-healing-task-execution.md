# ADR-0019: Self-healing task execution

- **Status:** Proposed (owner request, 2026-11-09)
- **Date:** 2026-11-09
- **Deciders:** Repository owner (approval pending)
- **Related:** [ADR-0012](0012-in-process-task-queue.md), [ADR-0017](0017-themed-document-generation.md), [Agent request flow](../architecture/AGENT_REQUEST_FLOW.md#7-self-healing), [Specialist agents](../modules/SPECIALIST_AGENTS.md)

## Context

The owner asked for a task "compare the top 5 electric scooters in India, make a 2-page PDF and an 8-slide deck". Research and documents were fine, but the task failed three times for reasons that were not the user's: the planner gave "Outline the 8-slide presentation structure" to the `pm` agent (which only briefs from Google Calendar and Notion, so it failed `all_sources_failed`); the planner invented a `save_file` tool for a step the document agent had already done (denied by default); and the model returned document blocks without a `type`, which the strict discriminated union rejected ("Unable to extract tag using discriminator 'type'"). Calendar 401 (static expired token) and Notion 404 (no database id) were genuine configuration problems. Owner request: "if things are not in order it should go another way: add a self-healing method".

## Decision

Heal in layers, never around a safety rule.

1. **Prevent bad plans.** The planner prompt carries the real catalogue of roles and tools. Before a plan is stored, `repair_plan` remaps unknown roles to a new planner role `executor`, drops unknown tools, removes redundant save/export steps (rewiring dependents), remaps `pm` used for content work, and drops invalid dependencies. Cycles are still rejected after one model repair call.
2. **Alias table.** A small `TOOL_ALIASES` map resolves synonyms of `file_write_artifact` and is classified with the target's tier, only for READ/WRITE targets. Unknown tools stay denied by default.
3. **Recover at run time.** For `unclassified_tool`/tool-not-provided, `agent_unavailable` and `all_sources_failed`, the worker skips a redundant save step, retries without the tool, or re-routes a content step to the plain text executor. At most two hops per step, each recorded as `{attempt, from, to, reason}` and shown to the user. Steps that need an integration (email, calendar, Notion, GitHub) are not re-routed and fail with the actionable message.
4. **Dependency tolerance.** The planner marks steps `critical` (default: deliverables, leaves, research). A non-critical failure lets the task continue; dependents get the input marked missing; the task ends `completed` with `warnings`. A critical failure fails the task and keeps the first real cause.
5. **Documents.** The model-facing schema is flat; the spec is normalised, repaired once by the model, and finally rebuilt from the research outputs, with the healing recorded in the proof.
6. **Never healed:** approval gates and approved actions, RESTRICTED or approval-tier tools, credential errors for side-effect actions, budget exhaustion, user abort. Healing never changes what the user approved.

Two storage decisions avoid a migration: `executor` is persisted as the existing `file` role with `input_data.executor = "text"`, and the final state is `completed` plus `shared_context.warnings` (and a skipped step is `completed` with `skipped: true`) because `tasks.status` and `subtasks.status` have CHECK constraints.

## Consequences

- Positive: the owner's task completes with both files; a wrong agent or tool no longer kills a task; what was healed is visible (step text, proof, events, task API).
- Negative: heuristics (keyword rules) decide "content work" and "needs an integration"; a misjudged step fails honestly or is re-routed to a plain LLM step. The planner role `executor` and the stored `file` role are different names for one thing. A non-critical failure can produce a deliverable built from less input (it is flagged).
- Needs owner approval later: a migration for real `completed_with_warnings` and `skipped` statuses, if wanted.
- Not tested against real Gemini: whether the model follows the flat block schema, the repair call, and the planner's use of the new catalogue are unverified.

## Alternatives considered

Letting the worker rewrite the plan with a model call (non-deterministic, harder to bound); auto-approving or lowering tiers for aliases (rejected: aliases only rename); a new database status (needs a migration); retrying a failed pm step unchanged (it can never succeed without credentials).
