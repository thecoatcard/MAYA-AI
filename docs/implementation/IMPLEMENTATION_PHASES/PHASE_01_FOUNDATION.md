# Phase 01 — Core Infrastructure and Application Foundation

## Implementation snapshot (2026-11-05)

**Implemented/Tested locally; short Gemini smoke measured; extended live evidence pending:** P01-T001 through T005
are implemented as the local foundation:
Compose data services, backend settings/logging/resource lifecycles/health routes,
and the Next.js overview. See the [runbook](../../operations/LOCAL_DEVELOPMENT.md)
and [test strategy](../../testing/FOUNDATION.md).

The router is mounted directly by the application factory; a separate router module
and unused frontend API client were not added. Resource checks are tested in the
resource integration suite. There are no application tables or migrations.

Tasks T006/T007 (S1/S2) now have implemented harnesses and measured synthetic reports:
[queue evaluation](../../architecture/SPIKE_S1_QUEUE.md) and
[filtered vector retrieval](../../architecture/SPIKE_S2_VECTOR.md).
The owner accepted ADR-0003: Celery 5.6.3 with Kombu 5.7.0 and redis-py 8.1.0
now has an actual isolated 80-task transport/recovery benchmark. Redelivery
completed, but no terminal cancellation was observed within four seconds.
Durable cooperative cancellation and terminal reconciliation are mandatory for
Phase 06; no runtime queue integration was added here.
ARQ 0.28.0 still conflicts with the runtime client pin; no runtime downgrade occurred;
see the [compatibility follow-up](../../architecture/SPIKE_S1_QUEUE.md#compatibility-follow-up).
Tasks T008/T009 (S3/S4) have offline fixture harnesses and isolated actual SDK
adapters with mocked deterministic tests:
[Live API readiness](../../architecture/SPIKE_S3_GEMINI_LIVE.md) and
[embedding readiness](../../architecture/SPIKE_S4_EMBEDDINGS.md).
The owner explicitly revised Phase 01 to Free-tier-only with no linked billing.
The combined INR 100 paid-cap condition remains mandatory before future paid
work, but is no longer a Phase 01 exit gate. No paid run is authorized.
A separately approved hidden-prompt
free-tier launcher was subsequently run by the user. It returned three
768-dimensional embedding vectors and audio for both independent text methods
(484/453 ms first audio). This verifies a bounded smoke, not full lifetime,
resumption/context continuity or independently verified billing.
The [deferred development record](../../architecture/SPIKE_S3_GEMINI_LIVE.md#deferred-development-record)
lists prerequisites and the evidence still needed before Gemini-dependent use.
Local closure validation passes: fast 8/0/0 and full 10/0/4 (pass/fail/skip);
146 backend tests pass. The original full-scope exit gate is not fully verified:
extended lifetime/resumption evidence is pending from the approved user-run test.
Paid-cap proof is not claimed.
The task descriptions below include planned
artifacts, not a claim that every named future file exists.

## A. Phase Overview

- **Phase Number:** Phase 01
- **Phase Name:** Core Infrastructure and Application Foundation
- **Objective:** Establish the local development foundation, scaffold the FastAPI modular monolith and Next.js 15 web client, configure PostgreSQL 17 (+pgvector) and Redis 7.4 (AOF persistence), set up configuration management and structured logging, implement non-sensitive health probes, and execute the four architectural spikes.
- **Why This Phase is Necessary:** Production-grade multi-agent and voice systems require stable runtime foundations, deterministic local services, and empirical verification of core platform assumptions (async queue behavior, vector index filtering, audio streaming constraints) before developing domain agents or data schemas.
- **Expected Outcome:** A documented and tested foundation for later application phases: Docker Compose configuration, FastAPI and Next.js scaffolds, health probes, and reproducible spike results. This phase alone does not deliver authentication, the complete RLS schema, the model gateway, or persisted chat.
- **Priority:** Critical
- **Estimated Complexity:** High
- **Estimated Duration:** 15 business days; the calendar target is unvalidated.

---

## B. Prerequisites

- **Required Completed Phases:** Phase 00 (Project Governance and Development Environment).
- **Required Infrastructure:** Docker Engine 26+ and Docker Compose v2; Python 3.12+; Node.js 22+; pnpm or npm.
- **Required Credentials or External Services:** Gemini credentials are needed only for live S3/S4 runs. Those runs contact an external service and require explicit approval before execution; deterministic tests must not require credentials or network access.
- **Required Architectural Decisions:** [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) Decisions D2 (Modular Monolith) and D9 (Single-node Redis with AOF persistence).
- **Required Documentation:** [`docs/architecture/SYSTEM_OVERVIEW.md`](../../architecture/SYSTEM_OVERVIEW.md), [`docs/architecture/REPOSITORY_STRUCTURE.md`](../../architecture/REPOSITORY_STRUCTURE.md).

---

## C. Scope

### Included Features

- Python backend setup with `pyproject.toml`; select exact dependency pins only after verifying compatibility, including the Gemini SDK/Pydantic constraints and the psycopg 3 driver required by the planned LangGraph Postgres checkpointer. Do not use open-ended version ranges.
- Next.js 15 web application scaffolding with TypeScript, Tailwind CSS, Lucide icons, and React 19.
- Local Docker Compose infrastructure: PostgreSQL 17 with pgvector and `pg_trgm`; Redis 7.4 configured with AOF persistence. Object storage is deferred until an approved phase has an identified consumer; MinIO is not included.
- Centralized configuration management using Pydantic Settings with `.env` loading and secret redaction.
- Structured JSON logging with correlation IDs (`trace_id`, `request_id`).
- Deep health and readiness endpoints (`/api/v1/healthz`, `/api/v1/readyz`) verifying database and Redis connectivity.
- Four technical spikes from [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §19:
  - Spike S1: Celery vs. ARQ performance, async compatibility, and task cancellation under asyncio + LangGraph.
  - Spike S2: Filtered HNSW vector search recall and latency constrained by both `workspace_id` and `user_id` in pgvector.
  - Spike S3: Gemini Live API session duration, resumption, separately exercised text-input methods, and first-audio latency. Do not mix `send_client_content` and `send_realtime_input` in the same session.
  - Spike S4: Verification of `output_dimensionality=768` parameter and vector precision on `gemini-embedding-2`.

### Excluded Features

- User authentication and session management (Phase 02).
- The application schema, authentication, and RLS (Phase 02).
- Model Gateway and persisted chat (Phases 03 and 04).
- Agent execution workflows and MCP integration (deferred to Phases 05 and 08).
- Audio streaming UI components (deferred to Phase 12).

### Deferred Features

- Production Kubernetes Helm charts (deferred to Phase 16).
- Redis Sentinel or Cluster deployment (explicitly deferred to post-MVP).

### Dependencies on Future Phases

- Phase 02 builds upon the PostgreSQL database setup and Pydantic configuration initialized here.
- Phase 03 uses the Gemini API credentials and model configuration validated in Spike S3/S4.
- Phase 06 relies on the task queue selection resolved in Spike S1.

---

## D. Implementation Breakdown

### Task P01-T001: Configure Docker Infrastructure and Local Services

- **Task ID:** P01-T001
- **Task Name:** Create Docker Compose and Service Configurations
- **Detailed Description:** Create a root-level Compose file for PostgreSQL 17 (using `pgvector/pgvector:pg17`) and Redis 7.4 (`redis:7.4-alpine` with AOF persistence). Configure local-only ports, named volumes, safe environment-variable references, and health checks. Do not add object storage in this phase.
- **Expected Files:** root-level `docker-compose.yml`, `docker/postgres/init.sql`, `.env.example`.
- **Dependencies:** None.
- **Assigned Agent Role:** DevOps Infrastructure Lead.
- **Required Tests:** `docker compose config`; starting containers and checking health is an infrastructure action and requires owner approval.
- **Documentation Updates:** `docs/guides/CONFIGURATION.md`, `docs/guides/ONBOARDING.md`.
- **Acceptance Criteria:** Compose configuration validates; with separate approval to start containers, PostgreSQL has the required extensions and Redis reports AOF enabled. No object-store service is required.

### Task P01-T002: Backend Application Scaffolding and Environment Configuration

- **Task ID:** P01-T002
- **Task Name:** Scaffold FastAPI Monolith and Pydantic Settings
- **Detailed Description:** Initialize `backend/` with `pyproject.toml` and Ruff/Mypy configurations. Implement settings and structured JSON logging with secret redaction. Document every environment variable manually in `.env.example` and the configuration reference. Keep migration and application database URLs/roles separate; schema and role details are completed in Phase 02.
- **Expected Files:** `backend/pyproject.toml`, `backend/app/main.py`, `backend/app/core/config.py`, `backend/app/core/logging.py`.
- **Dependencies:** P01-T001.
- **Assigned Agent Role:** Backend Systems Engineer.
- **Required Tests:** `pytest backend/tests/unit/test_config.py` and `ruff check backend`.
- **Documentation Updates:** `docs/architecture/REPOSITORY_STRUCTURE.md`.
- **Acceptance Criteria:** Settings validation fails clearly for missing or invalid required values; logs are structured and secrets are redacted; migration credentials are not used by the runtime application role.

### Task P01-T003: Database and Cache Connection Lifecycles

- **Task ID:** P01-T003
- **Task Name:** Establish Async SQLAlchemy Session and Redis Client Pools
- **Detailed Description:** Implement `backend/app/db/session.py` creating an async SQLAlchemy 2.0 engine using `asyncpg` with connection pooling (pool size 20, max overflow 10, pool pre-ping). Implement `backend/app/core/redis.py` creating an async Redis client. Implement FastAPI lifespan context manager handling graceful startup and shutdown.
- **Expected Files:** `backend/app/db/session.py`, `backend/app/core/redis.py`, `backend/app/main.py`.
- **Dependencies:** P01-T002.
- **Assigned Agent Role:** Backend Systems Engineer.
- **Required Tests:** `uv run --directory backend --no-sync pytest -q tests\integration\test_resources.py`; the resource suite uses mocks, and approved live smoke evidence is recorded separately in the test strategy.
- **Documentation Updates:** `docs/architecture/SYSTEM_OVERVIEW.md`.
- **Acceptance Criteria:** Application connects to Postgres and Redis on startup; connection errors trigger clean retry or shutdown; connections terminate cleanly on SIGTERM.

### Task P01-T004: Health Check and Observability Endpoints

- **Task ID:** P01-T004
- **Task Name:** Implement `/api/v1/healthz` and `/api/v1/readyz` Probes
- **Detailed Description:** Create `backend/app/api/v1/health.py` exposing liveness (`/healthz`) and readiness (`/readyz`) routes. Liveness verifies the web process is running. Readiness executes `SELECT 1` on PostgreSQL and `PING` on Redis, returning HTTP 200 with a generic status if healthy, or HTTP 503 if either dependency is degraded.
- **Expected Files:** `backend/app/api/v1/health.py`, `backend/app/main.py`.
- **Dependencies:** P01-T003.
- **Assigned Agent Role:** Backend Systems Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_health.py`.
- **Documentation Updates:** Health-probe API documentation, created when the API documentation directory is first needed.
- **Acceptance Criteria:** `/healthz` returns 200 immediately; `/readyz` returns 200 when Postgres and Redis respond, and a generic 503 when either is unavailable. Responses must not disclose connection strings, exception details, or other internals.

### Task P01-T005: Next.js 15 Web Application Scaffolding

- **Task ID:** P01-T005
- **Task Name:** Initialize Next.js 15 Web Client with Tailwind CSS
- **Detailed Description:** Scaffold `apps/web/` using a pinned Next.js 15 release, App Router, TypeScript, and Tailwind CSS. Configure `build`, `lint`, `typecheck`, and `test` scripts required by the repository stack checks. Create the root layout and basic navigation skeleton; do not imply authenticated chat UI is delivered in this phase.
- **Expected Files:** `apps/web/package.json`, `apps/web/tsconfig.json`, `apps/web/src/app/layout.tsx`, `apps/web/src/app/page.tsx`. Add an API client only when a consumer exists.
- **Dependencies:** P01-T001.
- **Assigned Agent Role:** Frontend Systems Engineer.
- **Required Tests:** `npm --prefix apps/web run lint`, `npm --prefix apps/web run typecheck`, `npm --prefix apps/web test`, and `npm --prefix apps/web run build`.
- **Documentation Updates:** `docs/architecture/REPOSITORY_STRUCTURE.md`.
- **Acceptance Criteria:** The web scaffold passes its declared stack checks. Do not use `next@latest`; resolve and pin a compatible Next.js 15 version before any dependency installation.

### Task P01-T006: Architectural Spike S1 — Celery vs. ARQ Evaluation

- **Task ID:** P01-T006
- **Task Name:** Execute Spike S1: Queue Runner under Asyncio and LangGraph
- **Detailed Description:** Create a reproducible benchmark harness comparing Celery and ARQ under the planned asyncio/LangGraph workload. Record the workload and environment, cancellation and redelivery behavior, event-loop impact, and observed resource use; do not present unmeasured thresholds as acceptance criteria.
- **Expected Files:** The S1 spike harness and a proposed ADR-0003 for task-queue selection.
- **Dependencies:** P01-T003.
- **Assigned Agent Role:** Senior Distributed Systems Architect.
- **Required Tests:** Script execution with documented metrics in ADR.
- **Documentation Updates:** ADR-0003, if the measured spike results warrant a queue decision.
- **Acceptance Criteria:** ADR-003 published detailing memory consumption, cancellation latency, failure modes, and final queue decision (Celery vs ARQ) for Phase 06.

### Task P01-T007: Architectural Spike S2 — Filtered HNSW Vector Recall

- **Task ID:** P01-T007
- **Task Name:** Execute Spike S2: pgvector Filtered HNSW Index Benchmark
- **Detailed Description:** Create `backend/tests/spikes/spike_s2_vector_filtering.py` with a documented, reproducible dataset and query set. Measure ANN recall and latency for queries filtered by both workspace and user using default HNSW and supported iterative scan modes, including `strict_order` and `relaxed_order`. Evaluate partial indexing only where the measured workload justifies it.
- **Expected Files:** `backend/tests/spikes/spike_s2_vector_filtering.py`; a vector-search benchmark report under `docs/architecture/`.
- **Dependencies:** P01-T003.
- **Assigned Agent Role:** AI Data Systems Engineer.
- **Required Tests:** Script execution with recall analysis.
- **Documentation Updates:** The data-model documentation, once created.
- **Acceptance Criteria:** Document measured recall and latency with workload details; make no pass/fail claim against an unapproved recall or latency target.

### Task P01-T008: Architectural Spike S3 — Gemini Live API Audio Bridge Constraints

- **Task ID:** P01-T008
- **Task Name:** Execute Spike S3: Gemini Live API Session Lifecycle and Latency
- **Current Implementation:** Isolated SDK adapters and a hidden-prompt free-tier launcher exist. User-run separate text sessions completed one audio turn each (484/453 ms first audio). Full session lifetime/resumption remain unverified; paid execution stays blocked.
- **Detailed Description:** Implement an opt-in live test client for the Gemini Live API. Measure first-audio latency and test session lifetime, resumption, `send_client_content`, and `send_realtime_input(text=...)` in separate sessions. A live run requires explicit approval because it uses an external service and may incur cost.
- **Expected Files:** `backend/tests/spikes/spike_s3_gemini_live.py`; a voice-subsystem evaluation under `docs/architecture/`.
- **Dependencies:** P01-T002.
- **Assigned Agent Role:** Senior AI Engineer (Voice/Realtime).
- **Required Tests:** Live API connection test with ground truth PCM fixtures.
- **Documentation Updates:** `docs/architecture/SYSTEM_OVERVIEW.md`.
- **Acceptance Criteria:** Record observed latency, session/resumption behavior, and results for each text-input method separately. Treat results as measured evidence, not an unapproved threshold.

### Task P01-T009: Architectural Spike S4 — Embedding Dimensionality Verification

- **Task ID:** P01-T009
- **Task Name:** Execute Spike S4: Verify `gemini-embedding-2` 768-Dimension Output
- **Current Implementation:** Offline/vector tests and the isolated SDK adapter exist; the approved user-run free-tier smoke returned three actual 768-dimensional vectors with documented norms/pair scores. This is bounded evidence, not production quality or billing verification; paid execution stays blocked.
- **Detailed Description:** Implement an opt-in live test of the embedding API. Verify the supported parameter for requesting 768 dimensions and the returned vector shape; record normalization and semantic-pair measurements without inventing score thresholds. A live run requires explicit approval because it uses an external service and may incur cost.
- **Expected Files:** `backend/tests/spikes/spike_s4_embeddings.py`; an embedding specification under `docs/architecture/`.
- **Dependencies:** P01-T002.
- **Assigned Agent Role:** AI Systems Engineer.
- **Required Tests:** Deterministic cases in `backend/tests/unit/test_spike_s4_embeddings.py`; opt-in live parameter/shape verification remains pending spending-control approval.
- **Documentation Updates:** The data-model documentation, once created.
- **Acceptance Criteria:** Record the verified request parameter and output shape plus reproducible measurements; do not assert unapproved similarity thresholds.

---

## E. File and Folder Changes

- **Implemented foundation and spike entry points:**
  - root-level `docker-compose.yml`
  - `docker/postgres/init.sql`
  - `backend/pyproject.toml`
  - `backend/app/main.py`
  - `backend/app/__main__.py`
  - `backend/app/core/config.py`
  - `backend/app/core/logging.py`
  - `backend/app/core/redis.py`
  - `backend/app/core/resources.py`
  - `backend/app/db/session.py`
  - `backend/app/api/v1/health.py`
  - `backend/tests/unit/test_config.py`
  - `backend/tests/integration/test_resources.py`
  - `backend/tests/integration/test_health.py`
  - `backend/tests/spikes/spike_s1_queue.py`
  - `backend/tests/spikes/spike_s2_vector_filtering.py`
  - `backend/tests/spikes/spike_s3_gemini_live.py`
  - `backend/tests/spikes/spike_s4_embeddings.py`
  - `apps/web/package.json`
  - `apps/web/tsconfig.json`
  - `apps/web/src/app/layout.tsx`
  - `apps/web/src/app/page.tsx`
  - proposed ADR-0003 and benchmark report

The health router is mounted by the application factory; no extra router wrapper
or unused frontend API client is needed. The SDK clients and live measurements
are tracked in the snapshot above; spike entry points existing does not prove
live acceptance.

- **Files to Modify:**
  - `README.md`
  - `.env.example`
  - `docs/guides/CONFIGURATION.md`
  - `docs/guides/ONBOARDING.md`
- **Files to Avoid Modifying:**
  - `MAYA_AI_FINAL_PLAN.md`
  - `AGENTS.md`
- **Relevant Directories:** `backend/app/`, `apps/web/`, `docker/`, `docs/adr/`.

---

## F. API and Data Contracts

### 1. Health Endpoints

- **`GET /api/v1/healthz`**
  - Response: `200 OK`
  - Body: `{"status": "alive"}` (implemented generic contract; no timestamp field)
- **`GET /api/v1/readyz`**
  - Response (Healthy): `200 OK`
  - Response (Degraded): `503 Service Unavailable`
  - Healthy body: `{"status": "ready"}`
  - Degraded body: `{"status": "unavailable"}`

---

## G. Security Requirements

- **Authentication:** Health check endpoints (`/healthz`, `/readyz`) are public for container probes and return only a generic status; they do not expose dependency names, internal errors, or sensitive configuration.
- **Data Isolation:** Docker container network isolation; PostgreSQL container does not expose credentials publicly.
- **Secret Handling:** Application database URLs come from process configuration or an explicitly selected environment file; no dotenv file loads implicitly. Secrets are never logged or serialized in error traces. Gemini runs remain blocked and do not load credentials.
- **Input Validation:** Configuration variables strictly validated with Pydantic; invalid port numbers, malformed URLs, or missing keys halt process startup immediately.

---

## H. Testing Strategy

- **Unit Tests:** Configuration validation tests, logging redaction tests (`backend/tests/unit/`).
- **Integration-labelled Tests:** Resource lifecycle and API probes with mocks (`backend/tests/integration/`); these are not live database isolation tests. Approved live service smoke evidence is recorded in the [test strategy](../../testing/FOUNDATION.md).
- **Linter & Type Checks:** From the repository root, use `uv run --directory backend --no-sync ruff check .`, `uv run --directory backend --no-sync ruff format --check .`, `uv run --directory backend --no-sync mypy .`, `npm --prefix apps\web run lint`, and `npm --prefix apps\web run typecheck`.
- **Spike Verifications:** Dedicated execution scripts producing structured benchmark reports in `docs/architecture/` and `docs/adr/`.

---

## I. Definition of Done

1. Compose configuration validates for PostgreSQL (pgvector, `pg_trgm`) and Redis AOF; starting containers is done only after separate approval.
2. With approved local services running, health probes report healthy status without exposing dependency internals.
3. Repository validation passes; backend and web stack checks run when their manifests are present.
4. Python and Next.js 15 scaffolds pass the repository-detected lint, type, test, and build checks.
5. S1 and S2 have reproducible reports; live S3 and S4 reports are produced only after explicit external-service approval. The queue choice is recorded in ADR-0003 before adoption.

---

## J. Phase Exit Gate

- **Demonstrated Proof:** Compose configuration validation, scaffold checks, health-probe tests, and reproducible S1/S2 reports; approved live S3/S4 reports when those external runs are authorized.
- **Pending proof:** Extended user-run lifetime observations and real resumption/context continuity. Short smoke dimensions/text-method audio are measured, not substitutes for these criteria.
- **Owner-approved scope revision:** Phase 01 is Free-tier-only/no linked billing. Provider-enforced INR 100 paid-cap verification remains mandatory before future paid use, not verified or waived by this phase. Paid mode is disabled.
- **Dependent Phase Unlocked:** Phases 02 and 03 may begin after this gate. Master-plan P1 remains incomplete until Phases 02–04 also pass their gates.
