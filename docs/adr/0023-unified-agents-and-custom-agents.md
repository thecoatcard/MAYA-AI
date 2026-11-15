# ADR-0023: Three unified agents and folder-defined custom agents

- **Status:** Proposed (owner request, 2026-11-10)
- **Date:** 2026-11-10
- **Deciders:** Repository owner (approval pending)
- **Related:** [ADR-0011](0011-mcp-gateway-and-specialist-agents.md), [ADR-0019](0019-self-healing-task-execution.md), [ADR-0022](0022-connected-folder-and-uploads.md), [Agents API](../api/AGENTS.md), [Tool permissions](../../governance/TOOL_PERMISSIONS.md)

## Context

The planner offered eight roles (research, document, email, pm, coding, executor, file_edit, project), several of them one thin wrapper per tool, and the model often picked the wrong one (a `pm` step asked to outline slides, a `document` step used for an edit). Owner request: remove unnecessary agents, merge same-category agents into clearer unified agents, and let a user with a connected working folder ask MAYA for custom agents, like Codex or Copilot custom agents.

Findings from the code that shape the decision:

- The persisted role set is the `SubtaskAgent` enum (a non-native string column) and the worker, healing, dispatch and key pool all key on the member names. The planner already uses a vocabulary of its own that `build_subtasks` maps to persisted roles (`executor`, `file_edit`, `project` are stored as `file` plus `input_data.executor`).
- Every member has an immutable tool allowlist and permission ceiling (`BaseSpecialistAgent`). These are the safety boundary and must not be merged away.
- `coding` is disabled in this deployment.

## Decision

### Part 1: three unified agents (a facade over unchanged members)

| Unified agent | Members (old names stay as aliases) | Capabilities |
| :------------ | :---------------------------------- | :----------- |
| Research | research, browser | grounded web search with citations, page reading, GitHub repository listing |
| Builder | document, project, file_edit, coding (disabled) | pdf/docx/pptx/xlsx/md files, static websites, edits of attached or folder files as new versions |
| Operator | email, pm | email (always approved), Calendar/Notion briefing, GitHub |

The planner sees `research`, `builder`, `operator` and `executor` (the tool-less text step). `builder` and `operator` carry an optional `input_data.kind`; `plan_repair` resolves them to one concrete member in code (`app/agents/unified.py`, `resolve_member`) before anything is stored, so stored tasks, the enum, the worker, healing, dispatch and the key pool are unchanged and no migration is needed. A model-given `kind` outside the agent's own members is ignored. Old role names keep working everywhere. The capability table in the planner prompt, the Agents API and the Settings UI group by the three agents.

Rejected: physically merging the member classes. It would weaken the per-member allowlists and ceilings, change stored roles (a migration) and touch files other work is changing.

### Part 2: custom agents are files in the connected folder

- A custom agent is `<folder>/.maya/agents/<name>.md`: a small header (`name`, `description`, `capabilities`, optional `model`, `enabled`) and the instructions as the body. No database: files are the storage.
- **Permission subset.** `capabilities` come from a closed vocabulary, `web_search` and `read_files`, each mapping to READ-tier tools only (asserted at import). A capability must be inside what the user already has (web search must be configured). Unknown header keys (`tools`, `approval`, ...) make the file invalid. A custom agent therefore cannot gain a tool, a write, a send or a way round approvals, and has no tool that needs approval.
- **Instructions are data.** They are placed in a delimited, untrusted block of the prompt under a fixed system instruction; fence markers are stripped; control and bidi characters are removed; length is capped. They shape focus and style only.
- **Validated twice.** At listing (an invalid file is shown with its reason and never loaded) and again at run time, so a file edited after planning cannot widen what runs.
- **Paths.** Every read and write goes through `FileContext.resolve` (SafePath plus MAYA's protected directories); names are `[a-z][a-z0-9-]{1,38}[a-z0-9]`, built-in role names are reserved, links are never followed, files over 16 KB are refused.
- **Approval for the write.** Creating or editing a file happens only after the client previews the exact text (`/custom/preview`) and sends `confirm: true`; create uses exclusive creation (never overwrites), edit keeps the previous text as `<name>.md.bak`, nothing is deleted (disable is an `enabled: false` edit). Writes are audited.
- **Use.** By name (`@name ...`, `use name to ...`: one step, no planner call) or by the planner, which is shown the enabled custom agents and may assign `custom:<name>` (unknown names become a text step). A custom step is stored as `file` plus `input_data.executor = "custom"`, like the other folder steps.

## Consequences

- Fewer, clearer roles for the planner and the UI; no schema or stored-data change.
- Custom agents are text agents with read-only capabilities in this version. Agents that write files, send email or run code are deliberately not possible; they would need a new ADR and approval design.
- **Chat.** The chat model has a `create_custom_agent` tool (offered when file features are installed), so "create an agent for X" in any wording or language drafts one through the same draft service as `POST /api/v1/agents/custom/draft`. The reply carries an `agent.draft` event (stored in message metadata, no migration) and the thread shows the exact file with Approve, Edit and Cancel; Approve calls the confirmed create endpoint, so the 428 rule still holds. With no connected folder the tool returns `needs_folder` and the model asks the user to connect one. The voice task planner receives the user's enabled custom agents and honours calling one by name.
- Limits: recognising the request relies on the chat model calling the tool (there is no keyword fallback); a draft card that was approved earlier shows again after a reload, and approving it then reports that the agent already exists.
