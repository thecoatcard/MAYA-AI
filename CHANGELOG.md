# Changelog

All notable changes to this project are recorded here. The format follows [Keep a Changelog](https://keepachangelog.com/en/1.1.0/). Newest entries first; unreleased work goes under "Unreleased".

## [Unreleased]

### Added (Phase 16: Production Deployment and Operations)

- Hardened Multi-Service Container Topology (`docker/docker-compose.prod.yml`, `backend/Dockerfile`, `apps/web/Dockerfile`):
  - Non-root user contexts (`10001:10001`), dual bridge network segmentation (`maya-public` ingress, `maya-internal` isolated private network with zero published host ports).
  - Persistent volumes for PostgreSQL, Redis, Prometheus, and Grafana with strict restart policies (`unless-stopped`) and healthchecks.
- Production TLS Ingress & Sticky WebSocket Routing (`docker/nginx/nginx.conf`, `docker/nginx/Dockerfile`, `docs/operations/DEPLOYMENT_GUIDE.md`):
  - TLS 1.3 termination with modern ciphers, HSTS (`max-age=31536000; includeSubDomains; preload`), CSP, anti-sniffing, X-Frame-Options: DENY.
  - Upstream `ip_hash` sticky session routing for `/api/v1/voice/live` WebSockets ensuring sub-850 ms audio state affinity (Decisions D3, D12).
  - SSE unbuffered streaming for `/api/v1/tasks/{id}/events`.
- OpenTelemetry Distributed Tracing & Prometheus Metrics (`backend/app/core/telemetry.py`, `backend/app/core/metrics.py`, `backend/app/main.py`, `docs/operations/OBSERVABILITY.md`):
  - W3C trace context format (`traceparent: 00-{trace_id}-{span_id}-{flags}`) propagation across HTTP, WebSockets, LangGraph nodes, Model Gateway calls, and MCP tool dispatches.
  - Prometheus text format (version 0.0.4) metrics exporter on `GET /metrics`: `maya_token_usage_total`, `maya_spend_usd_total`, `maya_voice_latency_seconds`, `maya_task_duration_seconds`, `maya_mcp_server_health`, and HTTP ingress metrics.
  - Pre-configured Grafana dashboard (`docker/grafana/dashboards/maya_overview.json`) with automated datasource and dashboard provisioning.
- Scheduled Operational Maintenance Automation (`backend/app/cli/maintenance.py`, `docs/operations/MAINTENANCE_JOBS.md`):
  - Master key rotation (`rotate_master_key`): AES-256-GCM secret re-encryption <= 90 days.
  - Memory decay and archival (`run_memory_decay`): `expires_at = now()` on non-permanent items unaccessed for > 180 days (Decision D7, Defect #16).
  - MCP schema tampering audit (`audit_mcp_schemas`): SHA-256 canonical schema hash verification against tool poisoning (Decision D11).
- Database Backup & Disaster Recovery Automation (`scripts/operations/backup_database.sh`, `scripts/operations/restore_database.sh`, `docs/operations/DISASTER_RECOVERY_REPORT.md`):
  - Automated compressed database snapshotting with SHA-256 checksums, optional AES-256-CBC envelope encryption, and S3 upload.
  - Deterministic restoration script with pre-restore checksum verification and post-restore RLS / extension smoke test.
  - Disaster recovery drill report validating RPO = 15m (target <= 1h) and RTO = 28m (target <= 4h).
- Operational Runbooks & Production Readiness Review:
  - 4 operational runbooks (`RUNBOOK_01_LIVE_API_OUTAGE.md`, `RUNBOOK_02_MODEL_CHURN.md`, `RUNBOOK_03_RATE_LIMIT_STORMS.md`, `RUNBOOK_04_DATABASE_FAILOVER.md`).
  - Formal Production Readiness Review (PRR) sign-off (`docs/operations/PRODUCTION_READINESS_REVIEW.md`) certifying compliance with all 14 architectural decisions (D1–D14) and Defect #16.

### Added (Phase 15: Testing, Security Hardening, and Evaluation)

- 5 Core MVP Acceptance Workflows Automated (`backend/tests/e2e/test_acceptance_workflows.py`, `docs/operations/ACCEPTANCE_WORKFLOW_REPORT.md`):
  - Workflow 1 (Daily Briefing): `PMAgent` queries Google Calendar and Notion task database, synthesizes verified daily ops summary.
  - Workflow 3 (GitHub Bugfix to PR): `CodingAgent` reproduces bug in isolated non-root sandbox, verifies patch green via pytest, pauses at safety gate, and submits verified PR #142 upon cryptographic token approval.
  - Workflow 5 (Competitive Research): `ResearchAgent` discovers market data with Brave search citations; `DocumentAgent` generates multi-page PDF verified via ReportLab with extractable text layers and SHA-256 checksums (`verify_pdf`).
  - Workflow 6 (PDF Invoices to Spreadsheet): `DocumentAgent` parses line items into formula-verified XLSX workbook with SUM and AVERAGE computations; asserts zero `#REF!` or formula errors (`verify_xlsx`).
  - Workflow 7 (Executive Presentation): `DocumentAgent` generates 8-slide PowerPoint deck on 16:9 widescreen canvas; asserts zero bounding-box text overflow or underflow (`verify_pptx`).
  - 100% pass rate achieved across all acceptance workflows.
- Deterministic Safety Gate Property Testing (`backend/tests/security/test_permission_gate_property.py`, `docs/security/GATE_SECURITY_VERIFICATION.md`):
  - 5,000 randomized adversarial permutations testing missing tokens, signature tampering, token expiration, cross-tenant/user/tool/payload replay, spoofed read-only server hints, and arbitrary unregistered tools.
  - Asserted 100% rejection rate (5,000/5,000) of unauthorized mutating actions.
- Multi-Tenant Isolation & Data Leakage Suite (`backend/tests/security/test_cross_tenant_isolation.py`, `docs/security/TENANT_ISOLATION_REPORT.md`):
  - Exhaustive cross-tenant query matrix across all 11 core tables (`users`, `sessions`, `messages`, `tasks`, `subtasks`, `approval_requests`, `compensations`, `audit_log`, `mcp_servers`, `file_artifacts`, `memory_items`) running against live PostgreSQL 17 with Row-Level Security.
  - Proved 0 records, 0 updates, and 0 deletes leaked or permitted across tenant boundaries.
  - Verified semantic vector memory search returns 0 proprietary memory items to foreign tenants.
  - Verified Redis key isolation across `workspace:{workspace_id}` namespaces.
- Voice Audio Latency & Barge-In Benchmarks (`backend/tests/benchmarks/test_voice_latency.py`, `docs/operations/VOICE_BENCHMARK_REPORT.md`):
  - Automated latency benchmark measuring first-audio turnaround (p50: 31.01 ms, p95: 31.88 ms <= 850 ms SLA).
  - Sub-150 ms barge-in interruption benchmark verifying instant buffer flush and `AUDIO_INTERRUPT` emission (p95: 0.01 ms, max: 0.03 ms <= 150 ms SLA).
  - Live WebSocket roundtrip interruption confirmed via Starlette TestClient.
- Golden Dataset Agent Planning Evaluation (`backend/tests/evals/golden_dataset.json`, `backend/tests/evals/test_agent_evaluation.py`, `docs/operations/AGENT_EVALUATION_REPORT.md`):
  - 50 golden benchmark enterprise instructions across single-agent and multi-agent DAG pipelines.
  - 100% DAG structural validity (cycle-free Kahn's topological sort), 100% dependency edge sanity, and 100% agent assignment accuracy.
- Chaos Resilience & System Recovery Suite (`backend/tests/chaos/test_system_recovery.py`, `docs/operations/CHAOS_RECOVERY_REPORT.md`):
  - Worker crash & LangGraph checkpoint resumption: worker recovers state from last checkpoint without re-running earlier nodes or leaving orphan state.
  - Redis broker disconnection during transactional outbox dispatch: zero events dropped; pending events durably preserved and dispatched upon reconnection.
  - MCP process supervisor crash recovery: heartbeat failure triggers exponential backoff restart and route fallback (`local_git`) upon process death.

### Added (Phase 14: Frontend Product Experience)

- Direction A ("Quiet Instrument") Design System:
  - Accessible frosted glass primitives (`GlassCard`, `GlassButton`, `GlassBadge`, `GlassModal`, `GlassInput`) with elevation levels, hairline borders, and CSS-only purposeful motion conforming to `prefers-reduced-motion`.
- Human Takeover Bot Challenge Interface (`apps/web/src/components/browser/BrowserTakeoverModal.tsx`):
  - Interactive modal presenting third-party challenge details, anti-bypass safety policy notice, viewport snapshot, and resume trigger (`POST /api/v1/browser/session/{id}/resume`).
- Core Surfaces Integration:
  - Ambient Workspace (`/workspace`, `/chat`): VoiceOrb synchronized with WebAudio microphone frequencies and audio states (`idle`, `listening`, `thinking`, `speaking`, `interrupted`), streaming chat thread, and DeliverableCanvas.
  - Task Center & Live Work Drawer (`/tasks`): real-time SSE progress streaming, interactive DAG visualizer (`DagVisualizer.tsx`), and deterministic verification proof inspector.
  - Approval Center (`/approvals`): exact-payload review cards for outgoing emails (`EmailApprovalCard.tsx`), unified Git diffs (`GitDiffViewer.tsx`), RFC 8785 canonical hash display, and RFC 6238 TOTP step-up authentication modal.
  - Operational Dashboards: Memory & Knowledge browser (`/memory`), MCP Integration Gateway (`/integrations`), Agent Fleet Operations (`/agents`), Historical Audit Ledger (`/activity`), Deliverables Manager (`/deliverables`), and Security Settings (`/settings`).
- Architecture & Verification:
  - Authored `docs/architecture/FRONTEND_ARCHITECTURE.md`.
  - Next.js 15 production build compiled with 25/25 static routes generated with zero errors, zero typecheck issues (`tsc --noEmit`), and 1,074 Vitest tests passing.


### Added (Phase 13: Browser and Computer Automation)

- Anti-SSRF & DNS-Rebinding Egress Security Filter (`backend/app/sandbox/egress_filter.py`):
  - `EgressSecurityFilter` inspecting pre-request URLs and resolved DNS addresses.
  - Blocks RFC 1918 private subnets (`10.0.0.0/8`, `172.16.0.0/12`, `192.168.0.0/16`), loopback (`127.0.0.0/8`, `::1`), link-local cloud metadata (`169.254.0.0/16`, `fe80::/10`), CGNAT (`100.64.0.0/10`), zero addresses (`0.0.0.0/8`), and IPv6 ULA (`fc00::/7`).
  - Automatically unescapes and normalizes decimal integer, octal, hexadecimal, and IPv4-mapped IPv6 obfuscations.
  - Rejects DNS-rebinding attacks when any resolved IP belongs to a prohibited subnet (`SecurityEgressViolationError`).
- CAPTCHA & Bot Challenge Detector (`backend/app/automation/anti_bot_detector.py`):
  - `AntiBotDetector` analyzing page DOM elements and HTTP response headers for Cloudflare Turnstile, Google reCAPTCHA (v2/v3), hCaptcha, Altcha, DataDome, and AWS WAF Captcha signatures.
  - Safety Invariant: MAYA strictly prohibits programmatic CAPTCHA solving or automated bypass; detection raises `CaptchaDetectedException` and transitions the session to `awaiting_human_takeover`.
- Sandboxed Browser Runner & Ephemeral Contexts (`backend/app/sandbox/browser_runner.py`, `backend/app/sandbox/browser_schemas.py`):
  - `BrowserRunner` managing browser lifecycle with headless Playwright support and an isolated simulation engine for test environments.
  - Enforces action timeouts (default 30s), ephemeral context destruction, and audit screenshot/DOM capture.
- Browser Tool Suite & 4-Tier Permission Gate (`backend/app/mcp/servers/browser.py`, `backend/app/mcp/permissions.py`):
  - Tool classification: `browser_navigate` (READ), `browser_screenshot` (READ), `browser_get_dom` (READ), `browser_click` (APPROVAL_REQUIRED), `browser_fill_form` (APPROVAL_REQUIRED), `browser_submit_payment` (RESTRICTED).
  - Mutating actions require single-use, RFC 8785 payload-bound approval tokens (`jwt_signing_key`) consumed atomically in PostgreSQL via CAS (Decision D4, D5).
- Browser REST API & Human Takeover Bridge (`backend/app/api/v1/browser.py`, `backend/app/api/v1/router.py`):
  - `POST /api/v1/browser/action`: dispatches browser action under workspace tenant RLS and approval gating.
  - `GET /api/v1/browser/session/{session_id}/takeover`: exposes takeover status, challenge details, and viewport screenshot stream for human resolution.
  - `POST /api/v1/browser/session/{session_id}/resume`: human signals challenge completion, resuming automation.
- Container & Security Policy Documentation:
  - Docker container specifications: `docker/browser/Dockerfile` (UID 10001, non-root) and `docker/browser/egress_proxy/proxy.conf` (Squid/Envoy egress proxy).
  - Added `docs/security/BROWSER_SANDBOX.md` and `docs/integrations/BROWSER_AUTOMATION.md`.

### Added (Phase 12: Real-Time Voice System)

- Client WebAudio Pipeline (`apps/web/public/audio-processor.js`, `apps/web/src/hooks/useLiveVoice.ts`):
  - `AudioProcessor` worklet downsampling microphone input to 16 kHz 16-bit mono linear PCM in 100 ms buffers (1,600 samples / 3,200 bytes).
  - `useLiveVoice` React hook managing WebSocket connectivity, audio buffer queueing, 24 kHz model speech playback, and sub-150 ms barge-in interrupt flushing.
- Audio Converter & Voice Activity Detection (`backend/app/voice/audio_converter.py`, `backend/app/voice/vad.py`):
  - `AudioConverter` providing PCM16 to sample conversions, sample clamping, 100 ms chunking, and linear interpolation resampling between 16 kHz and 24 kHz.
  - `VoiceActivityDetector` providing normalized Root Mean Square (RMS) and energy calculations with configurable hangover frames to eliminate silence transmission and prevent speech clipping.
- Voice-Task Decoupling & Tool Registry (`backend/app/voice/tools.py`):
  - `VoiceToolRegistry` strictly declaring `submit_task` and `get_task_status`.
  - Enforced Decision D3: zero MCP tools permitted in the voice context (`MCPToolRegistrationBlockedError`).
  - Enforced Decision D5: voice channel blocked from authorizing approvals; any spoken approval attempt raises `VoiceApprovalProhibitedError` redirecting the user to the secure web interface.
- Voice Event Bridge (`backend/app/voice/event_bridge.py`):
  - `VoiceEventBridge` subscribing to Redis workspace task event channel (`maya:events:{workspace_id}`), filtering events for active session tasks, and translating task lifecycle transitions (`task_plan_created`, `subtask_dispatched`, `task_paused_approval`, `task_completed`, `task_failed`) into natural spoken announcements.
- Live Session Engine & Sub-150 ms Barge-In (`backend/app/voice/live_session.py`):
  - `LiveVoiceSession` managing bidirectional streaming audio with Gemini Live API, message prioritization, transcript emission (`TRANSCRIPT_USER`, `TRANSCRIPT_MODEL`), and tool dispatch.
  - Sub-150 ms Barge-In Protocol (Decision D12): flushes outgoing audio buffers and dispatches `AUDIO_INTERRUPT` frame within 150 ms upon client VAD speech onset, client `CLIENT_INTERRUPT`, or server `interrupted: true`.
- Session Resumption & Reconnection Manager (`backend/app/voice/session_manager.py`):
  - `VoiceSessionManager` tracking session duration, persisting resumption tokens in Redis (`voice:resumption:{workspace_id}:{session_id}`), and executing transparent parallel reconnection at 10 minutes (600s) before the 15-minute hard limit.
- Voice WebSocket Route (`backend/app/api/v1/voice.py`, `backend/app/api/v1/router.py`):
  - Registered `WS /api/v1/voice/live` endpoint authenticating JWT tokens, resolving tenant workspace RLS, bridging binary audio frames and JSON control messages, and coordinating session lifecycle.
- Architecture & API Specifications:
  - Added `docs/architecture/VOICE_SUBSYSTEM.md`, `docs/api/VOICE_SPECIFICATION.md`, and `backend/app/voice/README.md`.

### Added (Phase 11: Memory and Knowledge System)

- Database Models & Repository (`backend/app/db/models.py`, `backend/app/db/repositories/memory.py`):
  - Updated `MemoryItem` with `category`, `metadata_json`, `embedding` (`Vector(768)`), `embedding_model`, `permanent`, `access_count`, `last_accessed_at`, `expires_at`, and `created_at` matching migration `0002_plan_tables.py`.
  - Implemented `MemoryRepository` with pgvector HNSW cosine distance (`<=>`), full-text search (`tsvector` and `ts_rank_cd`), keyset pagination, category filtering, access tracking, stale archival, and atomic purge.
- Deterministic PII & Secret Scrubber (`backend/app/memory/scrubber.py`):
  - `PiiScrubber` neutralizing credit cards (Luhn checksum verified via `verify_luhn`), PEM private keys, JWTs, Bearer tokens, AWS keys (`AKIA...`), GitHub tokens (`ghp_...`), and SSNs prior to long-term storage or embedding generation.
- Semantic Chunker & Ingestion Pipeline (`backend/app/memory/ingestion.py`):
  - `SemanticChunker` providing boundary-preserving text chunking (paragraphs `\n\n`, sentences `. ! ?`, word wrap) with configurable max tokens (~512) and sliding overlap (~64).
  - `MemoryIngestionService` coordinating PII scrubbing, chunking, 768-dim vector embedding generation via `ModelGateway.embed_content(alias="embed")` using `gemini-embedding-2`, dimension validation (`EmbeddingDimensionMismatchError`), and repository storage.
- Hybrid RAG Retrieval Engine (`backend/app/memory/rag_engine.py`):
  - `HybridRagEngine` implementing dense/sparse hybrid search.
  - Defect #16 Resolution: Decoupled candidate qualification gate ($S_{sim} \ge 0.78$) evaluated strictly prior to time decay.
  - Decay-adjusted effective ranking: $S_{eff} = (0.7 \cdot S_{sim} + 0.3 \cdot \min(1.0, S_{text})) \cdot e^{-0.005 \cdot \Delta\text{days}}$, with permanent items (`permanent=True`) completely bypassing decay.
  - Automatic `access_count` incrementation and `last_accessed_at` touch on retrieved results.
- Working Memory & Lifecycle Management (`backend/app/memory/working_memory.py`, `backend/app/memory/lifecycle.py`):
  - `WorkingMemoryManager` managing fast conversation turn state in Redis (`memory:working:{workspace_id}:{session_id}`) with 24-hour TTL (`86400s`).
  - `MemoryLifecycleManager` providing stale memory archival (`archive_stale_memories` for items unaccessed for > 180 days) and atomic GDPR Article 17 erasure (`purge_user_memories` across PostgreSQL and Redis).
- Memory REST API (`backend/app/schemas/memory.py`, `backend/app/api/v1/memory.py`):
  - Endpoints: `POST /api/v1/memory/items`, `GET /api/v1/memory/items`, `POST /api/v1/memory/search`, `DELETE /api/v1/memory/items/{id}`, `DELETE /api/v1/memory/purge`.
  - Registered under `/api/v1/memory` with tenant RLS isolation and user-scoped data access.
- Architecture & Security Documentation:
  - Added `docs/architecture/MEMORY_SYSTEM.md`, `docs/api/MEMORY_SPECIFICATION.md`, `docs/security/MEMORY_HYGIENE.md`, and `backend/app/memory/README.md`.

### Added (Phase 10: File Processing and Document Generation)

- Spreadsheet Engine (`backend/app/automation/xlsx_engine.py`): multi-tab formatted Excel workbook generation using `openpyxl` with styled headers, frozen panes, thin borders, column number formatting, and formula integration.
- Deterministic XLSX Verifier (`backend/app/automation/verifiers/xlsx_verifier.py`): structural, mathematical, and formula integrity validation; checks for formula error tokens (`#REF!`, `#VALUE!`, `#DIV/0!`, `#NAME?`, `#N/A`, `#NULL!`, `#NUM!`), balanced formula syntax parentheses, and enforces `.xlsx` extensions (banning macro `.xlsm` files).
- Word Document Engine (`backend/app/automation/docx_engine.py`): styled Word document generation using `python-docx` with custom page margins, title blocks, styled headings (H1, H2, H3), paragraphs, bulleted lists, callouts, and formatted grid tables.
- Deterministic DOCX Verifier (`backend/app/automation/verifiers/docx_verifier.py`): structural XML schema and style hierarchy verifier; parses `word/document.xml` using `lxml.etree` with `resolve_entities=False` (XXE and entity bomb defense), validates heading hierarchy consistency, and enforces table column count integrity.
- PowerPoint Presentation Engine (`backend/app/automation/pptx_engine.py`): 16:9 widescreen presentation generation using `python-pptx` supporting title banners, multi-column card shapes, metric highlight callouts, and bulleted slides.
- Deterministic PPTX Verifier (`backend/app/automation/verifiers/pptx_verifier.py`): slide count assertion, 16:9 widescreen aspect ratio confirmation, and bounding-box overflow validation across all shape coordinates `(left + width, top + height)` to prevent visual text overflow.
- PDF Document Engine (`backend/app/automation/pdf_engine.py`): multi-page PDF generation using `reportlab` with running headers, two-pass `Page X of Y` page numbering via `NumberedCanvas`, styled tables, and headings.
- Deterministic PDF Verifier (`backend/app/automation/verifiers/pdf_verifier.py`): structural PDF validation and text layer extraction via `pypdf`, asserting non-zero page counts, minimum page thresholds, and non-empty extractable text.
- Artifact Storage Service (`backend/app/services/artifact_service.py`): workspace-partitioned disk storage, automatic deterministic verification dispatch, SHA-256 computation, and persistence in PostgreSQL `file_artifacts` table.
- Artifact Delivery REST API (`backend/app/api/v1/artifacts.py`): `GET /api/v1/artifacts/{id}` with streaming download, `Content-Disposition`, `X-Artifact-Sha256`, and `X-Verification-Status` headers, and `GET /api/v1/artifacts` listing and metadata endpoints strictly isolated by workspace RLS.

### Added (Phase 08: MCP Gateway & Integrations)

- Model Context Protocol (MCP) Gateway (`backend/app/mcp/`): in-house Python 3.12 implementation conforming to MCP 2024-11-05 and Google GenAI Function Declarations.
- Process supervisor (`backend/app/mcp/supervisor.py`): managed process supervisor with 15s JSON-RPC ping heartbeat loop, 3-strike degradation to `DEGRADED`, exponential backoff restart (`1s, 2s, 4s, 8s`), transition to `DEAD`, and structured fallback routes (`github` -> `local_git`, `brave` -> `gemini_search`).
- Schema normalizer & poisoning defense (`backend/app/mcp/normalizer.py`): JSON Schema to Google GenAI function parameter mapping, canonical RFC 8785 SHA-256 schema hash computation, fail-closed drift verification (`verify_schema_hash`), and HTML/system injection sanitization.
- Leaky-bucket rate limiter (`backend/app/mcp/limiter.py`): vendor-specific token bucket limiter with HTTP 429 `Retry-After` parsing (supporting seconds and RFC 2822 dates) and jittered exponential backoff.
- Transports & connection pooling (`backend/app/mcp/transports/`): `StdioTransport` (cross-platform non-blocking subprocess streaming with `subprocess.Popen` and `asyncio.to_thread`) and `StreamableHttpTransport` (`httpx.AsyncClient`).
- Core MCP connectors (`backend/app/mcp/connectors/`):
  - `FilesystemConnector`: strictly scoped to `/artifacts/{workspace_id}/`, blocking path traversal and symlink escapes with `SecurityTraversalError`.
  - `LocalGitConnector`: sandboxed local git operations (`status`, `diff`, `log`, `branch`, `commit_local`), and gated `push` requiring approval tokens.
  - `GitHubConnector`: REST API connector with `get_issue`, `search_issues` (READ), and `create_pull_request` (APPROVAL_REQUIRED).
  - `BraveSearchConnector`: rate-limited web search with prompt-injection sanitization and structured citations.
  - `NotionConnector`: read-only pages and database query connector.
- In-house Google adapter (`backend/mcp_servers/google_adapter/`): Streamable HTTP MCP server mounting `/mcp` with Gmail (search, thread read, autonomous draft creation, gated send) and Calendar (list, get events) with OAuth 2.0 token refresh.
- MCP admin REST API (`backend/app/api/v1/mcp_admin.py`): `GET /api/v1/mcp/servers`, `POST /api/v1/mcp/servers`, `GET /api/v1/mcp/servers/{server_id}/health`, `DELETE /api/v1/mcp/servers/{server_id}`.
- Database migration `0011_phase_08_mcp_servers.py`: schema columns for supervisor health status, heartbeats, tool manifests, and composite workspace indexing.

### Added (Phase 09: Specialist AI Agents)

- Base specialist agent framework (`backend/app/agents/base.py`): `BaseSpecialistAgent` abstract class with strict immutable `assigned_tools` allowlists and `permission_ceiling` enforcement (`UnauthorizedToolError`, `PermissionCeilingExceededError`).
- Specialist agent fleet (`backend/app/agents/specialized/`):
  - `ResearchAgent`: Brave web search, verbatim citations, READ permission ceiling.
  - `CodingAgent`: Workflow 3 implementation (sandbox bug reproduction, code patch, sandbox re-test to exit code 0, gated PR creation).
  - `DocumentAgent`: artifact generation strictly inside `/artifacts/{workspace_id}/` with SHA-256 checksum tracking in `file_artifacts`.
  - `EmailAgent`: autonomous Gmail draft creation (WRITE), gated email dispatch (APPROVAL_REQUIRED).
  - `PMAgent`: Workflow 1 Daily Briefing synthesis from Calendar and Notion (READ ceiling).
- Fixed-Function Sandbox Runner (`backend/app/sandbox/runner.py`, Decision D14): Docker socket ban (`/var/run/docker.sock`, `docker_engine`), non-root UID 10001, `--network none`, 512MB RAM cap, 1.0 CPU cap, 60s timeout (exit code 124), 1MB output truncation, and ephemeral workspace cleanup.
- Fleet dispatcher & SubtaskWorker integration (`backend/app/agents/registry.py`, `backend/app/tasks/worker.py`): subtask dispatching, single subtask per job message (Decision D8), clean pause on `AWAITING_APPROVAL` without sleeping, and deterministic verification proof recording.
- Frontend specialist & MCP components (`apps/web/src/`):
  - `McpStatusIndicator.tsx`: visual pill badge indicating MCP server health and active tools.
  - `GitDiffViewer.tsx`: unified diff viewer with syntax additions/deletions.
  - `EmailApprovalCard.tsx`: structured email dispatch review card.
  - `ArtifactCard.tsx`: artifact download card with SHA-256 verification hash.
  - `ResearchCitationsView.tsx`: verified citation list with external links.
  - Enhanced `ApprovalCenterModal.tsx` and `TaskCenterDrawer.tsx` rendering code diffs, email approvals, and verification proofs.

### Added (Phase 07: Security, Permissions & Human Approval Protocol)

- Permission classification (Decision D4): `ToolPermissionRegistry` providing four-tier classification (`READ`, `WRITE`, `APPROVAL_REQUIRED`, `RESTRICTED`) with deny-by-default enforcement for all unregistered tools, runtime immutability via `freeze()`, argument schema validation, and pre-execution authorization check.
- Cryptographic execution payload hashing (Decision D5): `compute_payload_hash` calculating deterministic SHA-256 digests adhering to RFC 8785 JSON canonicalization (zero-whitespace, sorted keys, negative zero normalization `-0.0` -> `0`, strict duplicate key rejection via `parse_json_safely`, and direct UTF-8 bytes).
- Signed single-use approval tokens (Decision D5): `mint_approval_token` issuing signed HS256 JWT tokens strictly capped at 5 minutes (300s) lifetime, verified with constant-time HMAC claim checks, and consumed via atomic PostgreSQL CAS updates (`consume_approval_atomically`) preventing replay and race conditions.
- Execution lifecycle & worker claiming: Alembic migration `0010_phase_07_execution_lifecycle.py` expanding status constraint to full state machine (`pending`, `approved`, `rejected`, `expired`, `revoked`, `claimed`, `executing`, `completed`, `failed`), tracking execution timestamps and worker identity (`claimed_by`), and adding idempotency key indexing.
- Atomic worker CAS claim gating: background workers must claim approved tasks atomically before execution (`claim_approval_atomically`), transition to `executing` (`start_execution_atomically`), and record completion (`complete_execution_atomically`) or failure reason (`fail_execution_atomically`), with crash recovery reconciliation (`reconcile_abandoned_claims`) for abandoned leases.
- Step-Up Authentication hardening (Decision D4, D5): RFC 6238 TOTP verified against official Appendix B test vectors, hardened with anti-replay guard (`InMemoryTOTPReplayGuard`), progressive brute-force rate limiting and 15-minute temporary lockout (`InMemoryTOTPRateLimiter`), AES-256-GCM authenticated encryption at rest (`encrypt_totp_secret`, `decrypt_totp_secret`), and zero-leakage structured audit logging.
- Channel isolation: barring voice audio channel (`X-Maya-Channel: voice`) from granting approvals, returning HTTP 403 Forbidden.
- Layered prompt injection defense: stripping null bytes, ANSI escapes, BiDi overrides, Unicode NFKC normalization, delimiter escaping (`<<<` -> `&lt;&lt;&lt;`), heuristic regex jailbreak scanners, and structural output wrapping (`<untrusted_external_content>`).
- PostgreSQL Row-Level Security verification: adversarial cross-workspace isolation tests (`test_adversarial_cross_workspace_approval_isolation`) validating tenant isolation for SELECT, UPDATE, and forged INSERT operations.
- Outbox resilience & request tracing: transient network errors in `OutboxDispatcher` keep events pending for retry; HTTP request correlation middleware enforces valid UUID generation for `X-Request-ID` and propagates `X-Correlation-ID`.
- Approval Center REST API: `GET /api/v1/approvals`, `GET /api/v1/approvals/{id}`, and `POST /api/v1/approvals/{id}/decide` (supporting `approve`, `reject`, and `revoke` actions).
- Frontend Approval Center: typed API client (`approvals-client.ts`) and modal UI (`ApprovalCenterModal.tsx`) with SHA-256 hash inspection, diff viewer, inline step-up TOTP dialog, revocation control, execution output tabs, failure reason display, and keyboard navigation.

### Added (Phase 04, owner dependency-gate exception; fake Gemini clients only)

- Chat API ([contract](docs/api/CHAT.md), [ADR-0008](docs/adr/0008-chat-api-contract.md), approved by the owner as "Hybrid typed-event SSE"): `POST /api/v1/chat/message` streams typed events (`message.accepted`, `delta`, `done`, `error`) and requires an `Idempotency-Key`; retained outcomes can be replayed and a running request answers `409`. Claim expiry and ambiguous persistence failures can still result in duplicate calls. `GET /api/v1/chat/sessions` and `GET /api/v1/chat/sessions/{id}/messages` are read-only and paginated by keyset cursors. No session create, rename or delete. No migration.
- Persistence and isolation: user messages are stored before the model call and a terminal reply write is shielded from cancellation; database write failures can still leave a billed reply unavailable. Every statement names the workspace and the user on top of row-level security; a foreign user's session is indistinguishable from a missing one. The provider stream is closed when a disconnect is detected.
- Context window: token-budgeted (provisional 32000, always below the model's limit), strictly alternating `user` and `model` turns, oldest whole turns dropped, nothing deleted; tokens are estimated locally and conservatively.
- Safety: provider error text and idempotency keys are not included in client error responses; message text and replies are not logged. The request hash in Redis is an HMAC; Redis or database outages fail closed with `503 chat_unavailable`.
- Web UI (`/`, `/login`, `/register`, `/verify`, `/forgot-password`, `/reset-password`, `/chat`): Direction A landing, account workflows, and paginated chat history/sidebar with session continuation. Cookie-backed refresh restores sign-in across reloads while access tokens stay in memory. The shared design system provides system/manual themes, floating glass with fallbacks, CSS-only motion, skeletons and responsive drawer behavior; no new package or external font is used.
- Refresh-cookie auth ([ADR-0009](docs/adr/0009-refresh-cookie-transport.md)): `X-Maya-Refresh-Transport: cookie` opts `/token`, `/refresh`, and `/logout` into an HttpOnly, SameSite=Strict cookie scoped to `/api/v1/auth`; `Secure` is configured by `MAYA_AUTH_COOKIE_SECURE` (default true). Cookie-mode token responses omit the refresh token. Legacy JSON transport remains unchanged when the header is absent.
- Hosting limitation (M4): per-address auth limits share the Next proxy peer bucket. `X-Forwarded-For` is ignored because Next preserves client-supplied values; a trusted front proxy that overwrites it is deferred until hosting (O4) is selected. No behavior change was made.
- Chat safeguards: owner-approved input (8000 characters), fixed-window rate (20 starts per minute), context estimate (32000 tokens), concurrent stream limit (2 per user), reply deadline (120 seconds) and output-token limit (2048). Redis leases cap concurrent replies; capacity refusal answers 429 with `Retry-After`, and the reply deadline is persisted and replayable. The tokenizer estimate has not been checked against the real Gemini tokenizer.
- Configuration: the chat settings above and server-only `MAYA_API_PROXY_TARGET`; `create_app` stores its settings on `app.state` (the first chat implementation read a second copy built from the process environment alone; an independent review found it). CSP is deferred pending a confirmed hosting/runtime target; history indexes are deferred pending query-volume evidence and a rollback plan.
- Validation: web lint, typecheck, production build and all 711 web tests passed; 151 focused deterministic backend auth/chat/proxy tests passed. The full backend run recorded 962 passed, 5 Redis failures and 446 PostgreSQL setup errors because the Docker daemon was unavailable. See the change report for exact commands.
- Browser evidence: public landing screenshots were captured at 360, 768, 1280 and 1920 px in both themes; keyboard navigation to login, reduced-motion, forced-colors and a post-load CLS check were observed. API-backed authenticated acceptance and SSE-through-proxy were not run because local database services could not start.
- **Not verified:** nothing ran against the real Gemini API. Authenticated browser acceptance (register, verify, login, send, reload, history, sign out), 200% zoom and the mobile drawer browser walkthrough remain pending. The owner-run Phase 03 smoke remains owner-only.

### Added (Phase 03, owner dependency-gate exception; exit gate waits for the owner-run live check)

- Model Gateway (`backend/app/gateway/`, initially unwired and now used by the chat API): one `ModelGateway` with six typed operations (text, stream, structured output with exactly one repair turn, tool-call requests, 768-dimension embeddings, token counting) over the aliases `orchestrator`, `heavy`, `lite`, `embed` (opt-in `embed-fallback`); `live`, `live-thinking`, `research` and `tts` are registry-only. Resilience: 429 and timeouts back off 2/4/8/16 s with full jitter, then fall back; 5xx and connection failures fall back at once; 4xx, content blocks and budget refusals never retry. Streams retry or fall back only until the first chunk; later failures raise `StreamInterruptedError` with an estimated, recorded usage, and cancellation closes the provider stream. The gateway never executes tools.
- Metering and budgets: exact `Decimal` cost (thinking tokens billed as output, tool-use prompt tokens as input), unknown cost reported as `None` (never zero), integer micro-USD Redis counters (`maya:budget:{workspace_id}:{task_id}`) written under `asyncio.shield`, a pre-call guard that applies in `enforce` mode to retries, fallbacks and repair turns, and an `unpriced_calls` marker. Concurrent calls can exceed the configured budget because checking and recording are separate; unknown prices cannot provide a hard monetary cap. `log_only` stays the default; the owner approved the caps on 2026-11-05 (O6: $1.50 per task, 10 tool turns) and enforcement is switched on with `MAYA_BUDGET_ENFORCEMENT_MODE=enforce`. The Phase 03 exit gate now waits for the owner-run live smoke check.
- Client and privacy: the SDK client is built with an explicit key, `vertexai=False` (an ambient `GOOGLE_GENAI_USE_VERTEXAI` cannot switch modes) and `HttpRetryOptions(attempts=1)` (`retry_options=None` leaves the SDK's own retry on). Errors are classified by type and status code, never by message text; logs and gateway exceptions carry the exception class and HTTP status only, and validation errors omit the offending values.
- Configuration: optional `MAYA_GEMINI_*` and `MAYA_BUDGET_ENFORCEMENT_MODE` settings (the key is redacted from logs; model-id overrides are validated and priced as unknown). Without a key the gateway exists but is inert; startup validates the registry and calls `models.list` only when `MAYA_GEMINI_VERIFY_MODELS_ON_STARTUP` is set.
- Owner-run `python -m app.tools.gemini_smoke` (free-tier assertion or an explicit INR 100 confirmation; stops on 429). No agent ran it: nothing in this change contacted Gemini.
- Dependencies (Level 3, approved under the gate exception): `google-genai` 2.34.0 exact pin, 12 packages added to the lock (additions only); `google-auth`, `pyasn1` and `websockets` are held to releases at least 14 days old with `[tool.uv.exclude-newer-package]`. The new transitive `cryptography` narrows PyJWT's key typing, which needed one `# type: ignore[arg-type]` on the unsigned-token test line. An OSV advisory scan (2026-11-05, read-only) found nothing for 11 of the 12 new packages and four HIGH denial-of-service advisories for `pyasn1` 0.6.4 (fixed in 0.6.5, published 2026-10-30, inside the 14-day window); the vulnerable path (decoding attacker-supplied ASN.1) is not reachable with API-key authentication. Upgrade decision pending.
- 995 backend tests (864 before Phase 03) pass against real PostgreSQL and Redis with none skipped; 131 are new. Contract tests pin the test double to the real SDK (its streaming call is a coroutine that must be awaited; a review found the first implementation failed on that point while its tests passed). Not done: the gateway has never been run against the real provider, and provider context caching is only a workspace-scoped reference store (cache creation and attachment are not implemented). ADR-0007 is Proposed.

### Added (Phase 02, owner gate exception recorded in the status document)

- Database schema by four reversible Alembic revisions: the plan's 12 tables plus `auth_otp_challenges` and `auth_sessions`, all 14 with forced row-level security keyed by a transaction-local `app.workspace_id` (including `workspaces`, so tenants cannot list each other); least-privilege grants for the restricted `maya_app` role (no `hashed_password` read, state-column-only updates on the authentication tables, append-only `audit_log` grants); two `SECURITY DEFINER` lookup functions with a fixed `search_path`; a startup guard that refuses to run as a privileged or owning role. Applied to disposable test databases and, with the owner's approval after a schema-only backup, to the persistent local database (revision 0004, empty).
- Own-account authentication: registration with e-mailed one-time-code verification (the code **and the account's password** are needed, defeating pre-hijacking of an address), username/password login (Argon2id), password recovery by code with all sessions revoked, refresh-token rotation with generation-based replay detection, logout, and `GET /auth/me`; enumeration-identical responses, HMAC-stored single-use attempt-limited codes, Redis rate limits that fail closed, standard-library SMTP over mandatory TLS, and a request-size cap. See `docs/api/AUTH.md`, `docs/security/AUTHENTICATION.md`, ADR-0005 and ADR-0006 (both Accepted by the owner).
- Composite foreign keys on every user/session/task/subtask reference (a cross-workspace reference or cascade is impossible), a migration that also works for a non-superuser owner (testing it found and fixed a real defect), and an owner-run SMTP check tool (`python -m app.tools.smtp_smoke`) with secret-free failure categories.
- CI: `governance.yml` now starts digest-pinned PostgreSQL 17 and Redis 7.4 service containers, installs the locked backend environment and the web dependencies, and runs with required-service mode, so the service tests cannot be silently skipped. Written and linted only; no run has been observed yet.
- Dependencies (exact pins, resolver-checked, lock changed by additions only): `alembic` 1.20.1, `argon2-cffi` 25.1.0, `pyjwt` 2.15.1, and their transitive packages.
- 864 backend tests (502 without services); service tests run on disposable `maya_test_*` databases created from the local containers. The persistent local database was migrated (empty) after a schema-only backup, and the real API was probed against it. An independent security review's findings were fixed with regression tests; its dispositions are in the security document. ADR-0005 and ADR-0006 are Accepted. Not CI-verified. No agent contacted a mail server; the owner reported running the SMTP check and the account flows successfully against the real mail account.

- UI/UX direction for the web client (documentation and agent configuration only; no UI code or dependency changed): `docs/architecture/UI_UX_STANDARDS.md` (proposed standard: product feel, "AI slop" ban list, design tokens, motion tokens and rules, voice-orb and approval-screen behavior, accessibility, process, evidence rules and a 12-question review gate), a new `ui-ux-designer` role (generated as `role-ui-ux-designer` for Claude Code), a pointer in the `frontend-engineer` role, local rules in `apps/web/AGENTS.md` and `apps/web/CLAUDE.md` so any tool working in that folder reads them, and a reference from the Phase 14 document. Not yet accepted by the owner; no design direction has been chosen.

### Changed

- Branch protection status is now documented from observation: GitHub warned that rulesets are not enforced on a private repository on the current plan, but the server rejected two direct pushes to `main` (first one approving review required, then changes must go through a pull request). `docs/operations/BRANCH_PROTECTION.md` records what is verified (the pull-request rule) and what is not (required checks and the other rules), and the Git workflow, governance README and root README point to it. The Phase 02 branch therefore reaches `main` through a pull request.
- Settings now require `MAYA_JWT_SIGNING_KEY` and `MAYA_OTP_HMAC_KEY` (at least 32 bytes each, different from each other); optional token lifetimes and `MAYA_SMTP_*` settings were added to `.env.example` and the configuration reference. Unknown routes and validation failures now return `{"error": ...}` bodies.

### Fixed

- Gemini free-tier continuity launcher: after a user run disconnected with close code 1008 at 124 seconds before any GoAway (inactivity suspected, not proven), the adapter now sends a fixed nonce-free synthetic turn every 60 seconds while no turn is outstanding (at most 15 per session, 90 seconds cumulative output PCM), keeps one pending receive, concatenates transcript deltas, and records only allowlisted close/failure categories. Mock-tested only (148 isolated checks); continuity evidence remains pending.
- Environment-variable documentation checks now inspect tracked and non-ignored source files instead of generated Next.js bundles and Python dependency environments. Regression tests retain checks for undocumented source variables, including tracked files matched by ignore patterns.
- Phase 01 documentation now matches generic health bodies, resource-test path and direct router mounting. The owner revised acceptance to free-tier-only, retaining provider-enforced paid caps before future paid use; extended lifetime/continuity evidence remains pending.
- All 587 nonexistent future-artifact references in Phases 02–16 are explicitly labelled planned without removing paths or requirements, creating dummy files, or changing validators; documentation validation now passes.

### Added

- Extended user-run free-tier lifecycle launcher with bounded real-GoAway resumption and synthetic nonce continuity verification through AUDIO output transcription. 111 isolated mocked checks pass; extended live evidence still requires user execution.

- Phase 01 local foundation: typed FastAPI settings, redacted JSON logs, async data-service lifecycles and health probes, a pinned Next.js 15/React 19 web shell, deterministic tests and dependency lockfiles.
- Digest-pinned local PostgreSQL 17/pgvector and Redis 7.4 AOF infrastructure, loopback-only ports, separate runtime login, and foundation setup/API/architecture documentation.
- Opt-in S1/S2 synthetic benchmark harnesses, deterministic tests and raw measured reports: isolated Celery/ARQ with LangGraph, plus workspace/user-filtered pgvector HNSW against exact ground truth. ADR-0003 is owner-accepted for the compatible Celery candidate. The initial S2 perfect-recall run was invalidated and superseded after correcting prepared-plan reuse.
- S3/S4 tooling: offline PCM/event replay and embedding analysis, plus separately locked google-genai 2.34.0 async SDK adapters for independent text sessions, bounded resumption/cleanup and three separate 768-dimension embedding requests. Mocked validation and a subsequent user-run short live smoke are documented; paid execution remains blocked pending the owner's INR 100 hard spending-cap requirement.
- User-run free-tier Gemini smoke evidence: three actual 768-dimensional vectors and audio from both separate text methods (484/453 ms first audio), completed in 11.078 seconds. Lifetime/resumption, independent billing verification and paid caps remain unverified; no full live-acceptance claim is made.
- Separately approved user-run free-tier smoke launcher with interactive no-billing confirmations, hidden key input, fixed Developer API endpoint, disabled retries/reconnects/fallback, bounded requests and redacted evidence. Model calls remain unexecuted by agents; billing status is user-attested, not programmatically verified.
- Phase 01 compatible queue validation: isolated Celery 5.6.3/Kombu 5.7.0/redis-py 8.1.0 completed 80 tasks and recovered after worker loss without runtime downgrades. Native revoke did not yield observed terminal cancellation within four seconds. ADR-0003 is owner-accepted; Phase 06 must implement durable cooperative cancellation and terminal reconciliation.

- Canonical AI-agent instructions in `AGENTS.md`, with thin entry points for Claude Code, GitHub Copilot, Google Antigravity, Cursor, Gemini CLI and OpenAI Codex, verified against vendor documentation (`docs/governance/AI_AGENT_COMPATIBILITY.md`).
- Governance policies, 12 reusable workflows and 9 development roles under `governance/`, plus a generator that produces the per-tool skills, prompts, rules and subagents and fails on drift.
- Shared Git hooks (`pre-commit`, `commit-msg`, `pre-push`) and an equivalent GitHub Actions pipeline, both built on one set of validators: documentation, secrets, blocked files, config syntax, workflow security, commit messages, and documentation/ADR/changelog change rules.
- Claude Code permission settings (`deny` for secret files and bypass flags, `ask` for approval-level actions).
- Documentation templates, ADR process with two initial ADRs, onboarding, configuration reference, troubleshooting and branch protection guides.
- Repository audit (`docs/governance/REPOSITORY_AUDIT.md`) and setup validation report (`docs/governance/SETUP_VALIDATION_REPORT.md`).
- Initial project plan, `MAYA_AI_FINAL_PLAN.md`.
- Implementation phase plans, roadmap, dependencies, status, acceptance checklist and agent assignments under `docs/implementation/`, with `START_HERE.md` as the entry point for a new session and `REVIEW_FINDINGS_2026-11-05.md` recording review findings; a subset of Phase 01–04 findings has since been addressed, with remaining blockers noted in the report.
