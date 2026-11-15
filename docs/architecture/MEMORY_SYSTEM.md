# Memory and Knowledge System Architecture

MAYA AI implements a production-grade multi-tier memory and knowledge subsystem as defined in `MAYA_AI_FINAL_PLAN.md` (§12, §14, §19, Decision D7, Defect #16).

---

## 1. Storage Tiers & Taxonomy (Decision D7)

The memory system partitions persistence across three physical and logical tiers:

| Tier | Storage Engine | Scope & Retention | Primary Use Cases |
| :--- | :--- | :--- | :--- |
| **Fast Working Memory** | Redis 7.4 | Session-scoped, 24-hour TTL (`86400s`) | Active conversation turns, recent tool results, and execution state |
| **Profile & Preferences** | PostgreSQL 17 (`category='preference'`) | User-scoped, persistent (`permanent=True`) | Explicit user preferences (e.g. dark mode, concise style, tone, timezone) |
| **Semantic Long-Term Memory** | PostgreSQL 17 + pgvector | Workspace + User-scoped, HNSW indexed | Project documents, meeting transcripts, episodic interactions, knowledge snippets |

### Memory Categories

Memory items belong to one of four validated categories:
- `preference`: Long-lived user instructions, stylistic directives, and tool defaults (`permanent=True`).
- `project`: Contextual project goals, tech stack decisions, repository structures, and architecture constraints.
- `episodic`: Historical summaries of previous interaction sessions, decisions reached, and milestones.
- `knowledge`: Ingested technical guides, API documentation, company policies, and operational runbooks.

---

## 2. Mathematical Formulation & Defect #16 Resolution

### Defect #16 Analysis
In earlier iterations, time-decay scoring was applied directly to dense similarity before ranking filters were executed ($S_{sim} \cdot e^{-\lambda \cdot t} \ge \text{threshold}$). As a result, critical historical knowledge items older than 90–180 days were prematurely discarded from retrieval consideration, even when their semantic relevance to the query was exceptionally high ($S_{sim} \approx 0.95$).

### The Decoupled Ranking Pipeline
Under Phase 11, candidate qualification is strictly decoupled from time-decay ranking:

1. **Dense Cosine Similarity:**
   $$S_{sim} = 1.0 - (\vec{v}_{doc} \Leftrightarrow \vec{v}_{query})$$

2. **Candidate Qualification Gate (Defect #16):**
   A document qualifies for consideration if and only if its raw semantic similarity meets the threshold:
   $$\text{Qualifies} \iff S_{sim} \ge 0.78$$
   Documents with $S_{sim} < 0.78$ are immediately dropped from the candidate pool.

3. **Sparse Full-Text Ranking:**
   $$S_{text} = \text{ts\_rank\_cd}(tsv, \text{plainto\_tsquery}('english', query))$$

4. **Hybrid Combined Score:**
   $$S_{hybrid} = 0.7 \cdot S_{sim} + 0.3 \cdot \min(1.0, \max(0.0, S_{text}))$$

5. **Decay-Adjusted Effective Ranking:**
   For non-permanent items, exponential time decay prioritizes recent experiences while preserving the candidacy of relevant older documents:
   $$S_{eff} = \begin{cases} S_{hybrid} & \text{if } permanent = \text{True} \\ S_{hybrid} \cdot \exp(-0.005 \cdot \Delta\text{days}) & \text{if } permanent = \text{False} \end{cases}$$
   where:
   $$\Delta\text{days} = \max\left(0, \frac{\text{now}() - \text{last\_accessed\_at}}{86400}\right)$$

---

## 3. Database Schema & Vector Indexing

The `memory_items` table in PostgreSQL 17 is defined as follows:

```sql
CREATE TABLE memory_items (
  id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
  workspace_id UUID NOT NULL REFERENCES workspaces(id) ON DELETE CASCADE,
  user_id UUID NOT NULL,
  category VARCHAR(20) NOT NULL
    CHECK (category IN ('preference','project','episodic','knowledge')),
  content TEXT NOT NULL,
  metadata JSONB NOT NULL DEFAULT '{}',
  embedding vector(768),
  embedding_model VARCHAR(100) NOT NULL,
  tsv tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED,
  permanent BOOLEAN NOT NULL DEFAULT FALSE,
  access_count INTEGER NOT NULL DEFAULT 1,
  last_accessed_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  expires_at TIMESTAMPTZ,
  created_at TIMESTAMPTZ NOT NULL DEFAULT now(),
  CONSTRAINT memory_items_user_fk FOREIGN KEY (workspace_id, user_id)
    REFERENCES users (workspace_id, id) ON DELETE CASCADE
);

CREATE INDEX memory_embedding_hnsw ON memory_items
  USING hnsw (embedding vector_cosine_ops)
  WITH (m = 16, ef_construction = 64);

CREATE INDEX memory_tsv_gin ON memory_items USING gin (tsv);
CREATE INDEX memory_content_trgm ON memory_items USING gin (content gin_trgm_ops);
CREATE INDEX memory_scope_idx ON memory_items (workspace_id, user_id, category);
```

### Multi-Tenant Isolation
PostgreSQL Row-Level Security (RLS) is applied to `memory_items` restricting queries to `app.workspace_id`. In addition, every SQL query explicitly verifies both `workspace_id` and `user_id`, enforcing strict tenant and user boundary isolation.
