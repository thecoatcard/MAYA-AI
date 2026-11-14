# ADR-0026: Single tool-using chat loop, a job runner for long work, and agents retired in phases

- **Status:** Proposed. The owner approved the direction and Phase 1 in chat on 2026-11-11. This ADR is accepted for Phase 1 only. Phases 2 to 4 need separate approval.
- **Date:** 2026-11-11
- **Deciders:** Repository owner
- **Related:** [ADR-0016](0016-chat-function-calling-and-classifier.md), [ADR-0019](0019-self-healing-task-execution.md), [ADR-0021](0021-agent-key-affinity-and-model-policy.md), [ADR-0023](0023-unified-agents-and-custom-agents.md)

## Context

Current state, verified in code (line numbers as of this change):

- Chat is a streamed model loop with tools (`backend/app/services/chat_service.py:894` `_run`, `:1196` `_tools`). Its tools are `submit_task`, `create_file_inline`, `edit_attached_file`, `create_custom_agent`, `save_memory`, `generate_image`, read-only GitHub and Notion tools, and `show_code` (`chat_service.py:264-310`, `inline_files.py:39-40`, `image_gen.py:86`).
- A deterministic router (`services/chat_router.py:194` `route_message`) decides the route before the model runs. Inline file creation is on by default (`core/config.py:195` `chat_inline_files = True`).
- Inline rounds are capped at 4 (`MAX_INLINE_ROUNDS`, `chat_service.py:396`). GitHub read rounds are capped at 14 (`MAX_REPO_ROUNDS`, `:291`). The reply deadline is 600 s (`core/config.py:187`).
- Any task that goes to the background goes through `services/task_submission.py` (`_plan` at `:409`, which calls `DAGPlanner` at `:434`). `DAGPlanner.plan` (`agents/planner.py:456`) builds the whole step list up front. `build_subtasks` (`planner.py:345`) runs `repair_plan` (`plan_repair.py:79`) at plan time, not while steps run.
- Workers execute the planned steps through deterministic specialists (`tasks/dispatch.py:3`). I found no re-planning path in `tasks/worker.py` (2078 lines; not read in full).
- Specialist agents are `agents/specialized/{coding, document, email, pm, project_writer, research}.py`. Three unified roles (`agents/unified.py:19-22`: research, builder, operator, plus executor) sit on top of them.
- Voice has its own registry (`voice/tools.py:110`). Six document renderers live in `automation/`. Preview (`automation/preview.py:168`) and the visual loop (`automation/visual_review.py:642`) exist; the vision check depends on an operator-set alias and is reported as "not verified" (`visual_review.py:581-597`).

Problems:

1. The planner fixes steps before any work runs. Workers do not reason again about what they find, so a wrong early step is repaired only by fixed rules.
2. Most file requests become background tasks, even when a short inline answer would do.
3. Agents add a layer the user does not need. The user asks for a file; the system answers with a plan, a task and a set of agents.
4. Chat and voice cannot do live work (search, read a page, check a Notion page, then answer) inside one reply, except through the bounded GitHub and Notion read tools.

## Decision

1. **One tool-using chat loop is the primary path.** The model decides, per turn, whether to answer, call a tool or start a job. The deterministic router stays as a pre-filter that limits the tool set per route (see Guardrails).
2. **Tools** cover documents, files, web search, Notion, GitHub, email (with approval) and images. Each tool wraps an existing engine or connector (`backend/app/automation/*`, `backend/app/mcp/connectors/google_search.py`, `backend/app/mcp/notion_tools.py`, `backend/app/mcp/github_repo_tools.py`, `backend/app/agents/specialized/email.py`).
3. **Long work runs in a job runner** that executes the same tool functions, not a separate agent graph. A job has a durable state, a list of steps and a result. Jobs are the replacement for the task ledger.
4. **Per-conversation working state** holds the facts a turn needs (current document, attached files, open jobs) so the model does not re-derive them on each round.
5. **Agents are retired in phases.** Their behaviour moves into tools and jobs. The classes stay only as internal helpers until Phase 4 removes them.

