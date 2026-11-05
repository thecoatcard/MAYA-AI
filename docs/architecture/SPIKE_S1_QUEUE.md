# S1 queue spike: Celery versus ARQ

**Status:** Measured, bounded runs; owner accepted the compatible Celery candidate
for planned Phase 06 integration with mandatory durable cooperative cancellation.
**Run date:** 2026-11-05.
**Related decision:** [Accepted ADR-0003](../adr/0003-task-queue-selection.md).

## Result in brief

With the same three-node async LangGraph workload and a shared Redis/PostgreSQL
container environment, ARQ measured higher throughput, lower queue-to-completion
latency, lower event-loop lag, and lower observed container memory than Celery.
ARQ's native abort reached a terminal `aborted` state; Celery's revoke request did
not produce an observed terminal task state in the four-second observation window.
Both runners redelivered after a controlled worker kill and completed a unique,
idempotent synthetic database effect on the second attempt.

**Historical benchmark-lock blocker:** the current backend pins `redis-py==8.1.0`.
ARQ 0.28.0 requires `redis-py>=4.2.0,<6`; Celery 5.6.3's Redis extra resolves
through Kombu to `redis-py>=4.5.2,<6.5` (with two excluded releases). Therefore
neither Redis extra in that benchmark lock is dependency-compatible with the runtime pin.
The benchmark used the common supported `redis-py==5.3.1` in its isolated lock;
the backend manifest and lock were not changed. A later isolated candidate lock
and runtime check resolved this compatibility path for Celery only; ARQ remains
incompatible with the current Redis pin. No runtime downgrade is proposed by this spike.
A later metadata/resolver check found a compatible Celery
candidate with Kombu 5.7.0; it was then runtime-tested in a separate candidate
lock. See [the compatibility follow-up](#compatibility-follow-up). The original
benchmark lock and backend runtime dependencies remain unchanged.

## Reproduction

From the repository root, with Python 3.12.10, uv 0.12.17, and Docker available:

```powershell
python backend/tests/spikes/spike_s1_queue.py --run --jobs 80 --seed 20261105 --timeout-seconds 120
```

The runner is opt-in: without `--run` it prints a plan and creates no resources.
It never loads dotenv files, reads credentials, or invokes an external application
API. A cold run needs package/image registry access while building the pinned
image; benchmark containers run afterward on a new Docker `--internal` network
with no published ports or host mounts.

The checked-in [S1 lock](../../backend/tests/spikes/s1/uv.lock) has SHA-256
`f8dfdbcb71da9f03b0990cd6a6535508dab5f4a015a8e3ce1ca0a7d01ded6582`.
Direct benchmark pins are ARQ 0.28.0, Celery 5.6.3, LangGraph 1.2.20,
asyncpg 0.32.0, and redis-py 5.3.1; the isolated type/test group pins mypy
2.4.1 and pytest 9.2.0. The Python 3.12.10 base image is digest-pinned in the
S1 Dockerfile; pgvector/pg17 and Redis 7.4 service images are digest-pinned in
the orchestration code.

The raw, per-task, machine-readable result is
[s1-queue-20261105T043046Z-521eeeff2a21.json](../../backend/tests/spikes/s1/results/s1-queue-20261105T043046Z-521eeeff2a21.json).
It records the invocation, seed, host and Docker versions, lock hash, image IDs,
queue settings, per-task latencies and event-loop measurements, sampled Docker
stats, and cleanup outcome.

## Workload and settings

- 80 throughput tasks per queue, concurrency 2, seed `20261105`.
- Each task runs the same compiled LangGraph `StateGraph`: three async nodes,
  each yielding for a seed-generated 8–15 ms; completion writes synthetic
  PostgreSQL receipts.
- Cancellation uses one three-node task with a five-second async delay per node.
- Failure injection kills only the runner's own Linux worker process group after
  it writes one synthetic, unique effect row; a fresh worker is then started.
- Celery uses prefork, late acknowledgment, reject-on-worker-lost, prefetch 1,
  and Redis visibility timeout 3 seconds.
- ARQ uses `max_jobs=2`, `poll_delay=0.02` seconds, a 12-second job timeout,
  and `expires_extra_ms=1500`.
- Redis 7.4 has AOF enabled. PostgreSQL uses a disposable `s1_benchmark`
  database and `s1_bench` schema. The synthetic database accepts trust
  authentication only on the private, internal Docker network.
- Each driver container is limited to 1 CPU and 768 MiB; database and Redis
  services are separately limited. No ports are bound to the host.

## Measurements

| Measurement | Celery 5.6.3 | ARQ 0.28.0 |
| :--- | ---: | ---: |
| Completed throughput batch | 80 / 80 | 80 / 80 |
| Throughput (tasks/s) | 20.81 | 28.68 |
| Queue-to-completion latency p50 (ms) | 1,574.68 | 1,317.86 |
| Queue-to-completion latency p95 (ms) | 3,123.05 | 2,387.75 |
| Per-task worker event-loop lag p95 (ms) | 1.294 | 1.259 |
| Producer event-loop maximum lag (ms) | 27.38 | 1.41 |
| Native cancellation observation | Revoke request returned; task row still `running` at 4 s; no terminal state observed | `Job.abort` acknowledged; task reached `aborted` in 54.55 ms |
| Redelivery after worker kill | Completed on attempt 2; 4,037 ms from worker kill to attempt 2 | Completed on attempt 2; 18,975 ms from worker kill to attempt 2 |
| Synthetic effect rows / observations | 1 / 2 | 1 / 2 |
| Maximum driver-container memory (bytes) | 241,487,052 | 151,414,374 |
| Maximum driver-container CPU | 100.17% | 99.41% |
| Maximum process count | 10 | 4 |

Latency is measured from the PostgreSQL enqueue receipt to the completion receipt.
Throughput includes submission and waiting for all tasks to complete. Worker
event-loop lag is sampled by a 5 ms async heartbeat; Celery creates an
`asyncio.run` loop per task, whereas ARQ uses its worker's shared event loop.
Producer lag is measured separately in the driver. Docker stats were sampled
approximately every 200 ms; memory/CPU/process counts include the driver and its
worker children, but not the Redis or PostgreSQL containers.

The controlled-failure effect table is deliberately idempotent and synthetic:
both runs kept one effect row while recording two attempts. This demonstrates
redelivery and the test's duplicate guard, not exactly-once behavior for real
external side effects. The Celery revoke call does not wait for a worker reply
by default, so a returned API call is not cancellation proof; the task remained
`running` when this run's observation window ended.

## Interpretation and limits

This is one bounded run, not a statistically stable benchmark or a production
load test. The result covers short, I/O-yielding LangGraph nodes and synthetic
PostgreSQL receipts only: it excludes model calls, real tools, checkpoint
storage, production traffic, multi-worker-host behavior, and network failures.
The ARQ polling interval was deliberately set to 20 ms; ARQ's default 500 ms
poll interval materially reduced throughput in an earlier exploratory run, so
that unrecorded-configuration run is not used for the comparison above. The
failure-recovery delay also depends on ARQ's configured in-progress timeout.

On measured queue behavior alone, ARQ is the conditional preference for its
throughput, latency, memory, and explicit abort result. Its longer redelivery
delay must be considered when choosing the in-progress timeout. The runtime
dependency conflict in that measured lock blocked adoption of both candidates;
the [follow-up](#compatibility-follow-up) identifies a newer Celery combination
that passed one bounded runtime validation. This report does not authorize
changing the backend dependency. See the still-Proposed
[ADR-0003](../adr/0003-task-queue-selection.md) for the conditional recommendation.

## Cleanup evidence

The final run dropped `s1_bench` with `DROP SCHEMA ... CASCADE`, removed its
four owned containers, two named volumes, internal network, and temporary
runner image tag. Cleanup reported no errors. It published no ports and did not
mount host files. The ordinary unit tests use fake Docker executors and never
start containers.

## Compatibility follow-up

A fresh public PyPI metadata and resolver check on 2026-11-05 changed the dependency
picture without changing any benchmark results or installed application packages:

| Candidate | Verified package constraints | Resolver outcome |
| :--- | :--- | :--- |
| ARQ 0.28.0 with redis-py 8.1.0 | ARQ requires `redis[hiredis]>=4.2.0,<6` | Unsatisfiable |
| Celery 5.6.3, Kombu 5.7.0, redis-py 8.1.0 | Celery permits Kombu `>=5.6.0`; Kombu 5.7.0's Redis extra requires `redis>=6.4.0,<9.0.0` and `packaging==26.3` | Resolved with current backend direct pins and LangGraph 1.2.20 |

Sources are the public metadata for
[ARQ](https://pypi.org/pypi/arq/0.28.0/json),
[Celery](https://pypi.org/pypi/celery/5.6.3/json) and
[Kombu](https://pypi.org/pypi/kombu/5.7.0/json). No registry credentials were used.

The resolver-only command below reproduced the compatible candidate; it installs
nothing and is not the application's lockfile:

```powershell
@'
asyncpg==0.32.0
fastapi==0.162.0
pydantic==2.14.2
pydantic-settings==2.16.2
redis==8.1.0
sqlalchemy[asyncio]==2.0.57
uvicorn==0.54.0
celery[redis]==5.6.3
kombu==5.7.0
langgraph==1.2.20
'@ | uv pip compile - --python backend\.venv\Scripts\python.exe --no-config --no-header --no-annotate
```

It resolved 63 packages. Replacing the input with only ARQ 0.28.0 and redis-py
8.1.0 failed with the expected version-range conflict. Resolution proves declared
dependency satisfiability, **not** functioning Redis transport, cancellation,
redelivery or production compatibility.

### Isolated runtime validation (2026-11-05)

With owner approval, the candidate was installed and exercised in a separately
locked project; neither the backend runtime manifest nor the original measured
lock changed. The original lock SHA-256 remains
`f8dfdbcb71da9f03b0990cd6a6535508dab5f4a015a8e3ce1ca0a7d01ded6582`.
The isolated candidate lock
[`celery_compat/uv.lock`](../../backend/tests/spikes/s1/celery_compat/uv.lock)
has SHA-256
`0f7672404e6e89ddc7ddb910d1d5a068050de47c7c9f3be253721b9656732509`.
The run reports the actually installed Celery 5.6.3, Kombu 5.7.0,
redis-py 8.1.0, LangGraph 1.2.20 and asyncpg 0.32.0 versions in its raw JSON.

The bounded run used 80 jobs, seed `20261105`, concurrency 2 and the same
three-node synthetic async LangGraph workload. It used Python 3.12.10 in the
driver, Docker 29.8.2, Redis server 7.4 with AOF and a disposable PostgreSQL 17
database, on a private internal Docker network with no published ports or host
mounts. All 80 jobs completed; throughput was 22.79 tasks/s, queue-to-completion
latency p50 1,505.50 ms and p95 2,831.45 ms. Worker event-loop lag p95 was
1.22 ms. These are one candidate run, not a paired or statistically stable
comparison with the historical run.

The controlled worker loss occurred after the task wrote its synthetic
idempotent effect. Celery redelivered and completed attempt 2 after 6,986.865 ms;
the database retained one synthetic effect row and recorded two observations.
This confirms the tested Redis transport and this bounded recovery path, not
exactly-once behavior for real effects.

Cancellation remains a negative result: `revoke(terminate=True)` returned, but
there was no worker abort acknowledgment and the PostgreSQL task remained
`running` after the four-second observation window. No terminal cancellation
was observed. A later task-engine phase must not treat a returned revoke call as
proof of cancellation; it needs a durable `cancel_requested` state, cooperative
cancellation checks at safe task boundaries, and a persisted terminal
cancellation acknowledgment/status, with idempotent effects and reconciliation
for worker termination.

Maximum observed memory was 252,077,670 bytes in the Celery driver container
(limit 768 MiB; 24 Docker-stats samples). This scope includes the driver and its
worker children, but excludes Redis and PostgreSQL. CPU limit was 1 core and
maximum sampled CPU was 110.18%; Docker reports CPU as a percentage of one core.
Cleanup dropped the synthetic schema and removed all three owned containers,
both run-labelled volumes, the run-labelled internal network and candidate
image; cleanup reported no errors. The final raw record is
[`s1-queue-20261105T053205Z-celery-compat-f0f619c78f47.json`](../../backend/tests/spikes/s1/results/s1-queue-20261105T053205Z-celery-compat-f0f619c78f47.json)
(SHA-256 `bbc7b4574ad28e0449e1d64c10b472ff9a0bbe7a31e61b80fc668fbc7188e281`).

**Recommendation remains Proposed, not accepted:** Celery 5.6.3 with Kombu
5.7.0 and redis-py 8.1.0 is now the only candidate shown compatible with the
unchanged runtime and it passed the measured transport and redelivery scenario.
It is the recommended candidate for owner review and Phase 06 design, conditional
on the explicit cancellation strategy above. ARQ 0.28.0 remains resolver-
incompatible with redis-py 8.1.0 and was not runtime-tested with this candidate.
No production queue engine was added or approved.
