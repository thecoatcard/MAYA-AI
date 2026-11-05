# Phase 11 — Memory and Knowledge System

## A. Phase Overview

- **Phase Number:** Phase 11
- **Phase Name:** Memory and Knowledge System
- **Objective:** Build the multi-tiered memory and retrieval-augmented generation (RAG) system, including Redis working memory, PostgreSQL profile preferences, pgvector 768-dimensional semantic memory, hybrid dense/sparse retrieval with citations, deterministic PII scrubbing, time-decay scoring, and GDPR/CCPA purge functionality.
- **Why This Phase is Necessary:** As established in [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §12 and Decision D7, digital employees require contextual continuity across sessions without leaking cross-tenant data or suffering from retrieval degradation. Furthermore, resolving Defect #16 requires decoupling the 0.78 similarity threshold from the time-decay factor so that older, highly relevant knowledge is not prematurely dropped from search results.
- **Expected Outcome:** A unified memory service managing working, profile, and semantic tiers; 768-dim vector embeddings via `gemini-embedding-2`; hybrid ranking combining vector cosine similarity (0.7) with `tsvector` full-text search (0.3); pre-write PII and secret redaction; exponential decay ranking (`lambda = 0.005/day`); and `POST /api/v1/memory/search` and `DELETE /api/v1/memory/purge` endpoints.
- **Priority:** High
- **Estimated Complexity:** High
- **Estimated Duration:** 10 business days (Weeks 18–19 of master schedule).

---

## B. Prerequisites

- **Required Completed Phases:** Phase 02 (Authentication, Users, Workspaces, and Database Security), Phase 03 (Gemini AI Integration and Model Gateway), and Phase 10 (File Processing and Document Generation).
- **Required Infrastructure:** PostgreSQL 17 with `vector` and `pg_trgm` extensions; Redis 7.4.
- **Required Credentials or External Services:** Active `GEMINI_API_KEY`.
- **Required Architectural Decisions:** [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) Decision D7 (Three storage tiers, four categories), Defect #16 (Relevance gate applied to raw similarity, decay applied to ranking).
- **Required Documentation:** `docs/architecture/DATA_MODEL.md`, docs/security/DATABASE_ISOLATION.md (planned artifact).

---

## C. Scope

### Included Features

- Three storage tiers:
  - Working Memory (Redis): Active turns, recent tool outputs, 24-hour TTL on inactivity.
  - Profile & Structured Memory (PostgreSQL): User preferences (tone, default repo, contact aliases), updated only on explicit user confirmation.
  - Semantic Memory (PostgreSQL + pgvector): Four categories: `preference`, `project`, `episodic` (past tasks, post-mortems), `knowledge` (ingested docs, PDFs).
- Ingestion Pipeline:
  - Semantic chunker: 512 tokens with 64-token overlap.
  - Embeddings generation using `embed` alias (`gemini-embedding-2`, 768 dimensions).
  - Record `embedding_model` with every row to facilitate re-embedding on model migration.
- Pre-write hygiene:
  - Deterministic PII and secret scrubbing (credit cards, passwords, bearer tokens, SSNs) prior to long-term memory insertion.
- Hybrid Search Engine:
  - Dense vector similarity (HNSW cosine metric, weight 0.7).
  - Sparse full-text keyword search (`tsvector` and English dictionary GIN index, weight 0.3).
  - Mandatory tenant filtering: `WHERE workspace_id = $1 AND user_id = $2`.
- Scoring and Decay (Defect #16):
  - Raw cosine similarity threshold gate: `S_sim >= 0.78`.
  - Effective ranking score: `S_eff = S_sim * exp(-0.005 * delta_days)`.
  - Permanent items (`permanent=True`) bypass decay.
- Memory Governance & Privacy:
  - Memory browser API and citation metadata tracking.
  - GDPR/CCPA purge endpoint: `DELETE /api/v1/memory/purge` supporting granular or total erasure.

### Excluded Features

- S3 cold-archive tier with Parquet data lake (deferred to post-MVP per Decision D7).
- Unsupervised autonomous knowledge extraction from arbitrary background web crawls.

### Deferred Features

- Cross-workspace shared organizational knowledge bases (enterprise post-MVP).

### Dependencies on Future Phases

- Phase 12 (Voice System) and Phase 14 (Frontend) query memory to provide personalized contextual responses.

---

## D. Implementation Breakdown

### Task P11-T001: PII and Secret Scrubber Middleware

- **Task ID:** P11-T001
- **Task Name:** Build Deterministic Hygiene and Redaction Service
- **Detailed Description:** Implement backend/app/memory/scrubber.py (planned artifact). Ingest text chunks before database persistence; execute regular expression and checksum passes for:
  - Credit card numbers (Luhn algorithm verification).
  - API keys, JWTs, and private key headers.
  - Social Security and National Identity numbers.
  - Replace detected sensitive data with sanitized tokens (e.g. `[REDACTED_CREDIT_CARD]`).
- **Expected Files:** backend/app/memory/scrubber.py (planned artifact), backend/tests/unit/test_pii_scrubber.py (planned artifact).
- **Dependencies:** Phase 02.
- **Assigned Agent Role:** Security & Data Privacy Engineer.
- **Required Tests:** `pytest backend/tests/unit/test_pii_scrubber.py` (testing diverse credential formats, edge-case card numbers).
- **Documentation Updates:** docs/security/MEMORY_HYGIENE.md (planned artifact).
- **Acceptance Criteria:** Sensitive credentials and PII are redacted 100% of the time; clean text passes through uncorrupted.

### Task P11-T002: Ingestion Pipeline and Vector Embedding

- **Task ID:** P11-T002
- **Task Name:** Build Document Chunking and Embedding Generator
- **Detailed Description:** Implement backend/app/memory/ingestion.py (planned artifact). Split documents, markdown, and conversation logs into 512-token chunks with 64-token overlap. Cleanse text via `scrubber.py`. Call `ModelGateway.get_embeddings(alias="embed")` to generate 768-dimensional vectors. Insert records into `memory_items` table with metadata and `embedding_model='gemini-embedding-2'`.
- **Expected Files:** backend/app/memory/ingestion.py (planned artifact), backend/tests/integration/test_memory_ingestion.py (planned artifact).
- **Dependencies:** P11-T001, Phase 03.
- **Assigned Agent Role:** AI Data Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_memory_ingestion.py`.
- **Documentation Updates:** docs/architecture/MEMORY_SYSTEM.md (planned artifact).
- **Acceptance Criteria:** Chunks do not exceed token boundaries; output vectors contain exactly 768 dimensions; database rows populate correctly under tenant RLS.

### Task P11-T003: Hybrid Retrieval and Ranking Engine (Defect #16)

- **Task ID:** P11-T003
- **Task Name:** Implement Dense/Sparse Search with Raw Threshold Gate and Decay
- **Detailed Description:** Implement backend/app/memory/rag_engine.py (planned artifact):
  - Dense query: `1 - (embedding <=> query_vector)` using HNSW index.
  - Sparse query: `ts_rank_cd(tsv, plainto_tsquery('english', query_text))` using GIN index.
  - Mandatory tenant filter: `workspace_id = :ws_id AND user_id = :user_id`.
  - Filter gate: candidate must satisfy `raw_cosine_similarity >= 0.78`.
  - Apply time decay to ranking: `score = (0.7 * cosine + 0.3 * text_rank) * exp(-0.005 * days_since_last_accessed)`.
  - Return top K results with source citations.
- **Expected Files:** backend/app/memory/rag_engine.py (planned artifact), backend/tests/integration/test_hybrid_retrieval.py (planned artifact).
- **Dependencies:** P11-T002.
- **Assigned Agent Role:** AI Search Specialist.
- **Required Tests:** `pytest backend/tests/integration/test_hybrid_retrieval.py` (assert raw threshold gate behavior, test decay ordering).
- **Documentation Updates:** docs/architecture/MEMORY_SYSTEM.md (planned artifact).
- **Acceptance Criteria:** Results below 0.78 raw similarity are discarded; old high-similarity items rank appropriately; citations link back to source IDs.

### Task P11-T004: Memory Lifecycle, Archival, and GDPR Purge API

- **Task ID:** P11-T004
- **Task Name:** Implement Memory Lifecycle Maintenance and Purge Endpoint
- **Detailed Description:** Implement backend/app/api/v1/memory.py (planned artifact):
  - `POST /api/v1/memory/search`: Execute hybrid search query.
  - `GET /api/v1/memory/items`: List user's memory items with pagination and category filters.
  - `DELETE /api/v1/memory/purge`: Execute GDPR/CCPA erasure, deleting all memory items for the user or workspace.
  - Implement maintenance job `archive_stale_memories()`: mark items unused for > 180 days as expired unless `permanent=True`.
- **Expected Files:** backend/app/api/v1/memory.py (planned artifact), backend/app/schemas/memory.py (planned artifact), backend/app/memory/lifecycle.py (planned artifact), backend/tests/integration/test_memory_api.py (planned artifact).
- **Dependencies:** P11-T003.
- **Assigned Agent Role:** Compliance & Backend Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_memory_api.py`.
- **Documentation Updates:** docs/api/MEMORY_SPECIFICATION.md (planned artifact).
- **Acceptance Criteria:** Hybrid search returns accurate citations; purge endpoint deletes 100% of target user memory rows without affecting other tenants; stale items archive cleanly.

---

## E. File and Folder Changes

- **Files to Create:**
  - backend/app/memory/__init__.py (planned artifact)
  - backend/app/memory/scrubber.py (planned artifact)
  - backend/app/memory/ingestion.py (planned artifact)
  - backend/app/memory/rag_engine.py (planned artifact)
  - backend/app/memory/lifecycle.py (planned artifact)
  - backend/app/api/v1/memory.py (planned artifact)
  - backend/app/schemas/memory.py (planned artifact)
  - backend/tests/unit/test_pii_scrubber.py (planned artifact)
  - backend/tests/integration/test_memory_ingestion.py (planned artifact)
  - backend/tests/integration/test_hybrid_retrieval.py (planned artifact)
  - backend/tests/integration/test_memory_api.py (planned artifact)
  - docs/architecture/MEMORY_SYSTEM.md (planned artifact)
  - docs/security/MEMORY_HYGIENE.md (planned artifact)
  - docs/api/MEMORY_SPECIFICATION.md (planned artifact)
- **Files to Modify:**
  - backend/app/api/v1/router.py (planned artifact)
  - `backend/app/main.py`
- **Files to Avoid Modifying:**
  - `MAYA_AI_FINAL_PLAN.md`
- **Relevant Directories:** `backend/app/memory/`, `backend/app/api/v1/`, `docs/architecture/`.

---

## F. API and Data Contracts

### 1. Memory Search Request and Response Contract

- **`POST /api/v1/memory/search`**
  - Headers: `Authorization: Bearer <jwt>`
  - Request:
    ```json
    {
      "query": "What were the Q3 deliverables for the mobile app?",
      "categories": ["project", "knowledge"],
      "limit": 5
    }
    ```
  - Response (`200 OK`):
    ```json
    {
      "results": [
        {
          "id": "e6a2b724-4f01-49b8-a612-42111d4e022f",
          "category": "project",
          "content": "Q3 mobile app release shipped biometric login and offline sync.",
          "raw_similarity": 0.842,
          "effective_score": 0.815,
          "source_id": "doc-q3-summary-pdf",
          "created_at": "2026-09-15T10:00:00Z"
        }
      ]
    }
    ```

### 2. GDPR Memory Purge Contract

- **`DELETE /api/v1/memory/purge`**
  - Headers: `Authorization: Bearer <jwt>`
  - Request:
    ```json
    {
      "scope": "all"
    }
    ```
  - Response (`200 OK`):
    ```json
    {
      "status": "purged",
      "deleted_records": 142,
      "purged_at": "2026-11-05T15:00:00Z"
    }
    ```

---

## G. Security Requirements

- **Tenant Isolation:** Every memory query and ingestion pipeline step explicitly enforces `workspace_id` and `user_id` parameters under RLS.
- **PII Scrubbing:** Plaintext secrets, credit cards, and sensitive identifiers must never be inserted into long-term embeddings or vector columns.
- **Data Subject Rights:** Full compliance with GDPR Article 17 (Right to Erasure) via atomic deletion in `DELETE /api/v1/memory/purge`.

---

## H. Testing Strategy

- **Relevance Benchmark Tests:** Evaluate retrieval against a golden set of 50 query-document pairs, asserting top-3 recall >= 90%.
- **Cross-Tenant Vector Isolation:** Seed vector items in Workspace A and Workspace B; query with Workspace A's token; assert zero Workspace B items are returned even when query matches semantic content identically.
- **Decay Verification Tests:** Insert identical documents with timestamps separated by 180 days; verify that recent item ranks higher while both pass the raw 0.78 similarity threshold.

---

## I. Definition of Done

1. Semantic text chunker and PII scrubber cleanse text before embedding.
2. 768-dimensional embeddings generate via `gemini-embedding-2`.
3. Hybrid search combines vector cosine similarity and full-text search with citations.
4. Raw similarity gate (0.78) and time-decay ranking function correctly.
5. GDPR purge endpoint removes all records for a user with zero remnants.
6. Documentation in docs/architecture/MEMORY_SYSTEM.md (planned artifact) is complete.

---

## J. Phase Exit Gate

- **Demonstrated Proof:** `pytest backend/tests/integration/test_hybrid_retrieval.py` passes; cross-tenant memory isolation suite verifies zero leakage; purge endpoint test verifies complete row deletion.
- **Dependent Phase Unlocked:** Phase 12 (Real-Time Voice System) is cleared to begin.
