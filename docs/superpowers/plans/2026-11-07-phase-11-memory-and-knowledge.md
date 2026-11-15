# Phase 11 — Memory and Knowledge System Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Implement, integrate, test, and certify Phase 11 (Memory and Knowledge System) to production-grade standards, including Redis working memory, PostgreSQL preferences, 768-dimensional pgvector semantic long-term memory across 4 categories (`preference`, `project`, `episodic`, `knowledge`), deterministic PII/secret scrubbing, hybrid dense/sparse retrieval with citations and Defect #16 threshold/decay ranking, and GDPR/CCPA purge endpoints.

**Architecture:** A multi-tier memory service composed of:
1. Fast working memory (Redis) for active conversation turns and tool results with 24-hour TTL;
2. Profile and structured preferences (PostgreSQL `memory_items` category `preference`);
3. Long-term semantic memory (PostgreSQL + pgvector) indexed with HNSW cosine distance and `tsvector` GIN full-text search;
4. Pre-write deterministic hygiene scrubber (`scrubber.py`) neutralizing credit cards (Luhn-verified), credentials/tokens, and SSNs;
5. Semantic chunker and embedding pipeline (`ingestion.py`) calling ModelGateway (`gemini-embedding-2`, 768-dim);
6. Hybrid RAG retrieval engine (`rag_engine.py`) enforcing tenant isolation (`workspace_id` + `user_id`), raw cosine similarity gate ($\ge 0.78$), and time decay ranking ($e^{-0.005 \cdot \Delta\text{days}}$, permanent items bypass);
7. Lifecycle and compliance manager (`lifecycle.py`) executing stale archiving and atomic GDPR Art. 17 erasures;
8. Memory REST API (`/api/v1/memory/*`).

**Tech Stack:** Python 3.12, FastAPI, SQLAlchemy 2.0 (asyncio), PostgreSQL 17 (pgvector, RLS), Redis 7.4, Google GenAI SDK (`ModelGateway`), Pytest.

