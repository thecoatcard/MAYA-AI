# Module: Specialist AI Agents Fleet

- **Status:** Implemented
- **Base Framework:** `backend/app/agents/base.py` (`BaseSpecialistAgent`)
- **Fleet Registry:** `backend/app/agents/registry.py` (`AgentFleetRegistry`)
- **Worker Integration:** `backend/app/tasks/worker.py` (`SubtaskWorker`)

## Purpose

The Specialist AI Agents Fleet implements dedicated, task-bounded AI agents engineered for specific business and technical workflows. Each agent operates under strict immutable tool allowlists, permission ceilings, verification proof generation, and clean pausing on human approval gates (Decision D8).

## Agent Specifications

### 1. ResearchAgent (`backend/app/agents/specialized/research.py`)

- **Role:** `RESEARCH`
- **Model:** the `search` alias (`gemini-2.5-flash`, per-model cooldown), then the search fallback alias.
- **Permission Ceiling:** `READ`
- **Assigned Tools:** `web_search`, `google_search`, `fetch_web_page`, `file_read`
- **Verification Proof:** `search_engine`, `citations_count`, `sources` (URLs taken only from Gemini Google Search grounding metadata), `stripped_unverified_citations`, `as_of`; `degraded` and `sources_unavailable` when search was unavailable.
- **Workflow:** Reads only; no state mutation. Brave Search was removed on 2026-11-09; Google Search grounding is the only search backend. The multi-step pipeline (plan, fan-out, synthesis) was replaced on 2026-11-10 by ONE grounded call that carries the user's `USER REQUIREMENTS` block, today's date and the user's language.
  1. **Search:** one `generate_text(enable_google_search=True)` call (an empty answer is retried once). The planner makes at most one research step per task.
  2. **Clean:** markers and URLs that are not in the grounding metadata are removed, citations renumbered, and the `## Sources` list is appended by code.
  3. **Degrade, never fail the task:** if the search alias and its fallback are exhausted (quota) the step completes as `degraded` with "No web sources could be retrieved", no citations and `sources_unavailable`; later document steps then forbid invented figures and say "Prepared without web sources". Only a non-quota error, an empty answer or an answer without grounding returns `FAILED` (non-critical research steps are skipped by the plan).
  Prompts live in `backend/app/agents/prompts/research.py`.

### 2. CodingAgent (`backend/app/agents/specialized/coding.py`)

- **Role:** `CODING`
- **Model Role:** `heavy` (declared; the agent makes no model call itself)
- **Permission Ceiling:** `APPROVAL_REQUIRED`
- **Assigned Tools:** `file_read`, `file_write_artifact`, `git_status`, `git_diff`, `git_commit_local`, `github_create_pull_request`, `run_sandbox_command`
- **Status: disabled in this deployment.** `FleetSupport(allow_coding=False)` (set in `main.py`) makes the worker fail a `coding` subtask before the agent runs, with "The coding agent is turned off in this deployment, so no code was run ...". The sandbox runner executes on the host and is not container-isolated, so model-directed commands must not reach it. Planner-written `repo_dir`, `test_command` and `patch_*` therefore never execute. The class and its tests stay for the day a container sandbox exists.
- **Workflow 3 (when enabled):** reproduce in the sandbox, apply a contained patch, re-run until exit code 0, then pause for approval before opening the pull request. The approval is stored with its binding so the resumed run is verified against it.

### 3. DocumentAgent (`backend/app/agents/specialized/document.py`)

- **Role:** `DOCUMENT`
- **Model Role:** `orchestrator` (the context model call in `dispatch.py`)
- **Permission Ceiling:** `WRITE`
- **Assigned Tools:** `file_read`, `file_write_artifact`, `generate_document`
- **Input:** the model fills a bounded `DocumentSpec` (themes, blocks, slides, sheets) from the task, today's date and redacted prior outputs, in the user's language. Specs are validated before rendering; xlsx is capped at 20 sheets, 5,000 rows and 50 columns.
- **Verification Proof:** files are written only inside `artifacts/{workspace_id}/` (a name that escapes it, including one supplied by the planner, fails with `path_traversal`), with SHA-256, byte count, renderer, theme and verifier checks recorded in `file_artifacts`. Only a verified file is a success; otherwise `FAILED` with the verifier's errors. Timeout 240 s.
- Details: [Document generation](../architecture/DOCUMENT_GENERATION.md).

