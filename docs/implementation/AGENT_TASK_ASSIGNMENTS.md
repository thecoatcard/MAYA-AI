# MAYA AI — Multi-Agent Task Assignment and Collaboration Protocol

## 1. Overview and Multi-Agent Operating Model

MAYA AI is engineered to be implemented collaboratively by multiple autonomous AI coding tools (Claude Code, GitHub Copilot, Google Antigravity, OpenAI Codex, Cursor, and Gemini CLI) paired with human software engineers.

To prevent architectural drift, conflicting abstractions, race conditions on shared files, and unauthorized security bypasses, all agents must operate under the canonical policies established in [`AGENTS.md`](../../AGENTS.md) and adhere to the task assignment protocol defined in this document.

---

## 2. Agent Specialization Matrix

Each coding agent brings distinct strengths to the project lifecycle:

| Agent Tool                  | Recommended Role                                    | Primary Phase Assignments     | Key Responsibilities                                                                                                                    |
| :-------------------------- | :-------------------------------------------------- | :---------------------------- | :-------------------------------------------------------------------------------------------------------------------------------------- |
| **Claude Code**             | Principal Software Architect & Backend Systems Lead | Phases 00, 02, 05, 06, 07, 15 | Core architecture, LangGraph workflows, database RLS policies, cryptographic approval gate, security hardening, complex pytest suites   |
| **Google Antigravity**      | AI Infrastructure & Multimodal Real-Time Lead       | Phases 01, 03, 11, 12, 16     | Gemini API Model Gateway, Gemini Live WebSocket audio streaming, pgvector embeddings, context caching, Docker and cloud operations      |
| **GitHub Copilot / Cursor** | Frontend Systems & UI Component Lead                | Phases 04, 10, 14             | Next.js 15 App Router components, Tailwind CSS styling, AudioWorklet client capture, Deliverable Canvas viewers, Playwright UI tests    |
| **OpenAI Codex**            | Data Modeler, Automation & Integration Lead         | Phases 01, 08, 09, 10, 13     | Pydantic data schemas, Alembic migration scripts, MCP server connectors, document generator engines (openpyxl, python-docx), unit tests |
| **Gemini CLI**              | Fast Verification & Toolchain Integration           | Phases 00, 01, 15             | Automated CLI execution, linter checks, test suite validation, script verification                                                      |

---

## 3. Shared Task Assignment Template

When initiating work on any task, the assigned agent or human developer must generate a task assignment record conforming to this template:

```markdown
### Task Execution Record: [TASK-ID] — [Task Name]

- **Assigned Agent:** [e.g. Claude Code / Google Antigravity / GitHub Copilot]
- **Role:** [e.g. Backend Systems Engineer / Security Specialist]
- **Implementing Phase:** [e.g. Phase 07 — Security, Permissions, and Human Approval]
- **Prerequisites Verified:**
  - [ ] Upstream phase exit gates confirmed passed
  - [ ] Relevant documentation and ADRs read
  - [ ] Canonical `AGENTS.md` hard rules confirmed
- **Files to Modify / Create:**
  - Create: `backend/app/...`
  - Test: `backend/tests/...`
  - Docs: `docs/...`
- **Concurrency Locks:**
  - Exclusive Lock: [Files that no other agent may edit concurrently]
- **TDD Workflow:**
  1. Write failing test: `pytest ...` (Assert FAIL)
  2. Implement minimal code to pass test
  3. Run verification test: `pytest ...` (Assert PASS)
  4. Run fast validation: `npm run validate:fast`
- **Completion Handoff Artifact:**
  - Test command output: [Exact command and passing result]
  - Documentation updated: [Path to updated docs]
  - Commit message format: `feat(scope): concise description`
```

---

## 4. Concurrency Collision Prevention Rules

To prevent merge conflicts and overwrites when multiple agents work in parallel:

### Rule 1: Single File Ownership During Task Execution

Only one agent may hold write locks on a specific file at any given time. If Agent Alpha is modifying `backend/app/main.py`, Agent Beta must not touch `backend/app/main.py` until Agent Alpha has completed their task, committed the change, and passed validation.

### Rule 2: Files Strictly Banned from Concurrent Modification

The following files must **NEVER** be edited concurrently by multiple agents under any circumstances:

- `MAYA_AI_FINAL_PLAN.md` (Immutable specification).
- `AGENTS.md` (Canonical policy file; requires explicit human approval trailer).
- `package.json` / `package-lock.json` (Requires dependency lock).
- `backend/pyproject.toml` (Requires dependency lock).
- `backend/app/db/alembic/versions/*` (Migration scripts must be generated sequentially with single-parent head).
- `backend/app/main.py` (Central router registration; must be updated sequentially).
- `governance/governance.config.json` (Sensitive validator config).

### Rule 3: Isolation by Subsystem Directory

Parallel agents must operate in distinct functional directories:

- **Agent 1:** Working exclusively in `backend/app/gateway/` (Phase 03).
- **Agent 2:** Working exclusively in `backend/app/db/alembic/` (Phase 02).
- **Agent 3:** Working exclusively in `apps/web/src/components/chat/` (Phase 04).

---

## 5. Mandatory Agent Execution Steps

Every agent executing a task in this repository must follow these 10 steps in exact order:

1. **Read Instructions:** Read [`AGENTS.md`](../../AGENTS.md) and the assigned phase specification in `docs/implementation/IMPLEMENTATION_PHASES/`.
2. **Verify Prerequisites:** Ensure upstream tasks and environment dependencies are active.
3. **Inspect Existing Code:** Check existing implementations, interfaces, and shared utilities before writing new code.
4. **Define Contracts:** Confirm Pydantic schemas, database models, and API interfaces before implementing logic.
5. **Write Tests First (TDD):** Create failing unit or integration tests verifying the desired behavior.
6. **Implement Minimal Code:** Implement the smallest correct change satisfying the tests.
7. **Run Local Validation:** Execute `npm run validate:fast` and the specific pytest/build command.
8. **Update Documentation:** Update corresponding module documentation, API specs, and ADRs in the same change set.
9. **Review Diff:** Check `git diff` to ensure no accidental whitespace changes, secrets, or unrelated edits were introduced.
10. **Report Honestly:** Report exact files modified, test commands executed with real output, and any remaining risks or dependencies.

---

## 6. Integration, Review, and Handoff Protocol

### Handoff Between Phases

When a phase reaches completion:

1. The lead agent runs the full test suite and verifies all criteria in **Section I (Definition of Done)** and **Section J (Phase Exit Gate)** of the phase document.
2. The agent executes `npm run validate` to ensure whole-repository integrity.
3. The agent publishes a Phase Completion Report updating [`IMPLEMENTATION_STATUS.md`](IMPLEMENTATION_STATUS.md) and unlocking downstream phases.
4. Human review is requested prior to initiating dependent critical-path tasks.