**Spec:** `MAYA_AI_FINAL_PLAN.md` (§12, §14, §19, Decision D7, Defect #16), `docs/implementation/IMPLEMENTATION_PHASES/PHASE_11_MEMORY_KNOWLEDGE.md`.

## Global Constraints

- **Decision D7:** Three storage tiers (Redis working, PostgreSQL profile, pgvector semantic), four categories (`preference`, `project`, `episodic`, `knowledge`). Every query filtered by `workspace_id` AND `user_id`.
- **Defect #16 Resolution:** Raw cosine similarity threshold gate ($S_{sim} \ge 0.78$) evaluated *before* applying time-decay scoring. Effective ranking score: $S_{eff} = (0.7 \cdot S_{sim} + 0.3 \cdot \min(1.0, S_{text})) \cdot e^{-0.005 \cdot \Delta\text{days}}$. Permanent items (`permanent=True`) bypass time decay.
- **768-Dim Embeddings:** Vectors must strictly have 768 dimensions using `gemini-embedding-2` via `ModelGateway.embed_content(alias="embed")`.
- **Pre-Write Hygiene:** Credit cards (Luhn verified), bearer tokens, AWS/GitHub keys, private keys, and SSNs must be redacted before long-term insertion.
- **GDPR Article 17 Erasure:** `DELETE /api/v1/memory/purge` must completely erase user memory records atomically across PostgreSQL and Redis.
- **Strict RLS:** All database queries must run under `app.workspace_id` PostgreSQL Row-Level Security.
- **Zero Regressions:** All existing 1,795 backend tests, 868 frontend tests, and monorepo governance checks must pass.

## Review Focus

1. **Defect #16 Decay Decoupling:** Old items with high semantic similarity ($\ge 0.78$) must pass the candidate filter even after 180 days, while ranking behind recent identical items.
2. **Cross-Tenant Vector Isolation:** Semantic queries in Workspace A must return 0 rows for identical vector embeddings created in Workspace B.
3. **PII & Secret Scrubber Rigor:** Valid credit cards, secret keys, and SSNs must be redacted; false positives on ordinary numbers/dates must be avoided.
4. **Embedding Dimension Validation:** Any vector returned with length $\ne 768$ must raise `EmbeddingDimensionMismatchError` and fail closed.
5. **GDPR Atomic Purge:** Purging a user's memories must leave 0 rows in `memory_items` and 0 keys in Redis for that user without touching other users.

---

### Task 1: Update ORM Models and Database Repository

**Files:**
- Modify: `backend/app/db/models.py`
- Create: `backend/app/db/repositories/memory.py`
- Test: `backend/tests/postgres/test_memory_repository_postgres.py`

**Interfaces:**
- Produces: `MemoryItem(Base)` aligned with migration `0002_plan_tables.py`; `MemoryRepository` providing `create_item`, `get_item`, `list_items`, `search_hybrid`, `update_access`, `mark_expired`, `purge_user_items`.
- Consumes: `Base`, `Workspace`, `User` from `backend/app/db/models.py`.

- [ ] **Step 1: Write PostgreSQL repository tests verifying CRUD, tenant RLS isolation, and vector distance queries**
- [ ] **Step 2: Run test to confirm it fails before model/repository changes**
- [ ] **Step 3: Update `MemoryItem` in `backend/app/db/models.py` to match migration `0002` columns (`category`, `embedding`, `embedding_model`, `tsv`, `permanent`, `access_count`, `last_accessed_at`, `expires_at`)**
- [ ] **Step 4: Implement `MemoryRepository` in `backend/app/db/repositories/memory.py`**
- [ ] **Step 5: Run tests against PostgreSQL 17 to verify all repository operations pass**

---

### Task 2: Deterministic PII & Secret Scrubber Middleware

**Files:**
- Create: `backend/app/memory/scrubber.py`
- Test: `backend/tests/unit/test_pii_scrubber.py`

**Interfaces:**
- Produces: `PiiScrubber` class with `scrub(text: str) -> ScrubResult` returning `clean_text`, `redactions_count`, and `detected_types`; helper functions `verify_luhn(card_number: str) -> bool`.
- Redacts: Credit Cards (`[REDACTED_CREDIT_CARD]`), API keys/tokens/private keys (`[REDACTED_SECRET]`), SSNs (`[REDACTED_SSN]`).

- [ ] **Step 1: Write unit tests with Visa, MasterCard, Amex, dummy JWTs, private keys, AWS keys, and SSNs, plus clean conversational text**
- [ ] **Step 2: Run unit test to verify it fails**
- [ ] **Step 3: Implement `scrubber.py` with regex patterns, Luhn algorithm verification, and safe replacement**
- [ ] **Step 4: Run unit tests to verify 100% pass rate and zero regressions**

---

### Task 3: Semantic Chunker & Ingestion Pipeline

**Files:**
- Create: `backend/app/memory/ingestion.py`
- Test: `backend/tests/unit/test_memory_chunker.py`
- Test: `backend/tests/integration/test_memory_ingestion.py`

**Interfaces:**
- Produces: `SemanticChunker` with `chunk_text(text: str, max_tokens: int = 512, overlap: int = 64) -> list[str]`; `MemoryIngestionService` with `ingest_document(workspace_id, user_id, category, content, metadata) -> list[MemoryItem]`.
- Consumes: `PiiScrubber`, `ModelGateway.embed_content(alias="embed")`, `MemoryRepository`.

- [ ] **Step 1: Write unit tests for chunk boundary splitting (paragraphs, sentences, overlap) and integration tests for document ingestion**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement `SemanticChunker` in `backend/app/memory/ingestion.py`**
- [ ] **Step 4: Implement `MemoryIngestionService` coordinating scrubbing, chunking, 768-dim embedding generation, and repository persistence**
- [ ] **Step 5: Run unit and integration tests to verify they pass**

---

### Task 4: Hybrid RAG Engine with Defect #16 Threshold & Decay Ranking

**Files:**
- Create: `backend/app/memory/rag_engine.py`
- Test: `backend/tests/unit/test_rag_scoring.py`
- Test: `backend/tests/integration/test_hybrid_retrieval.py`

**Interfaces:**
- Produces: `HybridRagEngine` with `search(workspace_id, user_id, query_text, categories, limit=5, raw_threshold=0.78) -> list[MemorySearchResult]`.
- Enforces:
  - Dense cosine similarity $S_{sim} = 1 - (\text{embedding} \Leftrightarrow \text{query\_vector})$.
  - Raw threshold filter: $S_{sim} \ge 0.78$.
  - Sparse full-text score: $S_{text} = \text{ts\_rank\_cd}(tsv, \text{plainto\_tsquery}('english', query))$.
  - Effective ranking score: $S_{eff} = (0.7 \cdot S_{sim} + 0.3 \cdot \min(1.0, S_{text})) \cdot e^{-0.005 \cdot \Delta\text{days}}$ (permanent items skip decay).
  - Updates `access_count` and `last_accessed_at`.

- [ ] **Step 1: Write unit tests verifying math formulas for hybrid weights, decay factor, permanent bypass, and 0.78 raw gate**
- [ ] **Step 2: Write integration tests verifying real database vector and tsvector hybrid queries**
- [ ] **Step 3: Implement `HybridRagEngine` in `backend/app/memory/rag_engine.py`**
- [ ] **Step 4: Run unit and integration tests to verify they pass**

---

### Task 5: Memory Lifecycle Manager & Working Memory (Redis)

**Files:**
- Create: `backend/app/memory/lifecycle.py`
- Create: `backend/app/memory/working_memory.py`
- Test: `backend/tests/unit/test_working_memory.py`
- Test: `backend/tests/integration/test_memory_lifecycle.py`

**Interfaces:**
- Produces:
  - `WorkingMemoryManager`: Redis session state (`memory:working:{workspace_id}:{session_id}`) with 24-hour TTL, turn append, and retrieval.
  - `MemoryLifecycleManager`: `archive_stale_memories(threshold_days=180)`, `purge_user_memories(workspace_id, user_id, scope)`.
- Consumes: Redis client, `MemoryRepository`.

- [ ] **Step 1: Write tests for Redis working memory turn storage, TTL expiration, stale memory archival, and GDPR purge**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement `WorkingMemoryManager` in `backend/app/memory/working_memory.py`**
- [ ] **Step 4: Implement `MemoryLifecycleManager` in `backend/app/memory/lifecycle.py`**
- [ ] **Step 5: Run tests to verify they pass**

---

### Task 6: Memory REST API & Schemas

**Files:**
- Create: `backend/app/schemas/memory.py`
- Create: `backend/app/api/v1/memory.py`
- Modify: `backend/app/api/v1/router.py`
- Test: `backend/tests/integration/test_memory_api.py`

**Interfaces:**
- Produces:
  - `POST /api/v1/memory/search` -> hybrid search with citations.
  - `GET /api/v1/memory/items` -> paginated list of memory items with category filter.
  - `POST /api/v1/memory/items` -> ingest new memory or knowledge snippet.
  - `DELETE /api/v1/memory/items/{id}` -> delete single memory item.
  - `DELETE /api/v1/memory/purge` -> GDPR/CCPA purge with scope.

- [ ] **Step 1: Write API integration tests testing authentication, authorization, search, pagination, ingestion, and purge**
- [ ] **Step 2: Run tests to verify they fail**
- [ ] **Step 3: Implement Pydantic schemas in `backend/app/schemas/memory.py`**
- [ ] **Step 4: Implement FastAPI router in `backend/app/api/v1/memory.py` and register in `router.py`**
- [ ] **Step 5: Run API integration tests to verify they pass**

---

### Task 7: Architecture Documentation & Specifications

**Files:**
- Create: `docs/architecture/MEMORY_SYSTEM.md`
- Create: `docs/api/MEMORY_SPECIFICATION.md`
- Create: `docs/security/MEMORY_HYGIENE.md`
- Modify: `docs/implementation/IMPLEMENTATION_STATUS.md`
- Modify: `CHANGELOG.md`

- [ ] **Step 1: Author `docs/architecture/MEMORY_SYSTEM.md` detailing three tiers, Defect #16 math, and vector indexing**
- [ ] **Step 2: Author `docs/api/MEMORY_SPECIFICATION.md` documenting all endpoints, schemas, and error responses**
- [ ] **Step 3: Author `docs/security/MEMORY_HYGIENE.md` documenting PII scrubbing rules, Luhn check, and GDPR purge**
- [ ] **Step 4: Update `docs/implementation/IMPLEMENTATION_STATUS.md` and `CHANGELOG.md`**
- [ ] **Step 5: Validate documentation with `npm run docs:check`**

---

### Task 8: Full Regression & Multi-Phase Integration Certification

**Scope:**
- Backend pytest suite (Phases 01 through 11).
- Frontend Vitest suite.
- Monorepo governance checks (`npm run validate:fast`).
- Ruff linter and formatter, mypy typechecker, web typechecker.

- [ ] **Step 1: Run `ruff check backend` and `ruff format --check backend`**
- [ ] **Step 2: Run `mypy .`**
- [ ] **Step 3: Run backend pytest suite with PostgreSQL and Redis**
- [ ] **Step 4: Run frontend Vitest suite**
- [ ] **Step 5: Run monorepo governance checks (`npm run docs:check`, `check-secrets.mjs`, `check-sensitive.mjs`)**
- [ ] **Step 6: Generate Production Readiness and Certification Report for Phase 11**
