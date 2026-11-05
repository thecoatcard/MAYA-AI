# S2 — Filtered pgvector HNSW spike

## Purpose and status

This is a reproducible **synthetic benchmark**, not an application schema, migration,
RLS test, production-performance claim, or recall/latency acceptance gate. The raw
measurements, including per-query latency samples and `EXPLAIN (ANALYZE, BUFFERS)`
JSON plans, are in [`SPIKE_S2_VECTOR.measurements.json`](SPIKE_S2_VECTOR.measurements.json).
Treat the recorded measurements as observations of this exact workload and environment.

## Run

The harness is opt-in. It requires the already available pinned image from the local
foundation Compose configuration and the backend's installed `asyncpg` dependency:

```powershell
uv run --directory backend --no-sync python tests/spikes/spike_s2_vector_filtering.py --run
```

Without `--run` it exits before contacting Docker. Each run creates a UUID-named
container and volume, publishes PostgreSQL on a random loopback-only port, and limits
the container to 3 GiB memory and 2 CPUs. It uses a generated, local-only synthetic
database password; it does not read `.env` files, use credentials from the repository,
connect to the foundation database, or call an external service. The harness has
bounded Docker/database operations and removes only its own container and volume in a
`finally` path. It atomically replaces the report at its fixed S2-owned path.

## Dataset and method

- Fixed dataset seed `20261105` and query seed `20280038`; 768-dimensional normalized
  Gaussian vectors; 30,000 rows. Each row vector and query vector is independently
  sampled from a seeded Gaussian and normalized; tenant labels are assigned by the
  workspace/user counts, not correlated with embedding values.
- Four workspaces have 3,000, 6,000, 9,000, and 12,000 rows. Eight synthetic users
  per workspace follow weights `1,1,2,2,3,3,4,8`.
- Two users per workspace are queried: one with the smallest and one with the largest
  share. Each workspace/user case has two fixed-seed query vectors and `k=10`.
- The queried small/large users contain 125/1,000; 250/2,000; 375/3,000; and
  500/4,000 rows respectively. Workspace selectivity against all 30,000 rows is
  10%, 20%, 30%, and 40%; user selectivity within a workspace is 4.17% or 33.33%;
  combined workspace-and-user selectivity ranges from 0.42% to 13.33%.

| Workspace rows | Workspace selectivity | Queried user rows (small / large) | Combined selectivity (small / large) |
| ---: | ---: | ---: | ---: |
| 3,000 | 10% | 125 / 1,000 | 0.417% / 3.333% |
| 6,000 | 20% | 250 / 2,000 | 0.833% / 6.667% |
| 9,000 | 30% | 375 / 3,000 | 1.250% / 10.000% |
| 12,000 | 40% | 500 / 4,000 | 1.667% / 13.333% |
- Every query filters on **both** `workspace_id` and `user_id`. Returned rows are
  checked against both values. This verifies query-predicate confinement only; the
  temporary database has no RLS policy, and this result is not evidence of RLS.
- The exact ground truth uses the same cosine-distance filter/order query with index
  and bitmap scans disabled. Its `EXPLAIN` plan is checked to ensure it is a sequential
  scan and does not use the HNSW index.
- ANN runs use the cosine HNSW index (`m=16`, `ef_construction=64`), `ef_search=40`,
  and supported pgvector iterative-scan modes `off` (default behavior),
  `strict_order`, and `relaxed_order`. Sequential scans are disabled for the ANN
  comparison so each mode is evaluated through HNSW, not the planner's path choice.
  The `EXPLAIN` plans must show the HNSW index with both workspace and user in the
  post-index `Filter`; otherwise the benchmark fails. It compares each plan's actual
  output rows with a separate direct fetch using the same mode before accepting results.
- Tenant columns are **not** HNSW index keys. The HNSW index is global and orders by
  cosine distance; PostgreSQL applies both tenant conditions as a post-index filter.
  The raw plans record `Rows Removed by Filter` and the filter expression. There is no
  bitmap/intersection prefilter, partitioning, partial index, or tenant-aware index.
- Asyncpg statement caching is disabled and the ANN SQL has distinct statement text
  from the exact query. The original identical SQL strings caused asyncpg to reuse the
  exact baseline's sequential-scan plan after planner settings changed. Thus the
  original run's apparent ANN recall of 1.0 was invalid; its separate `EXPLAIN ANALYZE`
  showed HNSW returning 0 rows in the most selective off-mode case while direct fetches
  reported 10. The corrected run below replaces those measurements.
