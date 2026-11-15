# Memory and Knowledge System (`app.memory`)

The Memory and Knowledge module provides a production-grade multi-tier memory architecture for MAYA AI digital employees, adhering to Decision D7 and Defect #16 resolution in `MAYA_AI_FINAL_PLAN.md`.

## Core Responsibilities

1. **Working Memory (`working_memory.py`):**
   - Active dialogue turns and context cached in Redis under `memory:working:{workspace_id}:{session_id}`.
   - 24-hour time-to-live (`86400s`), auto-refreshed on every new turn.
2. **Profile & Long-Term Semantic Memory (`app.db.repositories.memory`):**
   - Stored in PostgreSQL `memory_items` table across four categories: `preference`, `project`, `episodic`, `knowledge`.
   - 768-dimensional vector embeddings with HNSW cosine distance indexing (`vector_cosine_ops`).
   - English full-text search index (`tsvector` with GIN indexing).
3. **Deterministic Hygiene Scrubber (`scrubber.py`):**
   - Neutralizes credit card numbers (Luhn algorithm verified), secret credentials, bearer tokens, JWTs, AWS keys, GitHub tokens, PEM private keys, and SSNs.
   - Pre-insertion scrubbing ensures zero sensitive data leaks into vector stores.
4. **Semantic Chunker & Ingestion (`ingestion.py`):**
   - Intelligent text splitting preserving paragraph boundaries (`\n\n`) and sentence boundaries (`. ! ?`).
   - Configurable chunk size (~512 tokens / ~2000 chars) with sliding overlap (~64 tokens / ~250 chars).
   - Generates 768-dimensional embeddings via `ModelGateway.embed_content(alias="embed")` using `gemini-embedding-2`.
5. **Hybrid RAG Retrieval Engine (`rag_engine.py`):**
   - Combines dense cosine similarity ($S_{sim} = 1.0 - \text{cosine\_distance}$) and sparse full-text scoring ($S_{text} = \text{ts\_rank\_cd}$).
   - **Defect #16 Threshold Gate:** Candidacy qualification gate ($S_{sim} \ge 0.78$) evaluated strictly before time decay.
   - **Time Decay Ranking:** $S_{eff} = (0.7 \cdot S_{sim} + 0.3 \cdot \min(1.0, S_{text})) \cdot e^{-0.005 \cdot \Delta\text{days}}$. Permanent items (`permanent=True`) bypass decay.
   - Automatic access count incrementation and `last_accessed_at` touch.
6. **Lifecycle & GDPR Article 17 Purge (`lifecycle.py`):**
   - Stale memory archival (`archive_stale_memories` sets `expires_at=now()` for non-permanent memories untouched for > 180 days).
   - Complete, atomic user memory deletion across PostgreSQL and Redis working memory.

## Architecture & Data Flow

```
User Input / Document
         │
         ▼
[ PiiScrubber ] ───► Redacts CC (Luhn), Tokens, Keys, SSN
         │
         ▼
[ SemanticChunker ] ───► Paragraph/sentence split with overlap
         │
         ▼
[ ModelGateway ] ───► 768-dim embeddings (gemini-embedding-2)
         │
         ▼
[ MemoryRepository ] ───► PostgreSQL 17 (pgvector + tsvector)
```
