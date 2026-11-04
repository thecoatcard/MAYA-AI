# MAYA AI — FINAL PROJECT PLAN
### Autonomous, voice-first digital employee — consolidated from Master Specification v1.0 and Technical Specification v2.0

| | |
| :--- | :--- |
| **Plan version** | 3.0 (final consolidated) |
| **Date** | 2026-11-05 |
| **Status** | Supersedes `MAYA_AI_TECHNICAL_MASTER_SPECIFICATION.md` (v1.0) and `MAYA_AI_TECHNICAL_SPECIFICATION_v2.0.md` (v2.0). Both were removed after this plan was written; everything worth keeping is carried below. |
| **Base document** | v2.0 (it already contains a design audit of v1.0). v1.0 content that v2.0 dropped has been restored where it was still valid. |
| **Evidence convention** | **[V]** = checked against a live source on 2026-11-05. **[S]** = taken from the specs as written, not independently checked. **[D]** = a design decision made in this plan. |

---

## 0. How to read this plan

1. §1 – the 14 decisions that define the final plan.
2. §2 – what was verified, and what was found wrong in the old specs.
3. §3–§16 – the architecture and design, in final form.
4. §17–§19 – workflows, repo layout, and the **22-week roadmap with exit criteria**.
5. §20–§24 – MVP scope, testing, deployment, cost, risks.
6. §25 – open decisions that need an owner before or during Phase 1.

---

## 1. Decision register