- Client round-trip latency samples, recall@10 against the exact IDs, returned row
  counts, exact-baseline latency, server execution/buffer plans, database/index size,
  Docker resource limits, and a container resource snapshot are retained in the JSON.
  The raw report's main fields are `configuration`, `environment`,
  `exact_baseline_measurements` (16 queries, including exact top-10 IDs),
  `ann_measurements` (24 case/strategy aggregates, 10 samples each and 80 per strategy),
  `plan_probe_measurements` (48 explain-versus-fetch row-count
  checks), `explain_plans` (8 representative exact plans and 48 per-query ANN plans),
  `cleanup`, and `limitations`.

## Corrected recorded run (2026-11-05)

Command:

```powershell
uv run --directory backend --no-sync python tests/spikes/spike_s2_vector_filtering.py --run
```

The corrected run took about 3 minutes 5 seconds. Environment: Python 3.12.10,
asyncpg 0.32.0 with statement cache disabled, Docker Server 29.8.2, PostgreSQL 17.11,
pgvector 0.8.7, and
`pgvector/pgvector:pg17@sha256:7d0a41464797aeb9862dfdc0bbf1526b82cfd324cdd8036df19e3545f4a9ccb8`.
The data seed is `20261105`; query seed is `20280038`. There are 8 workspace/user
cases, 2 query vectors each, 5 timed repetitions per query and strategy, and 1 ANN
warmup per query. Each strategy has 80 timed fetches.

The deterministic and backend test commands were:

```powershell
uv run --directory backend --no-sync pytest -q tests/unit/test_spike_s2_vector_filtering.py
uv run --directory backend --no-sync pytest -q
uv run --directory backend --no-sync ruff check tests/spikes/spike_s2_vector_filtering.py tests/unit/test_spike_s2_vector_filtering.py
uv run --directory backend --no-sync ruff format --check tests/spikes/spike_s2_vector_filtering.py tests/unit/test_spike_s2_vector_filtering.py
uv run --directory backend --no-sync mypy --explicit-package-bases tests/spikes/spike_s2_vector_filtering.py tests/unit/test_spike_s2_vector_filtering.py
```

Results: 25 focused tests and 93 backend tests passed; Ruff, formatting, and focused
mypy passed.

| Strategy | Mean recall@10 | Underfilled timed results | Client p50 / p95 (ms) |
| :--- | ---: | ---: | ---: |
| HNSW, iterative scan off | 0.056 | 80 / 80 | 6.53 / 8.00 |
| HNSW, strict order | 0.200 | 0 / 80 | 37.71 / 53.81 |
| HNSW, relaxed order | 0.544 | 0 / 80 | 21.67 / 49.44 |
| Exact filtered sequential scan | — | 0 / 80 | 9.20 / 24.48 |

These are descriptive observations, not target results. In off mode, the HNSW scan
stops at its initial `ef_search=40` candidates, then both predicates reject the
candidates; 80 timed results ranged from 0 to 5 rows, and all underfilled `k=10`.
The iterative modes continue scanning until 10 matching rows are produced in this
dataset, but returning 10 rows does **not** imply high recall: strict order averaged
0.200 and relaxed order 0.544. The filters are post-index, and tenant labels are
independent of the random embedding geometry. This is a workload where a global HNSW
traversal can spend substantial effort on rows outside the requested workspace/user.

There are 56 retained plans (8 representative exact plans plus all 48 ANN query
plans) and 48 plan/direct probes. All 48 probe row counts matched their corresponding
`EXPLAIN ANALYZE` HNSW output counts, and each HNSW plan filter named both tenant
columns. Exact scans were checked for each of the 16 exact query vectors and used
sequential scans; no exact plan used HNSW. The temporary container was limited to
3 GiB and 2 CPUs. Its sampled post-query use was 285.9 MiB and 1.25% CPU; the
database measured 257,873,587 bytes and the HNSW index 122,888,192 bytes. The table
measurement is `pg_relation_size` (main table fork, excluding TOAST and indexes).
The container and volume were removed; no `maya-s2` resource remained.

## Interpretation and limitations

There is no approved recall or latency threshold, so this report makes no pass/fail
claim or deployment recommendation. Synthetic isotropic vectors and the selected
tenant distribution may not represent a production retrieval workload. Client
latencies include local loopback and asyncpg overhead; the retained PostgreSQL plans
provide separate server-side execution evidence. The container resource snapshot is
not a peak-resource profile. No partial index is introduced: this synthetic workload
does not establish a stable production predicate or index-maintenance trade-off.
Because the ANN path is forced for comparability, its timings do not predict the
unforced planner's production path selection.

Do not use these results to infer tenant isolation, RLS behavior, application query
performance, or production capacity. Application schemas and policies remain outside
Phase 01 S2.