Names below are proposals. None of them exists today. `create_document` is currently a Python function (`automation/docx_engine.py:585`) and not a chat tool.

## Phases and exit criteria

**Phase 1 (accepted).** Behind `MAYA_CHAT_TOOL_LOOP`, default off.
- Tools: `create_document`, `preview_document`, `start_job`, `check_job`. Working state per conversation.
- `preview_document` uses a vision check only if the owner confirms an alias and image input is verified. Until then it returns the deterministic checks only.
- Tests: unit tests for each tool, the round and budget caps, and the flag-off path (unchanged behaviour).
- Exit: all tests pass; a scripted set of 20 small file requests answered inline with the flag on; the flag-off suite unchanged.
- Rollback: set the flag off. No data migration is expected. If working state needs a new table, Phase 1 stops for owner approval (see Guardrails).

**Phase 2.** Research, Notion, GitHub and email tools in the loop.
- Exit: tool tests for each; email sends only after explicit approval; no regression in the existing read-only GitHub and Notion behaviour.
- Rollback: remove the tools from the allowlist; the Phase 1 tools stay.

**Phase 3.** Planner off for new requests, behind a flag. The task ledger becomes the job list.
- Exit: the job list shows the same states as the ledger for the existing task test suite. Existing tasks load.
- Rollback: the flag returns new requests to the planner. This phase may need a database change; if so, it stops for owner approval.

**Phase 4.** Remove the agent classes.
- Exit: the unit and integration tests pass, and real Gemini runs of the eval set pass (the eval set does not exist yet; see Risks).
- Rollback: restore the previous commit. This is the only phase that cannot be undone cheaply, so it needs a separate approval.

## Guardrails

- Side effects keep their approval. Email, Notion writes and GitHub writes need explicit approval, as today (`mcp/permissions.py`, `tasks/saga.py`).
- A tool allowlist per route. The router decides the set before the model sees any tool.
- Bounded rounds and budget. The existing caps stay (4 inline rounds, 14 read rounds). The job runner adds a token and time budget per job.
- Secrets never go into prompts or logs. Connection tokens are resolved server-side (ADR-0024).
- Content from files, pages and connectors is data, not instructions.
- **Database:** no migration without owner approval. Phase 1 may need a table for working state; Phase 3 may need one for the job list. Each will stop and ask before the migration is written.

## Metrics

- Share of small requests answered inline (target to be set after Phase 1 baseline).
- Time from request to first file.
- Self-repair rate (tool errors that the loop fixed without the user).
- Task rate (share of requests that became background jobs).
- Cost per file (tokens and model calls).
- Failure classes, counted by code (not free text).

## Alternatives considered

- **Keep the planner and improve repair:** this is the current state. It fixes plans before work runs and does not let workers reason again. Rejected as the main direction.
- **A single model with no jobs:** rejected. Work over the 600 s reply deadline needs a durable job with its own state.
- **Several chat agents that hand off:** rejected. It keeps the layer the user does not need.

## Consequences

- Positive: fewer moving parts in the common path; live work in chat and voice; one tool set for chat and jobs.
- Negative: model reliability in loops is untested here; cost and latency per reply may rise; quality may fall without the planner; approval wording must stay clear.
- Mitigations: the flag keeps the old path; round and budget caps; a fixed eval set before Phase 4; approval prompts show the exact action.

## Security and compliance impact

Trust boundaries: model output calling tools, and tools calling external services (search, Notion, GitHub, email). Side-effect approvals stay as they are. Logs record tool names and codes, not content or secrets. This ADR adds no new credential path.

## Implementation notes

- Phase 1 code location is not yet decided. The likely home is `services/chat_service.py` and a new module next to `services/inline_files.py`.
- The eval set and the vision alias are open items for the owner.
- Unverified: the worker's full execution path (not read end to end); whether any task path already calls `enable_google_search` outside `agents/specialized/research.py:283`.

## Review trigger

Revisit if the Phase 1 eval shows the inline share or self-repair rate below target for two consecutive runs, or if the model's tool-call error rate exceeds the failure budget agreed in Phase 1.