| # | Decision | Why |
| :-- | :--- | :--- |
| **D1** | v2.0 is the base; v1.0 material is restored only where v2.0 dropped something still needed (cost model, testing strategy, deployment/observability, memory purge, PII scrubbing, MCP resiliency, CAPTCHA policy, `mcp_servers` table, UI screen inventory). | v2.0 is the later, audited revision. |
| **D2** | **Modular monolith** (FastAPI + Celery workers + Postgres + Redis), not microservices, for the MVP. | Same as v2 ADR-001: fewer network hops, one deploy unit, package boundaries keep a later split cheap. |
| **D3** | **Voice and task execution are separate runtimes** joined by a Redis event bus. The voice model gets exactly two tools: `submit_task` and `get_task_status`. It never holds MCP tools. | Long tasks must not block the audio stream; the 15-minute Live session limit [S] must not kill running work. |
| **D4** | **Four permission tiers** (READ / WRITE / APPROVAL_REQUIRED / RESTRICTED), **deny-by-default**. Tier is set by an admin-owned registry, never by the LLM and never by server-supplied tool hints (hints may only *raise* a tier). | v2 four-tier model + closes the "server says it's read-only" hole. |
| **D5** | **Approvals are single-use signed tokens bound to a hash of the exact payload, and in the MVP they can only be granted from the authenticated UI.** Voice can announce, read back, and *deny/cancel* — it cannot *approve*. | v2 contradicted itself (AUD-03 says verbal PIN, §9.2 says semantic confirmation). A spoken PIN can be overheard or replayed; denial by voice is always safe. Revisit when speaker verification exists. |
| **D6** | **One source of truth for execution state:** the LangGraph Postgres checkpointer. `tasks`/`subtasks` tables are queryable projections written from it. | v2 had both a `checkpoint_state` column and LangGraph checkpoints — two truths drift. |
| **D7** | **Memory: three storage tiers, four categories.** Redis (working), Postgres (relational prefs + pgvector for project / episodic / knowledge). The cold S3/Parquet archive is deferred. Every query is filtered by `workspace_id` **and** `user_id`. | v2's six tiers over-build the MVP; its tenant-filter rule (AUD-09) is kept. |
| **D8** | **Celery** is the default job runner (both specs' repo layouts use it) with a hard rule: *a worker never waits on a human, and each subtask is its own short job.* ARQ is the named alternative; a Phase-1 spike decides. | Celery+Redis redelivers unacknowledged jobs after the visibility timeout (default 1 h) — fatal for "2-hour tasks" with side effects. Backend is asyncio; Celery prefork is not. |
| **D9** | **Single-node Redis with AOF persistence** for the MVP; Redis Cluster is post-MVP. | v1/v2 specified a cluster with no workload that needs it. |
| **D10** | **Roadmap is 22 weeks**, reordered so governance (approvals/audit) lands **before** any write-capable tool is exposed, and a memory phase exists. | v2's roadmap had no memory phase and delivered approvals in week 16–17 after write tools in week 7–9. |
| **D11** | **MCP catalogue replaced** (§9.2). Servers are pinned to exact versions and baked into images; no `npx -y` / `uvx` fetch at runtime. | Most "official" package names in both specs are deprecated or do not exist [V]. |
| **D12** | **NFRs:** voice target 450–600 ms, **SLA p95 ≤ 850 ms**; barge-in ≤ 150 ms; MVP availability **99.9 %** (99.95 % + multi-AZ is post-MVP). | Reconciles v1 (hard 600 ms) with v2 (target vs SLA). |
| **D13** | **Model IDs live in config as role aliases** (`orchestrator`, `heavy`, `lite`, `live`, `embed`, …) with fallback chains, and are checked against `models.list` at startup. | Avoids hard-coded IDs (AUD-08); IDs were verified once, but models churn. |
| **D14** | **Docker socket is never exposed to an agent.** Code execution goes through a fixed-function *Sandbox Runner* service with a narrow API (image allow-list, CPU/mem/time/network limits). | Specs listed a generic "Docker MCP: container_run" over the host socket, which is host-root for anyone who can steer the model. |

---

## 2. Verification results and defects found in the old specs

### 2.1 What was checked (2026-11-05)

| Claim in specs | Result |
| :--- | :--- |
| Gemini model IDs: `gemini-3.8-live`, `-live-extended-thinking`, `gemini-3.8-flash`, `gemini-4-argon`, `gemini-3.5-flash-lite`, `deep-research-preview-04-2026`, `antigravity-preview-09-2026`, `gemini-3.8-flash-tts`, `gemini-3.5-transcribe-live`, `gemini-embedding-2` | **[V] All 10 appear on Google's model page.** `gemini-embedding-001` also exists. |
| `gemini-embedding-2` outputs 768-dim vectors | **[V] 768 is supported and recommended** (range 128–3072). Max input 8,192 tokens. The request parameter name was not confirmed on the page — confirm in the SDK. |
| `google-genai >= 2.30.1` | **[V]** Latest on PyPI is **2.34.0**. |
| `mcp >= 1.2.0` (Python SDK) | **[V]** Latest is **2.3.0** — a major version above the floor. Pin an exact version; do not use an open `>=`. |
| LangGraph | **[V]** latest 1.2.19. |
| Pricing, context windows, Live-API 15-minute session limit, rate limits | **[S] Not verified.** Re-check before budgeting (§23) and in the Phase-1/5 spikes. |

### 2.2 MCP catalogue — verified against npm / PyPI / GitHub

| Spec's package | Reality [V] | Final choice |
| :--- | :--- | :--- |
| `@modelcontextprotocol/server-github` | **Deprecated** ("no longer supported") | GitHub's own `github/github-mcp-server` (repo exists) |
| `@modelcontextprotocol/server-git` | **Does not exist on npm** | `mcp-server-git` (PyPI, v2026.10.28) |
| `@modelcontextprotocol/server-notion` | **Does not exist** | `@notionhq/notion-mcp-server` (v2.6.1) |
| `@modelcontextprotocol/server-google-drive` | **Does not exist** (`server-gdrive` exists but is deprecated) | In-house Google adapter (Gmail / Calendar / Drive) |
| `@modelcontextprotocol/server-slack` | **Deprecated** | Not selected — post-MVP |
| `@modelcontextprotocol/server-postgres` | **Deprecated** | Not needed for MVP; if needed later, in-house adapter on a read-only DB role |
| `@modelcontextprotocol/server-playwright` | **Does not exist** | `@playwright/mcp` (v0.0.84) — post-MVP |
| `@modelcontextprotocol/server-brave-search` | **Deprecated** | `@brave/brave-search-mcp-server` (v2.1.4) |
| `@atlassian/mcp-server-jira` | **Does not exist** | Not selected — post-MVP |
| `mcp-server-linear` | Exists (v1.6.0, community) | Vet source before use — post-MVP |
| `@modelcontextprotocol/server-filesystem` | Exists, not deprecated (v2.1.0) | Use (MVP) |

### 2.3 Internal defects and how this plan resolves them

| # | Defect in the specs | Resolution |
| :-- | :--- | :--- |
| 1 | v2 change-log and AUD-06 claim `idempotency_keys` and `task_checkpoints` tables, a `tenant_id` column, and RLS "across all tables". The DDL has none of them (only an `idempotency_key` column, `workspace_id` on 5 of 10 tables, **no RLS policies**). | §14: complete DDL with `workspace_id` everywhere and actual RLS policies (D6 removes the need for `task_checkpoints`). |
| 2 | `tasks.idempotency_key` is **globally** unique — tenants can collide. | `UNIQUE (workspace_id, idempotency_key)`. |
| 3 | `tool_executions.user_id … ON DELETE CASCADE` — deleting a user deletes the "immutable" audit trail; also conflicts with GDPR purge semantics. | Separate `audit_log` with no FK cascade, hash-chained, `UPDATE/DELETE/TRUNCATE` revoked; user purge pseudonymises `user_ref`. |
| 4 | `file_artifacts.verification_status DEFAULT 'verified'` — unverified files would read as verified, contradicting "never claim success without proof". | Default `'pending'`; only the verifier can set `'verified'`. |
| 5 | "Hybrid search with `tsvector`" but both DDLs index with `pg_trgm` (trigram), which is not full-text search. | Generated `tsvector` column + GIN index (trigram kept for fuzzy match). |
| 6 | v2 dropped the `mcp_servers` table, `memory_items.expires_at`, and the memory purge endpoint that v1 had. | Restored. |
| 7 | v2's roadmap has **no memory phase**, though pgvector memory is in the MVP scope. | Phase 7 (§19). |
| 8 | v2 puts approvals/HITL in weeks 16–17 but write-capable GitHub/Gmail tools in weeks 7–9. | Phase 4 governance precedes any write tool going live; Phase 2 ships the deny-by-default gate. |
| 9 | v1's MVP workflow #1 (Daily Briefing) needs Gmail, Calendar and Notion; v1's MVP MCP set was GitHub / filesystem / Brave only. Google Calendar appears in no catalogue. | MVP includes read-only Gmail, Calendar, Notion via the in-house Google adapter and Notion MCP; a read-only PM agent (§8). |
| 10 | v2 MVP omits PPTX although v1's MVP workflow #7 produces a deck. | PPTX engine is in Phase 6. |
| 11 | Tier conflict: v1 requires approval to move/overwrite files; v2 makes File-agent move/rename `WRITE`. | **WRITE only inside the `/artifacts` sandbox.** Any change to user-owned files outside it is APPROVAL_REQUIRED (one batch plan approval), delete is RESTRICTED. |
| 12 | Compensating actions (v2 §7.3) are listed as automatic, but several (e.g. a retraction email) are themselves outward-facing. | A compensation inherits the tier of its action type and needs approval when that tier does. `gmail_send` is honestly labelled *irreversible* — which is why sending is gated. |
| 13 | Cost table (v1 §20): personal-tier column sums to **$55.60**, not the printed **$57.60**; LLM lines mix input-only and blended pricing. | Corrected and caveated in §23. |
| 14 | Prompt-injection defence relied on `<<<UNTRUSTED>>>` delimiters plus a "lightweight validator". Delimiters are advisory to a model, not a security boundary. | Boundary = the permission gate + payload-bound approvals (§11.3). Delimiters/validator stay as defence-in-depth. |
| 15 | Batch approval (Workflow 15: "single confirmation executes three actions") vs. per-payload token binding. | One UI confirmation may mint N tokens, each bound to its own payload hash. |
| 16 | Memory similarity threshold 0.78 combined with exponential decay: applying the threshold to the *decayed* score silently drops old-but-relevant memories (factor ≈ 0.41 at 180 days). | Threshold applies to **raw** similarity; decay only affects ranking. |
| 17 | Neither spec says who the identity provider is (OAuth PKCE endpoint **and** `hashed_password`). | Open decision O2; `hashed_password` made nullable. |
| 18 | The working folder is named "Testing **Ollama**Ai" but both specs are Gemini-only (Gemma local inference appears only as a far-future item). | Open decision O1. The Model Gateway (D13) makes a local provider an add-on, not a redesign. |

---

## 3. Vision, principles and scope

**MAYA AI** is a voice-first, multi-agent *digital employee*: it takes an instruction by voice or text, decomposes it into a dependency graph (DAG) of subtasks, runs them through specialised agents using tools reached over the Model Context Protocol (MCP), **verifies** the results deterministically, asks the human before anything consequential, and reports back by voice and screen.

**Non-negotiable principles**
1. **Action over conversation** — output is verified deliverables (a spreadsheet, a PR), not prose.
2. **No success without proof** — a task step is `completed` only on a tool receipt, exit code, HTTP 2xx or verified artifact hash. Model text alone never marks anything done.
3. **Explicit trust boundaries** — enforced in backend code, never in prompts.
4. **Asynchronous by default** — the user can assign work, close the laptop, and come back to the result.
5. **Everything auditable** — every tool call, approval and outcome is logged tamper-evidently.

**Out of scope for the MVP:** Playwright computer-use over authenticated multi-page sites, Salesforce/SAP/enterprise Jira clusters, Tauri desktop packaging, local offline inference, swarm cloning of agents, SAML/Okta SSO, cold-archive tier.

---

## 4. Requirements

### 4.1 Functional

| ID | Requirement | Verification |
| :-- | :--- | :--- |
| FR-01 | Bidirectional streaming voice with live transcription. | Recorded round trip; transcript compared to ground truth. |
| FR-02 | Barge-in: playback stops within 150 ms of user speech; buffers flushed. | Automated audio test, §21. |
| FR-03 | Compound instructions become an inspectable, editable DAG before execution; cycle detection passes. | Graph validation tests. |
| FR-04 | Long tasks run in the background without blocking chat/voice. | Voice stays responsive while a worker runs a multi-step task. |
| FR-05 | Dynamic MCP discovery; MCP tool schemas mapped to Gemini function declarations. | Handshake test; declarations populated. |
| FR-06 | State-changing actions are blocked until a valid, unexpired, payload-bound approval exists. | Gate tests at the MCP boundary (§21). |
| FR-07 | Generate and validate PDF, DOCX, XLSX, PPTX. | File verifiers (§13). |
| FR-08 | Repo analysis, bug repro, patch, tests in sandbox, PR (gated). | Sandbox test run + PR created via GitHub MCP. |
| FR-09 | Hybrid (vector + full-text) memory with citations. | Retrieval relevance tests, §12. |
| FR-10 | *(Post-MVP)* Sandboxed browser automation. | — |
| FR-11 | GDPR/CCPA purge of a user's memory and history. | `DELETE /api/v1/memory/purge` e2e test. |

### 4.2 Non-functional

| Metric | Target | Hard SLO / constraint |
| :--- | :--- | :--- |
| Voice end-to-end latency | 450–600 ms | **p95 ≤ 850 ms** under normal load |
| Text time-to-first-token | 250–400 ms | ≤ 600 ms (`orchestrator` alias) |
| Voice interrupt latency | 80–120 ms | ≤ 150 ms |
| Concurrency | 12 subagents/session, 10 active tasks/user | 100 active tasks per worker cluster |
| Availability | — | **99.9 % MVP** (99.95 % multi-AZ post-MVP) |
| Secrets | AES-256-GCM, keys rotated ≤ 90 days | No plaintext on disk |
| Tenant isolation | RLS on every tenant table | Verified by a cross-tenant test suite |
| Audit | Append-only, hash-chained, 365-day retention | — |
| Recovery | Task resumes from last checkpoint after worker crash | No duplicated side effects (idempotency keys) |

---

## 5. System architecture

```
                         CLIENTS  (Next.js 15 web canvas · WebAudio 16 kHz worklet · [later] Tauri)
                                        │ HTTPS / WSS / SSE
                 ┌──────────────────────▼──────────────────────┐
                 │  INGRESS: reverse proxy · OAuth2/JWT guard · rate limit / WAF │
                 └───────────┬───────────────────────┬─────────┘
              WebSocket (audio)                 REST / SSE
       ┌─────────────────────▼───────┐      ┌────────▼──────────────────────────────┐
       │  VOICE SERVICE              │      │  API + TASK ENGINE                     │
       │  Gemini Live session        │      │  LangGraph orchestrator (StateGraph)   │
       │  Silero VAD · barge-in      │◄────►│  DAG planner · saga · idempotency      │
       │  tools: submit_task,        │ Redis│  Permission gate · approval service    │
       │         get_task_status     │ event│  Model Gateway                         │
       └─────────────────────────────┘  bus └────────┬──────────────────────────────┘
                                                     │ one short job per subtask
                                  ┌──────────────────▼───────────────────┐
                                  │  AGENT WORKERS (Celery/ARQ)          │
                                  │  Research · Coding · Document ·      │
                                  │  Email · PM(read-only)               │
                                  └───────┬─────────────────┬────────────┘
                              ┌───────────▼─────────┐  ┌────▼───────────────────┐
                              │  MCP GATEWAY        │  │  SANDBOX RUNNER (D14)  │
                              │  pool · supervisor  │  │  fixed-function, no    │
                              │  schema normaliser  │  │  docker socket exposed │
                              │  gate · sanitiser   │  └────────────────────────┘
                              └───────────┬─────────┘
                       GitHub · Git · Filesystem · Brave · Notion · Google adapter
                                          │
          PostgreSQL 17 (+pgvector, RLS)  ·  Redis 7.4 (single node, AOF)  ·  S3-compatible artifacts
```

**Rules of the architecture**
- Agents never cause side effects directly; every external effect passes the MCP Gateway (or Sandbox Runner), which enforces tier, approval, idempotency, and audit.
- The Orchestrator has read-only access to state and **no** network/filesystem write access.
- Approval *resolution* is reachable only through the authenticated REST endpoint — never through an LLM tool call.
- A worker never blocks waiting for a human: it persists `awaiting_approval`, exits, and is re-scheduled by the approval event.

---

## 6. Voice subsystem

**Pipeline:** Mic (16 kHz, 16-bit PCM, mono) → `AudioWorklet` (100 ms / 1,600-sample frames) → client Silero VAD → WebSocket → Voice Service → Gemini Live (`live` alias) → 24 kHz PCM back → playback queue.

**Barge-in:** when the server reports `interrupted: true` (or client VAD fires while audio is playing) the Voice Service sends `{"type":"AUDIO_INTERRUPT"}`; the client flushes its queue and stops output ≤ 150 ms; the new user speech becomes the turn.

**Decoupling (D3):**
1. User states a request; Live model calls `submit_task(intent, params)`.
2. Voice Service publishes `TASK_SUBMISSION_EVENT` to Redis and speaks an immediate acknowledgement.
3. Task Engine plans and runs; progress is published as `TASK_PROGRESS_UPDATE` / `TASK_COMPLETED_EVENT`.
4. Voice Service injects those events into the live session (`send_client_content` — **validate in the Phase-5 spike**) so MAYA speaks updates without blocking the conversation.
5. Approvals needed → the UI shows the card; voice says "I need your approval for …" and accepts "cancel / deny" only (D5).

**Session limits [S]:** audio sessions drop around 15 minutes; use context-window compression and session-resumption tokens, reconnecting transparently (~every 10 min). Verify the real limit in the spike.

**Fallbacks:** `live` → `live-extended-thinking` for sessions needing background reasoning; degraded path = text model + `gemini-3.8-flash-tts` (post-MVP).

---

## 7. Model registry and Model Gateway

### 7.1 Registry (IDs [V]; prices/context [S])

| Alias | Model ID | Transport | Context in/out | Price per 1M tokens [S] | Fallback |
| :--- | :--- | :--- | :--- | :--- | :--- |
| `live` | `gemini-3.8-live` | Live API (WS) | 128k / 64k | in $1.50 · out(text) $9.00 · audio per real-time rate | `live-thinking` |
| `live-thinking` | `gemini-3.8-live-extended-thinking` | Live API (WS) | 128k / 64k | in $1.50 · out $9.00 (incl. thinking) | `live` |
| `orchestrator` | `gemini-3.8-flash` | HTTPS | 1,048,576 / 65,536 | in $0.75 · out $3.75 · cached $0.075 | `lite` |
| `heavy` | `gemini-4-argon` | HTTPS | 1,048,576 / 65,536 | in $2.00 · out $10.00 · cached $0.10 | `orchestrator` |
| `lite` | `gemini-3.5-flash-lite` | HTTPS | 1,048,576 / 65,536 | in $0.30 · out $2.50 · cached $0.03 | `orchestrator` |
| `research` | `deep-research-preview-04-2026` | Managed agent (async polling) | dynamic | managed tier | `heavy` with a custom plan |
| `tts` | `gemini-3.8-flash-tts` | HTTPS | 32k / audio | audio tier | — (post-MVP) |
| `embed` | `gemini-embedding-2` (768-dim) | Embeddings | 8,192 | text $0.20 · image/PDF $0.45 · audio $6.50 | `gemini-embedding-001` |
| *(optional)* | `antigravity-preview-09-2026` | Managed agent sandbox | dynamic | managed tier | Own Sandbox Runner (the default) |
| *(optional)* | `gemini-3.5-transcribe-live` | Live API | stream | streaming tier | client Whisper/WebSpeech |

Legacy `gemini-1.5-*`, `2.0-*`, `2.5-*` are not used.

### 7.2 Gateway behaviour (D13)
- Agents request an **alias**, never a model string. Alias → model ID → fallback chain is configuration.
- At startup: call `models.list`, fail fast (or warn + degrade) if an alias target is missing.
- Failure policy: HTTP 429 → exponential backoff with jitter (2/4/8/16 s) then next fallback; 5xx → fallback immediately; malformed structured output → one self-repair turn, then fallback.
- Enforces per-task budget: **max 10 tool turns and $1.50 per task** (configurable) before requiring user re-authorisation.
- Routing: voice → `live`; planning/orchestration/multi-tool → `orchestrator`; complex code, deep math, cross-repo refactor → `heavy`; extraction/classification/cleanup → `lite`; exhaustive web research → `research`; embeddings → `embed`.
- Use context caching for system prompts, agent definitions and repo schemas.

---

## 8. Agent fleet

Hierarchical LangGraph `StateGraph`; agents communicate through typed Pydantic payloads. Each agent has an **allow-list of tools**; it cannot call anything else.

| Agent | Model alias | Tools (MVP unless noted) | Tier ceiling without approval |
| :--- | :--- | :--- | :--- |
| **Orchestrator** | `orchestrator` | `create_plan_dag`, `dispatch_subtask`, `request_human_approval` (creates a request only), `synthesize_final_report` | Read-only state; no external I/O |
| **Research** | `research` / `heavy` | Brave Search, fetch/scrape (egress-filtered) | READ |
| **Coding** | `heavy` | GitHub MCP, Git MCP, Sandbox Runner, AST inspector | READ + WRITE (local branch, tests in sandbox); push / PR → APPROVAL |
| **Document** | `orchestrator` (+`lite` for bulk extraction) | `openpyxl`, `python-docx`, `python-pptx`, `weasyprint`, `pdfplumber`, `pypdf`, Pandoc | WRITE to `/artifacts` only |
| **Email & Comms** | `lite` filter + `orchestrator` draft | Gmail (search/read/draft/send) | READ; draft = WRITE; **send = APPROVAL** |
| **PM** *(MVP: read-only)* | `orchestrator` | Notion read, Calendar read | READ. Writes (Notion/Linear/Jira) are post-MVP |
| **Browser** *(post-MVP)* | `orchestrator` | `@playwright/mcp` in the sandboxed container | navigate = READ; form submit = APPROVAL; financial = RESTRICTED |
| **File Mgmt** *(post-MVP)* | `lite` | Filesystem MCP, hash verifier | WRITE only in `/artifacts`; user files = APPROVAL; delete = RESTRICTED |

**State object** (carried from v1/v2, unchanged in shape):

```python
class SubTask(BaseModel):
    id: str
    assigned_agent: Literal["research","coding","document","email","pm","browser","file"]
    description: str
    dependencies: list[str] = []
    status: Literal["pending","in_progress","awaiting_approval","completed","failed"] = "pending"
    input_data: dict = {}
    output_result: dict | None = None
    verification_proof: dict | None = None   # required before status -> completed
    error_message: str | None = None

class AgentExecutionState(BaseModel):
    session_id: str; user_id: str; workspace_id: str
    user_instruction: str
    interaction_modality: Literal["voice","text"]
    active_plan: list[SubTask] = []
    plan_status: Literal["planning","executing","paused","completed","failed"] = "planning"
    completed_task_ids: Annotated[list[str], operator.add] = []
    pending_approval: dict | None = None
    shared_context: dict = {}
    execution_trace: Annotated[list[dict], operator.add] = []
```

---

## 9. MCP Gateway

### 9.1 Responsibilities
Gemini only does JSON-schema function calling; it cannot speak MCP's JSON-RPC 2.0 or open stdio pipes. The gateway:
1. **Discovers** tools from each connected server and **normalises** schemas into Gemini `function_declarations`.
2. **Pools** connections — stdio (subprocess) and Streamable HTTP (legacy SSE only if a server requires it).
3. **Gates** every call: tier lookup → approval check → idempotency check → execute → audit.
4. **Sanitises** outputs: strip control characters, wrap in untrusted-content delimiters, optional lightweight validator pass.
5. **Supervises** servers: JSON-RPC `ping` every 15 s; 3 misses → `DEGRADED`; stdio respawn with back-off 1/2/4/8 s; permanent failure → Orchestrator re-routes (e.g. GitHub MCP down → local Git; Brave down → Gemini search grounding).
6. **Rate-limits** per vendor with a leaky-bucket to avoid 429s.

### 9.2 Final MCP catalogue

| Service | Server (pinned in image) | Transport | Tier handling | MVP |
| :--- | :--- | :--- | :--- | :--- |
| Filesystem | `@modelcontextprotocol/server-filesystem` | stdio | read = READ; write inside `/artifacts` = WRITE; elsewhere APPROVAL; delete RESTRICTED | ✅ |
| Git (local) | `mcp-server-git` | stdio | status/log/diff READ; commit/branch local WRITE; push APPROVAL; force-push/protected branch RESTRICTED | ✅ |
| GitHub | `github/github-mcp-server` | stdio (local) or remote HTTP | read READ; branch/PR/comment APPROVAL | ✅ |
| Web search | `@brave/brave-search-mcp-server` | stdio | READ | ✅ |
| Notion | `@notionhq/notion-mcp-server` | stdio / HTTP | read READ; write APPROVAL | ✅ read-only |
| Gmail · Calendar · Drive | **In-house Google adapter** (OAuth 2.0, least scopes) | Streamable HTTP | read READ; draft WRITE; **send APPROVAL** | ✅ |
| Playwright | `@playwright/mcp` | stdio (in sandbox container) | navigate READ; submit APPROVAL | ⏳ post-MVP |
| Linear | `mcp-server-linear` (vet) | HTTP | writes APPROVAL | ⏳ |
| Jira/Confluence | to be selected | HTTP | writes APPROVAL | ⏳ |
| Slack | to be selected | HTTP | post APPROVAL | ⏳ |
| Postgres (user DBs) | in-house, read-only role | stdio | `SELECT` only; DDL RESTRICTED | ⏳ |

### 9.3 MCP-specific hardening
- **Pin and hash**: exact versions, checksums, built into the image. No runtime `npx -y` / `uvx` pulls (supply-chain risk).
- **Tool-description poisoning**: tool names/descriptions from third-party servers enter the model's context, so they are untrusted. Store a hash of each server's schema; alert and disable on change.
- **Server hints are untrusted**: `readOnlyHint` / `destructiveHint` annotations can only raise a tool's tier, never lower it (D4).
- **Unclassified tool ⇒ disabled** until an admin classifies it.
- **Secrets**: injected by the gateway from the vault into the server's environment; never appear in model context.

---

## 10. Task execution engine

### 10.1 13-stage lifecycle
1 Ingest → 2 Intent parse & route → 3 Permission/safety pre-check → 4 DAG plan (checkpointed) → 5 Decompose → 6 Assign agents → 7 MCP discovery & schema binding → 8 Sandboxed execution with timeout → 9 Telemetry/event stream (SSE, voice fillers) → **10 Deterministic verification** → fail: 11 retry (back-off) or compensate → pass: 12 Persist artifacts & memory → 13 Final synthesis.

- Verification (stage 10) uses **exit codes, HTTP status, file hashes, structural checks** — not model opinion.
- Retry policy: ≤3 attempts for transient errors; ≤2 self-repair turns for logical validation failures, then escalate to the human.

### 10.2 Error classes

| Class | Examples | Policy |
| :--- | :--- | :--- |
| Transient | HTTP 429, network timeout | Back-off 2/4/8/16 s + jitter; check idempotency key before re-calling a mutating tool; then model fallback |
| Recoverable | MCP subprocess crash | Restart server, resume from checkpoint, retry once |
| Logical | Verification failed | Feed trace to agent (≤2 repair turns) → escalate |
| Fatal | Permission denied, budget exceeded | Halt, notify user, write audit record |

### 10.3 Sagas and compensation (D6, defect #12)
External effects cannot be rolled back, so each forward action registers a compensation in `compensations`:

| Forward action | Reversible? | Compensation | Needs approval? |
| :--- | :--- | :--- | :--- |
| create remote branch | yes | delete remote branch | yes (tier of branch delete) |
| `gmail_send` | **no** | flag for human review / draft a clarification | follow-up send is APPROVAL |
| `notion_create_page` | yes | archive page | yes (tier of Notion write) |
| create Slack channel | yes | archive channel | yes |
| file write in `/artifacts` | yes | delete file / restore from backup | no (sandbox only) |
| file move (user files) | yes | reverse move from move-log | yes |

### 10.4 Execution rules
- **Idempotency:** every mutating call carries `(workspace_id, idempotency_key)`; the gateway refuses duplicates.
- **Short jobs:** one subtask = one queue message; set broker visibility timeout > longest subtask; long waits are expressed as state, not as a blocked worker (D8).
- **Single state source:** LangGraph Postgres checkpointer (D6); `tasks`/`subtasks` are projections.
- **Abort:** `POST /tasks/{id}/abort` stops dispatch, kills child processes, then runs registered compensations (each subject to its own tier).

---

## 11. Security and governance

### 11.1 Permission tiers (D4)

| Tier | Autonomy | Examples |
| :--- | :--- | :--- |
| 🟢 **READ** | autonomous | read workspace files; git status/log/diff; `SELECT`; web search; read email/calendar/Notion/Slack |
| 🔵 **WRITE** | autonomous, reversible, sandbox-scoped | Gmail *drafts*; files in `/artifacts`; local git branches; tests in sandbox; build documents |
| 🟡 **APPROVAL_REQUIRED** | human approves each payload | send email / post Slack; push commits, open PRs, post review comments; create/update Notion-Linear-Jira items; submit web forms; modify user files outside `/artifacts` |
| 🔴 **RESTRICTED** | UI approval **plus step-up** (re-auth / TOTP) | DDL (`DROP/ALTER/TRUNCATE`, bulk `DELETE`); permanent file deletion; payments; security-setting / token / SSH-key changes; force-push or protected-branch merge; bulk PII export |

### 11.2 Approval protocol (D5)
1. A gated call pauses; the gateway writes `approval_requests` with `payload_hash = SHA-256(canonical{tool, args, workspace, user, idempotency_key})` and a human-readable `payload_diff` (email body, git diff, Notion change).
2. UI shows the exact payload. The user approves via `POST /api/v1/approvals/{id}` with their **session JWT**.
3. Server issues a signed single-use token (JWT, `exp` ≤ 5 min, claims: `request_id, tool_name, payload_hash, user_id, jti`).
4. At execution the gate **recomputes the hash from the actual call** (TOCTOU defence), verifies signature/expiry, and atomically flips the row `approved → consumed`. Anything else is denied and audited.
5. RESTRICTED additionally requires step-up authentication.
6. Voice may read back, deny or cancel; it can never produce an approval (D5).
7. A batch confirmation mints one token per action, each bound to its own hash.

### 11.3 Prompt-injection defence (layered)
1. **Capability gate (the real boundary):** untrusted content can at worst *request* an action; mutating actions need a payload-bound human approval.
2. **Privilege separation:** agents that ingest untrusted content (Research, Email read, Browser) hold read-only tools; they hand data — not instructions — to the Orchestrator as structured fields.
3. **Delimiters + sanitiser + small validator model** on all tool output (`<<<UNTRUSTED_EXTERNAL_INPUT>>>`) — defence in depth only.
4. **Egress control:** research/browser traffic passes a proxy; no auto-following of links to internal ranges.
5. **Review UI** shows the *actual* payload (not the model's summary of it).

### 11.4 Secrets and isolation
- AES-256-GCM at rest; keys from KMS (AWS/GCP) or PBKDF2 (600k iterations) master key locally; rotate ≤ 90 days (`key_id` stored with ciphertext). Plaintext only in memory during the call.
- Postgres RLS by `workspace_id` (§14) with a non-owner, non-superuser application role.
- **Sandbox Runner (D14):** non-root containers, image allow-list, CPU/mem/time limits, no host mounts, default-deny network.
- **Browser sandbox (post-MVP):** separate container; egress proxy blocks RFC1918, loopback and `169.254.169.254` **by resolved IP** (defeats DNS-rebinding); credentials injected by the vault, never shown to the model; **never defeats CAPTCHA/bot checks** — pauses and hands the session to the human.
- Audit: hash-chained `audit_log` with redacted arguments/outputs (no secrets, PII masked).

---

## 12. Memory and RAG (D7)

| Tier | Store | Contents | Lifetime |
| :--- | :--- | :--- | :--- |
| **Working** | Redis | last 20 turns, active DAG state, ephemeral tool output | 24 h inactivity |
| **Profile & structured** | Postgres | explicit preferences (tone, default repo, contacts); updated only on user statement/confirmation | until changed |
| **Semantic** | Postgres + pgvector (HNSW) | categories: `project`, `episodic` (task history, post-mortems, good patches), `knowledge` (PDFs, Notion pages, API docs) | decay + archive rules below |

- **Ingestion:** semantic chunking 512 tokens / 64 overlap; `embed` alias, 768-dim; store `embedding_model` with every row (re-embed on model change).
- **Retrieval:** hybrid — dense cosine (initial weight 0.7) + `tsvector` full-text (0.3); weights tuned on a labelled set in Phase 7. **Always** filter `workspace_id` and `user_id`. Enable pgvector iterative scan for filtered HNSW (≥ 0.8) or partition if recall drops under filters — test in Phase 1.
- **Threshold/decay (defect #16):** relevance gate 0.78 on **raw** cosine similarity; ranking score `S_eff = S_sim · exp(−λ·Δt)`, λ = 0.005/day; items unused 180 days are marked archived unless `permanent`.
- **Hygiene:** deterministic PII/secret scrub (cards, passwords, bearer tokens, government IDs) before any write to long-term memory; user-visible memory browser with edit/delete; `DELETE /api/v1/memory/purge`.
- **Citations:** every retrieved snippet carries its source id for display.

---

## 13. Document pipeline and verification

A file is `completed` only after its verifier passes (default status `pending`).

| Format | Engine | Deterministic verification |
| :--- | :--- | :--- |
| **XLSX** | `openpyxl`, `pandas` | reopen (`data_only=False`); no `#REF!/#VALUE!/#NAME?`; row/col counts match source data; formulas evaluated by a calc engine in the test harness |
| **DOCX** | `python-docx`, Pandoc | XML valid (`lxml`); heading hierarchy consistent; tables render with styles |
| **PPTX** | `python-pptx` | slide count = request; every text frame within canvas bounds (overflow check); 16:9 |
| **PDF** | `weasyprint`, `pypdf`, `pdfplumber` | exit 0; page count > 0; fonts embedded; text layer extractable; render-to-image smoke check |
| Data/Text | `csv`, `json`, `marko`, `Pillow` | schema validation; lint |

Each artifact row stores `sha256`, `size_bytes`, and the `verification_report`.

---

## 14. Data model (PostgreSQL 17 + pgvector) — final DDL

```sql
CREATE EXTENSION IF NOT EXISTS vector;
CREATE EXTENSION IF NOT EXISTS pg_trgm;           -- fuzzy match; gen_random_uuid() is built in

CREATE TABLE workspaces (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  name VARCHAR(255) NOT NULL,
  slug VARCHAR(100) UNIQUE NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE users (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  email VARCHAR(255) UNIQUE NOT NULL,
  full_name VARCHAR(255) NOT NULL,
  hashed_password VARCHAR(255),                   -- NULL when an external IdP is used (open decision O2)
  role VARCHAR(50) NOT NULL DEFAULT 'user',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE sessions (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  title VARCHAR(255),
  channel VARCHAR(20) NOT NULL DEFAULT 'web' CHECK (channel IN ('web','voice','desktop')),
  status VARCHAR(20) NOT NULL DEFAULT 'active',
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE messages (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  sender VARCHAR(20) NOT NULL CHECK (sender IN ('user','orchestrator','agent','system')),
  agent_name VARCHAR(100),
  modality VARCHAR(20) NOT NULL DEFAULT 'text' CHECK (modality IN ('text','audio','multimodal')),
  content TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}',
  audio_s3_uri VARCHAR(512),                      -- subject to retention policy
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE tasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  session_id UUID NOT NULL REFERENCES sessions(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  idempotency_key VARCHAR(128) NOT NULL,
  title VARCHAR(255) NOT NULL,
  instruction TEXT NOT NULL,
  dag_plan JSONB NOT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','executing','awaiting_approval','completed','failed','cancelled','compensating')),
  budget_usd NUMERIC(8,2) NOT NULL DEFAULT 1.50,
  spent_usd  NUMERIC(8,4) NOT NULL DEFAULT 0,
  tool_turns INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ,
  UNIQUE (workspace_id, idempotency_key)          -- per-tenant, not global
);

CREATE TABLE subtasks (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  assigned_agent VARCHAR(50) NOT NULL,
  description TEXT NOT NULL,
  dependencies JSONB NOT NULL DEFAULT '[]',
  status VARCHAR(30) NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','in_progress','awaiting_approval','completed','failed')),
  input_data JSONB NOT NULL DEFAULT '{}',
  output_result JSONB,
  verification_proof JSONB,                       -- required before status = 'completed' (enforced in code + test)
  error_message TEXT,
  retry_count INTEGER NOT NULL DEFAULT 0,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  completed_at TIMESTAMPTZ
);

CREATE TABLE approval_requests (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  subtask_id UUID NOT NULL REFERENCES subtasks(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  tool_name VARCHAR(150) NOT NULL,
  permission_level VARCHAR(30) NOT NULL CHECK (permission_level IN ('APPROVAL_REQUIRED','RESTRICTED')),
  action_description TEXT NOT NULL,
  payload_hash CHAR(64) NOT NULL,
  payload_diff JSONB NOT NULL,
  status VARCHAR(20) NOT NULL DEFAULT 'pending'
    CHECK (status IN ('pending','approved','denied','expired','consumed')),
  token_jti UUID,
  decided_at TIMESTAMPTZ,
  consumed_at TIMESTAMPTZ,
  expires_at TIMESTAMPTZ NOT NULL,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- Single-use is enforced atomically:
--   UPDATE approval_requests SET status='consumed', consumed_at=now()
--   WHERE id=$1 AND status='approved' AND expires_at>now() AND payload_hash=$2 RETURNING id;

CREATE TABLE compensations (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  subtask_id UUID REFERENCES subtasks(id) ON DELETE SET NULL,
  forward_tool VARCHAR(150) NOT NULL,
  forward_ref JSONB NOT NULL,                     -- e.g. {"repo":"…","branch":"…"}
  compensating_tool VARCHAR(150),                 -- NULL = irreversible
  compensating_args JSONB,
  permission_level VARCHAR(30) NOT NULL,
  status VARCHAR(30) NOT NULL DEFAULT 'registered'
    CHECK (status IN ('registered','pending_approval','executed','failed','not_applicable')),
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

-- Append-only, hash-chained audit trail. No FK to users/tasks so it survives deletes.
CREATE TABLE audit_log (
  id BIGINT GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
  workspace_id UUID NOT NULL,
  user_ref TEXT NOT NULL,                         -- user id, or a pseudonym after a GDPR purge
  task_id UUID, subtask_id UUID, approval_id UUID,
  server_name VARCHAR(100), tool_name VARCHAR(150),
  permission_level VARCHAR(30),
  input_redacted JSONB, output_redacted JSONB,    -- secrets removed, PII masked
  status VARCHAR(30) NOT NULL,                    -- success | failed | denied | …
  duration_ms INTEGER,
  prev_hash BYTEA,
  row_hash BYTEA NOT NULL,                        -- sha256(prev_hash || canonical row); chain per workspace
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- REVOKE UPDATE, DELETE, TRUNCATE ON audit_log FROM maya_app;   (writes serialised per workspace via advisory lock)

CREATE TABLE mcp_servers (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  name VARCHAR(100) NOT NULL,
  transport VARCHAR(20) NOT NULL CHECK (transport IN ('stdio','streamable_http','sse_legacy')),
  command VARCHAR(512), args JSONB NOT NULL DEFAULT '[]', url VARCHAR(512),
  pinned_version VARCHAR(100), schema_hash CHAR(64),
  env_vars_encrypted BYTEA, key_id VARCHAR(100),  -- AES-256-GCM ciphertext + KMS key id
  is_active BOOLEAN NOT NULL DEFAULT TRUE,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE file_artifacts (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  task_id UUID NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  file_name VARCHAR(255) NOT NULL,
  file_type VARCHAR(20) NOT NULL CHECK (file_type IN ('pdf','xlsx','docx','pptx','md','csv','other')),
  storage_uri VARCHAR(512) NOT NULL,
  size_bytes BIGINT NOT NULL,
  sha256 CHAR(64) NOT NULL,
  verification_status VARCHAR(20) NOT NULL DEFAULT 'pending'   -- NOT 'verified' (defect #4)
    CHECK (verification_status IN ('pending','verified','failed')),
  verification_report JSONB,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);

CREATE TABLE memory_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id UUID NOT NULL REFERENCES users(id) ON DELETE CASCADE,
  category VARCHAR(20) NOT NULL CHECK (category IN ('preference','project','episodic','knowledge')),
  content TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}',
  embedding vector(768),
  embedding_model VARCHAR(100) NOT NULL,          -- re-embed when the model changes
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
  permanent BOOLEAN NOT NULL DEFAULT FALSE,
  access_count INTEGER NOT NULL DEFAULT 1,
  last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now()
);
CREATE INDEX memory_embedding_hnsw ON memory_items USING hnsw (embedding vector_cosine_ops) WITH (m = 16, ef_construction = 64);
CREATE INDEX memory_tsv_gin        ON memory_items USING gin (tsv);
CREATE INDEX memory_content_trgm   ON memory_items USING gin (content gin_trgm_ops);
CREATE INDEX memory_scope_idx      ON memory_items (workspace_id, user_id, category);

-- Row-level security: the specs claimed it but never defined it.
DO $$
DECLARE t text;
BEGIN
  FOREACH t IN ARRAY ARRAY['users','sessions','messages','tasks','subtasks','approval_requests',
                           'compensations','audit_log','mcp_servers','file_artifacts','memory_items'] LOOP
    EXECUTE format('ALTER TABLE %I ENABLE ROW LEVEL SECURITY', t);
    EXECUTE format('ALTER TABLE %I FORCE ROW LEVEL SECURITY', t);
    EXECUTE format($p$CREATE POLICY ws_isolation ON %I
       USING      (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)
       WITH CHECK (workspace_id = NULLIF(current_setting('app.workspace_id', true), '')::uuid)$p$, t);
  END LOOP;
END $$;
-- Per request: BEGIN; SET LOCAL app.workspace_id = '<uuid>'; …  (unset ⇒ no rows ⇒ default-deny).
-- The app role must NOT own the tables or hold BYPASSRLS/superuser. Login (email lookup before the
-- workspace is known) goes through a SECURITY DEFINER function, not a policy exception.
```

---

## 15. API surface

| Path | Method | Purpose |
| :--- | :--- | :--- |
| `/api/v1/auth/token` | POST | OAuth 2.0 (PKCE) code → JWT access/refresh (IdP per O2) |
| `/api/v1/chat/message` | POST | Submit text instruction; SSE stream of task events |
| `/api/v1/voice/live` | WS | Audio bridge to the Voice Service |
| `/api/v1/tasks` | GET / POST | List (filters, pagination) / submit with idempotency key |
| `/api/v1/tasks/{id}` | GET | DAG, subtask states, artifact links |
| `/api/v1/tasks/{id}/events` | GET (SSE) | Live progress |
| `/api/v1/tasks/{id}/abort` | POST | Cancel + run compensations |
| `/api/v1/approvals` | GET | Pending approvals |
| `/api/v1/approvals/{id}` | POST | `{decision: approve|deny}` — **user session only**; RESTRICTED needs step-up |
| `/api/v1/artifacts/{id}` | GET | Download verified artifact |
| `/api/v1/mcp/servers` | GET / POST | List+health+tools / register (validates handshake & schema) |
| `/api/v1/mcp/servers/{id}` | DELETE | Disable and remove |
| `/api/v1/memory/search` | POST | Hybrid search with citations |
| `/api/v1/memory/purge` | DELETE | GDPR/CCPA erase (granular or full) |

---

## 16. UI/UX

**MVP — three surfaces** (v2's "single digital-employee experience"):
1. **Ambient workspace:** waveform voice orb (listening / thinking / speaking / interrupted), chat stream, and a *Deliverable Canvas* previewing the XLSX / PDF / diff next to the conversation.
2. **Task Center & Live Work drawer:** plain-language progress ("Step 2 of 4 — extracting pricing tables"), Pause / Cancel / Inspect evidence; DAG view.
3. **Approval Center:** exact-payload cards (email preview, unified git diff, Notion change), *Approve & Execute* / *Reject* / *Edit & Execute*; RESTRICTED shows step-up.

**Post-MVP ops screens** (from v1's ten): Agent Fleet Operations Center · MCP Integration Gateway (one-click OAuth, per-tool toggles) · Connected SaaS Apps · Memory & Knowledge Repository · Artifact Manager · Historical Task & Audit Ledger · Security/Privacy/Profile Settings (routing prefs, spend limits, PII rules, retention).

---

## 17. The 15 workflows

Acceptance set for the MVP = ✅. "Highest tier" is the strictest tier any step needs.

| # | Workflow | Agents | Models | Tools | Highest tier | Phase |
| :-- | :--- | :--- | :--- | :--- | :--- | :--- |
| 1 | Daily briefing & agenda | Orchestrator, Email, PM(ro) | `orchestrator` (+`live` for audio) | Gmail, Calendar, Notion (read) | READ | ✅ P5 |
| 2 | Email triage & draft replies | Email | `lite` filter + `orchestrator` | Gmail | APPROVAL (send) | stretch |
| 3 | GitHub issue → root cause → fix PR | Coding | `heavy` | GitHub, Git, Sandbox Runner | APPROVAL (push/PR) | ✅ P4 |
| 4 | Sync merged PRs into Notion sprint board | PM, Coding | `orchestrator` | GitHub, Notion | APPROVAL | post-MVP |
| 5 | Deep competitive research → PDF | Research, Document | `research` + `orchestrator` | Brave, weasyprint | WRITE | ✅ P6 |
| 6 | PDF invoices → formatted Excel | Document | `orchestrator` (multimodal) | `openpyxl` | WRITE | ✅ P6 |
| 7 | Brief → 8-slide PowerPoint | Document | `orchestrator` | `python-pptx` | WRITE | ✅ P6 |
| 8 | Client meeting dossier | Research, Email, PM | `orchestrator` | Gmail, Notion, Brave | READ | stretch |
| 9 | Transcript → Jira tickets | Document, PM | `orchestrator` | Jira | APPROVAL | post-MVP |
| 10 | Weekly project health report | PM, Coding, Document | `orchestrator` | GitHub, Linear | WRITE | post-MVP |
| 11 | PR code review & security check | Coding | `heavy` | GitHub | APPROVAL (public comments) | stretch |
| 12 | Codebase docs / OpenAPI generation | Coding, Document | `heavy` + `lite` | Git, Filesystem | APPROVAL (repo write) | post-MVP |
| 13 | Organise Downloads + backup | File | `lite` | Filesystem | APPROVAL (move) / RESTRICTED (delete) | post-MVP |
| 14 | Weekly accomplishment report | Orchestrator, PM, Email | `orchestrator` | Notion, GitHub, Gmail | READ + WRITE | post-MVP |
| 15 | Client onboarding (Notion + Slack + email) | PM, Email | `orchestrator` | Notion, Slack, Gmail | APPROVAL (batch, N tokens) | post-MVP |

**Per-workflow requirements kept from the specs:** every workflow ends with verified output; partial-failure behaviour is explicit (e.g. #1 proceeds without Notion and says so; #3 aborts the PR after 3 failed repair attempts and returns a diagnostic report; #13 never overwrites — collisions get `_1` suffix and moves use copy → hash-verify → unlink; #15 on Slack name collision appends the year).

---

## 18. Repository structure

```
maya-ai/
├── .github/workflows/                 # test.yml, deploy.yml
├── apps/
│   ├── web/                           # Next.js 15 + TS + Tailwind
│   │   └── src/{app, components/{voice,canvas,tasks,approvals}, hooks/useLiveVoice.ts, lib/api.ts}
│   └── desktop/                       # [post-MVP] Tauri shell
├── backend/
│   ├── app/
│   │   ├── api/v1/                    # auth, chat, voice, tasks, approvals, artifacts, mcp_admin, memory
│   │   ├── core/                      # config, security (AES-GCM, JWT), telemetry
│   │   ├── db/                        # SQLAlchemy models, Alembic migrations, RLS setup
│   │   ├── gateway/                   # model_gateway.py (aliases, fallbacks, budgets)
│   │   ├── voice/                     # live_session.py, audio_converter.py, vad.py, event_bridge.py
│   │   ├── tasks/                     # worker app, saga.py, idempotency.py, events.py
│   │   ├── agents/                    # orchestrator.py, state.py, specialized/{research,coding,document,email,pm}.py
│   │   ├── mcp/                       # client_manager.py, registry.py, permissions.py, approvals.py,
│   │   │                              #   sanitizer.py, supervisor.py, transports/
│   │   ├── sandbox/                   # sandbox runner client (D14)
│   │   ├── memory/                    # short_term.py, long_term.py, rag_engine.py, scrubber.py
│   │   └── automation/                # {xlsx,docx,pptx,pdf}_engine.py + verifiers/   (playwright_runner.py post-MVP)
│   ├── mcp_servers/google_adapter/    # in-house Gmail/Calendar/Drive MCP server
│   ├── tests/{unit,integration,e2e,evals}/
│   ├── Dockerfile
│   └── pyproject.toml                 # exact version pins (google-genai, mcp, langgraph, …)
├── docker/{docker-compose.yml, docker-compose.prod.yml, postgres/, sandbox/}
└── README.md
```

---

## 19. Roadmap — 22 weeks to MVP

> Durations are carried over from the specs (+2 weeks for the memory phase v2 omitted). **Neither spec states team size**, so treat the calendar as unvalidated until O3 is answered.

| Phase | Weeks | Scope | Exit criteria (all must pass) |
| :--- | :--- | :--- | :--- |
| **P1 Foundation** | 1–3 | Repo, Docker Compose (Postgres 17+pgvector, Redis), Alembic + final DDL **with RLS**, auth, Model Gateway with alias config + `models.list` check, basic Next.js chat on `orchestrator`. **Week-1 spikes:** (S1) Celery-vs-ARQ under asyncio + LangGraph; (S2) filtered HNSW recall with workspace filters; (S3) Live API: real session limit, resumption, `send_client_content` injection, first-audio latency; (S4) confirm embedding `output_dimensionality` = 768. | Register/login, streaming chat persisted; cross-tenant read test returns 0 rows; all four spikes written up with a go/no-go. |
| **P2 Agent core & task engine** | 4–6 | LangGraph orchestrator, DAG planner + cycle check, queue, Redis event bus, checkpointer (D6), idempotency, **deny-by-default permission gate skeleton** (registry + `approval_requests` creation, stub approver), token/cost telemetry. | Multi-step instruction renders a DAG and runs dependent steps in order/parallel; killing a worker mid-task resumes without duplicate side effects; an unclassified tool call is refused. |
| **P3 MCP Gateway** | 7–9 | Client pool (stdio + Streamable HTTP), supervisor, schema normaliser, sanitiser, rate limiting; Filesystem, Git, GitHub, Brave, Notion(ro); in-house Google adapter (Gmail/Calendar read + draft). Version pinning, schema-hash alarms. | ≥ 3 distinct servers invoked end-to-end; server killed → respawn + fallback works; **no write tool is reachable without the gate**. |
| **P4 Governance** | 10–11 | Signed payload-bound approval tokens, Approval Center UI, step-up for RESTRICTED, secret vault (AES-GCM + KMS), hash-chained audit log, saga/compensation registry, Sandbox Runner. Enables **Workflow 3**. | Tampered payload ⇒ denied; replayed or expired token ⇒ denied; voice cannot approve; audit chain verifies; Workflow 3 passes in sandbox with PR gated. |
| **P5 Real-time voice** | 12–14 | Voice Service (Live), AudioWorklet, Silero VAD, barge-in, resumption, event-bus bridge with spoken progress, `submit_task`/`get_task_status`. Enables **Workflow 1**. | p95 first-audio ≤ 850 ms in the benchmark; interrupt ≤ 150 ms; a 20-minute session survives reconnection; voice stays responsive while a task runs. |
| **P6 Document engines** | 15–17 | XLSX, DOCX, PDF, **PPTX** engines + verifiers, artifact registry + previews. Enables **Workflows 5, 6, 7**. | Each verifier has positive and negative tests (corrupt file ⇒ `failed`); Workflows 5–7 pass. |
| **P7 Memory & RAG** | 18–19 | Ingestion, hybrid retrieval, PII scrubber, decay/archive job, memory browser, purge endpoint; tune weights. | Labelled retrieval set meets recall target at the 0.78 raw-similarity gate; filtered query p95 within the budget measured in S2; purge removes all rows for a user. |
| **P8 Hardening & launch** | 20–22 | e2e + eval suites, load test, OpenTelemetry/Prometheus/Grafana, K8s/Compose prod config, runbooks, cost monitoring, stretch workflows (2, 8, 11) if time. | All 5 acceptance workflows pass with 0 unhandled exceptions; 99.9 % over the load-test window; zero unapproved mutating calls in the safety suite. |

**Critical path:** P1 → P2 → P3 → P4 are strictly serial (each depends on the previous gate). P5, P6, P7 can overlap if staffing allows once P4 is done.

---

## 20. MVP scope

**In:** Next.js workspace with text chat and voice; Orchestrator + Research, Coding, Document, Email agents + read-only PM; MCP: Filesystem, Git, GitHub, Brave, Notion(read), Google adapter (Gmail/Calendar); four-tier governance with payload-bound approvals and audit; PostgreSQL + pgvector memory with Redis working memory; XLSX/DOCX/PDF/PPTX generation with verifiers; the five acceptance workflows (#1, #3, #5, #6, #7).

**Deferred:** Browser agent / Playwright, File-Management agent, Slack/Linear/Jira/Postgres MCP, Tauri desktop, voice approval, local/offline inference, swarm agents, SAML/SSO, cold-archive tier, multi-AZ 99.95 %.

---

## 21. Testing and quality

| Layer | What | Notes |
| :--- | :--- | :--- |
| **Unit (pytest)** | parsers, cipher, hash/token logic, verifiers, decay math, RLS | positive **and** negative cases |
| **Gate / safety (deterministic)** | property tests at the MCP boundary: *no mutating tool call reaches a server without a valid, unexpired, unconsumed, hash-matching approval* | 100 % pass required; **not** LLM-judged |
| **Integration** | mock MCP servers (GitHub, Gmail, Notion) with scripted success/429/crash; Redis bus; LangGraph checkpoint/resume | exercises retries, fallbacks, idempotency |
| **Tenant isolation** | two workspaces; every endpoint and table must show zero cross-reads | in CI |
| **Evals (LLM-as-judge)** | plan quality, tool-selection precision/recall, parameter fidelity against golden datasets | initial thresholds set from the Phase-2 baseline and ratcheted; the specs' 98 %/99 % are aspirations. Use deterministic checks as primary; the judge shares a model family with the agents, so don't treat it as ground truth |
| **Voice latency** | replay 16 kHz PCM, time to first 24 kHz chunk; interrupt test | gate in CI nightly |
| **File verifiers** | corrupt/truncated files must be rejected | covers `#REF!`, overflow, empty PDF |
| **E2E (Playwright UI)** | approval flow, DAG status colours, voice orb states | |
| **Chaos** | kill worker / MCP server / Redis mid-task | resume with no duplicate side effects |

---

## 22. Deployment and operations

- **Primary topology (cloud):** containers behind a TLS reverse proxy with sticky sessions for WebSockets; FastAPI replicas; Celery/ARQ workers as non-root containers; managed Postgres 17 + pgvector; Redis single node with AOF (cluster later); S3-compatible artifacts. Start on Docker Compose; Kubernetes (EKS/GKE) when scale demands.
- **Desktop / privacy-first (post-MVP):** Tauri shell + Python sidecar, local Postgres/SQLite-with-vector, local sandbox.
- **Observability:** OpenTelemetry traces across FastAPI → LangGraph → MCP; Prometheus metrics (token use, TTFT, voice latency, MCP connection health, queue depth) in Grafana; structured JSON logs; the hash-chained `audit_log` is the compliance record.
- **Operational controls:** per-task and per-user spend limits, key-rotation job, memory-decay job, MCP schema-change alarms, runbooks for Live-API outage (degrade to text), model-deprecation (change alias config), and rate-limit storms.

---

## 23. Cost model

Carried from v1 with the arithmetic corrected. Prices are the specs' quoted figures [S]; **re-verify before committing to a budget.**

| Component | Personal (1 user) | Small team (5) | Scale (50) |
| :--- | :--- | :--- | :--- |
| `orchestrator` | $4.50 (6M tok) | $22.50 (30M) | $225.00 (300M) |
| `heavy` | $6.00 (1M) | $30.00 (5M) | $300.00 (50M) |
| `lite` | $1.20 (4M) | $6.00 (20M) | $60.00 (200M) |
| `live` voice | $9.00 (60 min) | $45.00 (300 min) | $450.00 (3,000 min) |
| `embed` | $0.40 (2M) | $2.00 (10M) | $20.00 (100M) |
| PostgreSQL + pgvector | $15.00 | $45.00 | $180.00 |
| Redis | $8.00 | $20.00 | $65.00 |
| Compute & sandboxes | $10.00 | $35.00 | $160.00 |
| Storage & bandwidth | $1.50 | $6.00 | $35.00 |
| **Total / month** | **$55.60** *(spec printed $57.60 — sum error)* | **$211.50** | **$1,495.00** |
| Per user | $55.60 | $42.30 | $29.90 |

**Caveat:** the `orchestrator` and `lite` lines equal *tokens × input price* only (6M × $0.75 = $4.50), while the `heavy` line is a ~50/50 in/out blend ($6.00/M). Output tokens are therefore unpriced on two of three LLM lines. As an **illustrative assumption only** (70 % input / 30 % output), those lines would be ≈ 2.2× and ≈ 3.2× higher. Treat the table as a floor and re-baseline from real token telemetry in Phase 2.

**Levers:** context caching (cached read $0.075 vs $0.75, −90 %); route bulk work to `lite`, reserve `heavy` for hard code/maths; client-side VAD to avoid sending silence (spec claims up to 40 % fewer audio tokens — **a hypothesis to measure, not a number to plan on**); per-task $1.50 / 10-turn cap.

---

## 24. Risks and mitigations

| Risk | Severity | Mitigation |
| :--- | :--- | :--- |
| Indirect prompt injection via web / email / tool output | Critical | Layered defence §11.3; the approval gate is the boundary |
| Unintended destructive or outward-facing action | Critical | Deny-by-default tiers, payload-bound single-use approvals, idempotency, audit |
| Cross-tenant data leakage (SQL or vector search) | Critical | RLS with a non-owner role, mandatory scope filters, isolation tests in CI |
| Docker-socket / sandbox escape | Critical | D14: no socket exposure, fixed-function runner, non-root, no host mounts |
| MCP supply-chain / tool-poisoning | High | Pin + hash + bake images; schema-hash alarms; untrusted descriptions |
| Stale/non-existent MCP packages (found in specs) | High | §2.2 catalogue; re-verify at the start of P3 |
| Hallucinated success | High | `verification_proof` required; verifiers; no model-text completion |
| Live API session drop (~15 min [S]) | High | Compression, resumption tokens, decoupled voice/tasks |
| Duplicate side effects from queue redelivery | High | Idempotency keys; short jobs; visibility timeout > longest job |
| Model/API change or outage | Medium | Aliases + fallbacks + startup `models.list` check |
| Bot detection / CAPTCHA | High (post-MVP) | Never bypass; pause and hand to the human |
| MCP stdio crash | Medium | Supervisor, back-off respawn, fallback routes |
| Cost runaway from repair loops | Medium | $1.50 / 10-turn caps, spend alarms |
| Schedule risk (no team size, preview models) | Medium | O3; preview models (`deep-research`, `antigravity`) kept behind fallbacks |

---

## 25. Open decisions (need an owner)

| # | Question | Recommended default |
| :-- | :--- | :--- |
| **O1** | The folder is named *Ollama* but the plans are Gemini-only. Should a **local Ollama provider** be supported behind the Model Gateway (offline / dev / privacy mode)? | Not in MVP scope; keep the gateway provider-agnostic so it can be added later without redesign. |
| **O2** | **Identity:** own accounts (hashed passwords) or an external IdP? | Google OIDC — Google OAuth scopes are needed for Gmail/Calendar anyway; `hashed_password` stays nullable. |
| **O3** | **Team size and target date** — the 22-week plan assumes unstated staffing. | Provide team size to turn the phases into a dated schedule; P5/P6/P7 can run in parallel with ≥ 3 engineers. |
| **O4** | **Hosting target** for the MVP: cloud or local desktop first? | Cloud (specs' recommendation); desktop after MVP. |
| **O5** | **Should voice be allowed to approve** low-risk actions later? | Not until speaker verification exists; revisit post-MVP. |
| **O6** | **Budget ceiling** per user per month. | Decide after the Phase-2 token baseline; sets per-task caps. |

---

## 26. Post-MVP roadmap

1. **Browser agent** with `@playwright/mcp` in the isolated container (egress proxy, vault-injected credentials, CAPTCHA hand-off).
2. **Remaining integrations:** Slack, Linear, Jira/Confluence, read-only Postgres; workflows 2, 4, 8–15.
3. **Proactive ambient mode:** webhook/email/calendar monitors that notify by voice when attention is needed.
4. **Local inference option** (Gemma and/or Ollama) behind the Model Gateway for zero-egress workflows.
5. **Desktop shell (Tauri)** and local-first storage.
6. **Enterprise:** SAML/Okta SSO, 99.95 % multi-AZ, Redis cluster, cold-archive tier.
7. **Agent swarms** for large parallel refactors; **generative UI** mini-apps on the canvas.

---

### Provenance
Consolidated on 2026-11-05 from `MAYA_AI_TECHNICAL_MASTER_SPECIFICATION.md` (v1.0.0-PROD-SPEC) and `MAYA_AI_TECHNICAL_SPECIFICATION_v2.0.md` (v2.0.0-PROD-RELEASE). External facts marked **[V]** were checked against the npm registry, PyPI, GitHub and Google's model documentation on that date.
