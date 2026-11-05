# ADR-0003: Task queue selection

- **Status:** Accepted
- **Date:** 2026-11-05
- **Deciders:** Repository owner
- **Related:** [Phase 01 S1 spike](../architecture/SPIKE_S1_QUEUE.md), [MAYA AI final plan D8](../../MAYA_AI_FINAL_PLAN.md)

## Context

The plan names Celery as the default job runner and ARQ as its alternative.
The Phase 01 S1 spike compared both against the same async LangGraph workload
because the later task engine needs cancellation, worker recovery, and low
event-loop impact. The backend currently pins redis-py 8.1.0.

The measured run used Python 3.12.10, Celery 5.6.3, ARQ 0.28.0, LangGraph
1.2.20, PostgreSQL 17, Redis server 7.4, and an isolated shared redis-py 5.3.1
benchmark lock. The reproducible workload and full raw data are in the
[spike report](../architecture/SPIKE_S1_QUEUE.md) and its linked JSON output.

## Decision

**Accepted owner decision (2026-11-05):** use Celery 5.6.3 with explicit Kombu
5.7.0 and the unchanged redis-py 8.1.0 for the planned Phase 06 task engine.
The owner explicitly accepted the compatible candidate after its isolated
transport/recovery run. Durable cooperative cancellation and terminal-state
reconciliation are mandatory before Phase 06 acceptance; native revoke alone
is insufficient. Acceptance does not install or integrate the queue now.

The original proposal and compatibility addendum below are retained as
chronological evidence; this accepted decision supersedes their open
recommendation/status statements.

**Original proposal (superseded):** prefer ARQ for the measured workload, conditional
on resolving its runtime dependency incompatibility and reviewing its longer
redelivery delay. In the single measured 80-task batch, ARQ achieved 28.68
tasks/s versus Celery's 20.81, p95 completion latency of 2,387.75 ms versus
3,123.05 ms, and lower observed driver-container memory. ARQ's abort was
acknowledged and reached `aborted`; Celery's revoke request did not reach an
observed terminal state in four seconds.

This is **not approval to integrate either runner**. ARQ 0.28.0 declares
redis-py `>=4.2.0,<6`; Celery 5.6.3's Redis extra resolves through Kombu to
redis-py `>=4.5.2,<6.5` with excluded releases. Both conflict with the current
`redis==8.1.0` backend pin. The spike did not change or downgrade the runtime.
Until a compatible queue/client combination is established, neither candidate
is approved for Phase 06.

## Alternatives considered

- **Celery:** redelivered after worker loss in 4,037 ms, but its revoke request
  yielded no confirmed terminal cancellation in the observation window. It used
  more memory in this run and its Redis extra also conflicts with redis-py 8.1.0.
- **ARQ:** measured better throughput, completion latency, memory, and native
  abort behavior. Redelivery took 18,975 ms with the configured 12-second job
  timeout and 1,500 ms extra in-progress expiry; its dependency range is
  incompatible with the current runtime.
- **Keep the choice open:** required for integration until the dependency
  compatibility blocker is resolved; this is the current implementation state.

## Consequences

- Positive: the proposal is grounded in a runnable benchmark and explicit
  failure/cancellation evidence rather than framework assumptions.
- Negative: the best-measured candidate cannot be integrated with the current
  backend Redis client pin; one run is not enough to establish production
  performance or reliability.
- Mitigations: keep this ADR Proposed, do not modify the runtime dependencies,
  rerun the spike after a compatible client/framework release or an approved
  dependency decision, and validate recovery/cancellation with the Phase 06
  task lifecycle before adoption.

## Security and compliance impact

The spike used no credentials, dotenv files, Gemini calls, external application
APIs, or real user data. Runtime benchmark containers shared only a newly
created internal Docker network, had no published ports or host mounts, and
used a disposable PostgreSQL schema and data. The runner removed only
run-labelled resources that it created; the report records cleanup evidence.

## Implementation notes

The spike harness, exact dependency lock, and raw evidence are under
`backend/tests/spikes/`. No application queue implementation was added. The
ADR index is maintained separately and is not changed by this scoped spike.

## Review trigger

Revisit before implementing the Phase 06 queue integration, after the required
cancellation behavior is designed, or if the backend Redis client policy or
candidate package compatibility changes.

## Compatibility review addendum (2026-11-05)

The [S1 compatibility follow-up](../architecture/SPIKE_S1_QUEUE.md#compatibility-follow-up)
found that Celery 5.6.3 with the newly checked Kombu 5.7.0 resolves against the
backend's unchanged redis-py 8.1.0. Kombu 5.7.0 declares Redis `>=6.4.0,<9.0.0`.
The original measured run used a different transitive lock, so the historical
constraints and behavioral measurements above remain specific to that run.
ARQ 0.28.0 still fails resolution with redis-py 8.1.0.

The [approved isolated runtime run](../architecture/SPIKE_S1_QUEUE.md#isolated-runtime-validation-2026-11-05)
then exercised Celery 5.6.3/Kombu 5.7.0/redis-py 8.1.0 against Redis 7.4 and
the synthetic PostgreSQL workload without changing the backend runtime lock.
All 80 tasks completed; after controlled worker loss, Celery redelivered and
completed attempt 2, preserving one synthetic effect row across two attempts.
This is bounded transport and recovery evidence, not exactly-once proof.

Cancellation remains unsuccessful: the Celery revoke request returned, but no
worker acknowledgment or terminal cancellation state was observed within four
seconds; the task row remained `running`. The task engine must use a durable
cancellation request, cooperative checks at safe boundaries, and persisted
terminal cancellation evidence with recovery/reconciliation. A returned revoke
request is not proof of cancellation.

**Proposed recommendation:** Celery 5.6.3 with explicit Kombu 5.7.0 and the
unchanged redis-py 8.1.0 is the only candidate verified against the current
runtime pin and is recommended for owner review and Phase 06 design, conditional
on implementing the cancellation strategy. ARQ 0.28.0 remains incompatible
with redis-py 8.1.0 and was not runtime-tested in this follow-up. No queue is
accepted, no backend runtime dependency changed, and this ADR remains Proposed.