### 4. EmailAgent (`backend/app/agents/specialized/email.py`)

- **Role:** `EMAIL`
- **Model Role:** `orchestrator` (context model call in `dispatch.py`)
- **Permission Ceiling:** `APPROVAL_REQUIRED`
- **Assigned Tools:** `read_emails`, `gmail_search`, `gmail_read_thread`, `draft_email`, `gmail_create_draft`, `gmail_send`
- **Workflow:**
  - The worker builds the email (one recipient that must appear in the user's request, subject, body) **before** asking for approval, so the approval card shows the real content and binds it; the approved run sends exactly that content.
  - Without a verified token the agent only drafts (reusing an existing `draft_id`) and pauses. With the token it sends directly and records the Gmail message id. `has_valid_approval_token` is set by the worker only.
  - Gmail is called directly with `MAYA_GOOGLE_ACCESS_TOKEN` (not through the MCP gateway; no token refresh). No token: the subtask fails up front with that instruction.
  - Gmail errors become short actionable failures; a failed or timed-out send is never retried automatically. Timeout 60 s.

### 5. PMAgent (`backend/app/agents/specialized/pm.py`)

- **Role:** `PM`
- **Model Role:** `orchestrator` (declared; the agent makes no model call)
- **Permission Ceiling:** `READ`
- **Assigned Tools:** `read_calendar`, `calendar_list_events`, `calendar_get_event`, `notion_query_database`, `notion_get_page`, `file_read`
- **Workflow 1 (Daily Briefing):**
  - Reads Google Calendar events for the requested UTC day (`date`, default today) and the Notion database (`notion_database_id`).
  - One failing source yields a briefing with a visible notice; all sources failing, or none connected, is `FAILED` with what to configure. It never reports "no events" for an unreachable calendar. Timeout 60 s.
  - The Notion database comes from `MAYA_NOTION_DATABASE_ID` (or `notion_database_id` in the step context); without it Notion is skipped with "set MAYA_NOTION_DATABASE_ID in .env and share the database with the integration". A Calendar 401/403 is reported as "token expired" (no refresh flow exists; set a fresh `MAYA_GOOGLE_ACCESS_TOKEN`).
  - **Only for calendar and Notion briefings.** The planner does not give it outlining or writing work, and the worker re-routes such a step to the generic text executor ([self-healing](../architecture/AGENT_REQUEST_FLOW.md#7-self-healing)).
  - Known gap: the day is UTC, not the user's time zone.

See [Agent request flow](../architecture/AGENT_REQUEST_FLOW.md) for the full path from chat message to result, tiers and failure semantics of every agent.

## Lifecycle & Worker Invariants

- **Decision D8:** Exactly 1 subtask per queued job message. `SubtaskWorker` never sleeps or blocks waiting for human approval. Upon encountering an `APPROVAL_REQUIRED` tool, the agent yields `AgentExecutionStatus.AWAITING_APPROVAL`, saves the execution state with the pending `approval_request_id`, and exits cleanly.
- **Verification Proof:** Every completed subtask records a structured `verification_proof` dictionary containing output artifact IDs, test run logs, checksums, or citations.
- **Tool Allowlist Enforcement:** `BaseSpecialistAgent` validates every tool call against `assigned_tools` before dispatch. Any unassigned tool call raises `PermissionCeilingError` immediately.

## Testing

Comprehensive test suites in `backend/tests/`:

- `tests/unit/test_base_agent.py`: Agent role initialization, tool allowlist enforcement, permission ceilings.
- `tests/unit/test_agent_request_flow.py`: email recipient and send gating, pm source failures, document path safety, dispatch prompts, worker availability, approval binding, timeouts.
- `tests/integration/test_sandbox_runner.py`: Decision D14 Docker socket prohibition, non-root execution, timeout, output capping.
- `tests/integration/test_specialist_agents.py`: Individual specialist agent workflows and proofs.
- `tests/integration/test_agent_workflows_e2e.py`: End-to-end integration with `SubtaskWorker`, pause-on-approval, and proof capture.
