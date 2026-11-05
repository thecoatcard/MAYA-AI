# Data-store foundation

## Implemented

- Local PostgreSQL 17, with `vector` and `pg_trgm` extensions initialized.
- A bootstrap administrator and a separate `maya_app` runtime login.
- The runtime login is not a superuser, database/role creator, replication role
  or RLS-bypass role. It owns no application objects.
- Redis 7.4 runs with append-only persistence in a named volume.
- SQLAlchemy/asyncpg and Redis async clients have explicit startup, probe and cleanup paths.

There are no application tables, ORM models, Alembic migrations, authentication
records, tenant policies or vector indexes yet. Connection-role separation is not
a claim that tenant isolation has been implemented.

## Synthetic filtered-vector spike

[Spike S2](SPIKE_S2_VECTOR.md) now measures a global HNSW index on disposable
synthetic 768-dimensional vectors with both workspace and user post-index filters.
Its exact baseline and ANN executions have distinct SQL and disabled statement
caching to avoid reusing a sequential-scan plan as an ANN measurement.
An initial perfect-recall run was invalidated by that plan-cache issue and superseded.

In the corrected 30,000-row run, mean recall@10 was 0.056 with iterative scans off,
0.200 with strict ordering and 0.544 with relaxed ordering. Off mode underfilled
all 80 measured queries; both iterative modes returned 10 rows but not necessarily
the exact nearest 10. These are synthetic observations, not acceptance thresholds.
No application vector index, RLS policy, partial index or production tuning decision
was introduced; the disposable database and its volume were removed.

## Offline embedding analysis

[S4](SPIKE_S4_EMBEDDINGS.md) now has a documented 768-dimension SDK request plan
and offline response/vector validation. Hand-constructed fixtures exercise shape,
norm, cosine and local float32-conversion metrics. Those fixture values are not
provider results. The subsequent [user-run S4 smoke](SPIKE_S4_EMBEDDINGS.md#user-run-live-measurements-2026-11-05)
returned three actual 768-dimensional vectors with raw norms near 1.0.
No schema, embedding storage or application model integration changed.

The separately locked actual SDK adapter now sends three independent async
requests, one per synthetic text, and applies that analyzer to the collected
vectors. Gemini Embedding 2 aggregates list inputs; the offline bundle must not
be treated as one request returning three independent embeddings.
This behavior has SDK-model/fake-client coverage and the subsequent bounded
user-run smoke evidence; it does not establish production retrieval quality.

## Planned

The schema and RLS in [master plan section 14](../../MAYA_AI_FINAL_PLAN.md) belong to
[Phase 02](../implementation/IMPLEMENTATION_PHASES/PHASE_02_AUTH_DATABASE_SECURITY.md).
Workspace visibility, OTP scope and workspace-less audit events still need decisions.
The later LangGraph checkpointer requires psycopg 3 independently of the current
SQLAlchemy/asyncpg connection path; it is not installed by this scaffold.

See the [local runbook](../operations/LOCAL_DEVELOPMENT.md) for initialization,
persistence and non-destructive shutdown behavior.
