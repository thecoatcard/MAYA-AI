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
- **Model Role:** `heavy` / `orchestrator`
- **Permission Ceiling:** `READ`
- **Assigned Tools:** `brave_search`, `notion_get_page`, `notion_query_database`
- **Verification Proof:** Verifiable source citations containing `title`, `url`, `snippet`, and extraction timestamp.
- **Workflow:** Collects external data without state mutations. Sanitizes untrusted content before synthesizing findings.

### 2. CodingAgent (`backend/app/agents/specialized/coding.py`)

- **Role:** `CODING`
- **Model Role:** `heavy`
- **Permission Ceiling:** `APPROVAL_REQUIRED`
- **Assigned Tools:** `filesystem_read_file`, `filesystem_write_file`, `filesystem_list_directory`, `git_status`, `git_diff`, `git_branch`, `git_commit_local`, `git_push`, `github_get_issue`, `github_create_pull_request`, `sandbox_execute`
- **Workflow 3 (Autonomous Bug Fix):**
  1. Clones/isolates workspace in ephemeral sandbox.
  2. Runs failing reproduction test inside Docker Sandbox Runner (non-root, `--network none`).
  3. Writes patch and re-executes tests in sandbox until exit code 0 is verified.
  4. Commits locally (`git_commit_local`).
  5. Requests human approval for `github_create_pull_request` and `git_push`.
  6. Subtask pauses cleanly with `AWAITING_APPROVAL` status and git diff proof.

### 3. DocumentAgent (`backend/app/agents/specialized/document.py`)

- **Role:** `DOCUMENT`
- **Model Role:** `orchestrator`
- **Permission Ceiling:** `WRITE`
- **Assigned Tools:** `filesystem_read_file`, `filesystem_write_file`, `filesystem_list_directory`, `document_create_pdf`, `document_create_docx`, `document_create_xlsx`, `document_create_pptx`
- **Verification Proof:** Output files generated strictly inside `/artifacts/{workspace_id}/` with cryptographic SHA-256 checksum and byte count recorded in `file_artifacts` database table.

### 4. EmailAgent (`backend/app/agents/specialized/email.py`)

- **Role:** `EMAIL`
- **Model Role:** `orchestrator`
- **Permission Ceiling:** `APPROVAL_REQUIRED`
- **Assigned Tools:** `gmail_search`, `gmail_read_thread`, `gmail_create_draft`, `gmail_send`
- **Workflow:**
  - Reading threads and searching mail operates autonomously (`READ`).
  - Drafts are created autonomously and stored in Gmail (`gmail_create_draft` -> `WRITE`).
  - Sending mail (`gmail_send`) is strictly gated by human approval (`APPROVAL_REQUIRED`). Requires RFC 8785 canonical hash matching and CAS-consumed approval token.

### 5. PMAgent (`backend/app/agents/specialized/pm.py`)

- **Role:** `PM`
- **Model Role:** `orchestrator`
- **Permission Ceiling:** `READ`
- **Assigned Tools:** `calendar_list_events`, `calendar_get_event`, `notion_get_page`, `notion_query_database`
- **Workflow 1 (Daily Briefing):**
  - Fetches today's agenda from Google Calendar.
  - Fetches pending action items and sprint status from Notion.
  - Synthesizes structured daily executive briefing with meeting schedule, deadlines, and action items. Operates purely under `READ` ceiling.

## Lifecycle & Worker Invariants

- **Decision D8:** Exactly 1 subtask per queued job message. `SubtaskWorker` never sleeps or blocks waiting for human approval. Upon encountering an `APPROVAL_REQUIRED` tool, the agent yields `AgentExecutionStatus.AWAITING_APPROVAL`, saves the execution state with the pending `approval_request_id`, and exits cleanly.
- **Verification Proof:** Every completed subtask records a structured `verification_proof` dictionary containing output artifact IDs, test run logs, checksums, or citations.
- **Tool Allowlist Enforcement:** `BaseSpecialistAgent` validates every tool call against `assigned_tools` before dispatch. Any unassigned tool call raises `PermissionCeilingError` immediately.

## Testing

Comprehensive test suites in `backend/tests/`:

- `tests/unit/test_base_agent.py`: Agent role initialization, tool allowlist enforcement, permission ceilings.
- `tests/integration/test_sandbox_runner.py`: Decision D14 Docker socket prohibition, non-root execution, timeout, output capping.
- `tests/integration/test_specialist_agents.py`: Individual specialist agent workflows and proofs.
- `tests/integration/test_agent_workflows_e2e.py`: End-to-end integration with `SubtaskWorker`, pause-on-approval, and proof capture.
