# S1 Celery Compatibility Follow-up — Change Report

## Summary

**Status: Implemented and Tested.** Added and ran an isolated Celery 5.6.3 /
Kombu 5.7.0 / redis-py 8.1.0 candidate against the bounded synthetic workload.
The backend runtime dependencies and historical S1 lock were not changed; queue
selection remains Proposed pending owner acceptance.

## Motivation

Resolve the Phase 01 S1 Redis-client compatibility question with actual transport,
async LangGraph task, worker-loss redelivery, synthetic idempotent-effect, and
cancellation observations rather than dependency metadata alone.

## Files Changed

| File | Change | Status |
| :--- | :--- | :--- |
| `backend/tests/spikes/spike_s1_queue.py` | Added candidate selection, isolated lock metadata, and candidate plan output. | Implemented |
| `backend/tests/spikes/s1/celery_compat/pyproject.toml` | Added exact-pinned Celery/Kombu/Redis candidate and strict type-check settings. | Implemented |
| `backend/tests/spikes/s1/celery_compat/uv.lock` | Added separate resolved candidate lock; historical lock remains unchanged. | Implemented |
| `backend/tests/spikes/s1/celery_compat/Dockerfile` | Added isolated candidate image build using the candidate lock. | Implemented |
| `backend/tests/spikes/s1/orchestrator.py` | Added candidate image/run path with existing run-labelled resource ownership and cleanup. | Implemented |
| `backend/tests/spikes/s1/driver.py` | Made ARQ imports lazy, recorded package versions, and distinguished revoke return from terminal cancellation. | Implemented |
| `backend/tests/spikes/s1/runtime.py` | Kept shared synthetic task logic importable without installing ARQ in the Celery candidate. | Implemented |
| `backend/tests/spikes/s1/arq_worker.py` | Isolated ARQ-only worker configuration. | Implemented |
| `backend/tests/spikes/s1/metrics.py` | Added deterministic cancellation-outcome classification. | Implemented |
| `backend/tests/unit/test_spike_s1_queue.py` | Added fake-executor candidate selection, collision, lock, and negative cancellation tests. | Tested |
| `backend/pyproject.toml` | Precisely excluded the ARQ-only worker entrypoint from host mypy discovery; it is checked with the isolated S1 lock. | Implemented |
| `backend/tests/spikes/s1/results/s1-queue-20261105T053205Z-celery-compat-f0f619c78f47.json` | Persisted measured run evidence and final candidate-specific metadata. | Tested |
| `docs/architecture/SPIKE_S1_QUEUE.md` | Recorded compatibility run, cancellation limitation, memory scope, cleanup and proposed recommendation. | Implemented |
| `docs/adr/0003-task-queue-selection.md` | Added measured compatibility follow-up; status remains Proposed. | Implemented |

## Architecture Impact

No application queue or backend runtime integration was added. The candidate runs
only in its own locked project and ephemeral runner image. The S1 harness now
selects either the historical comparison lock or the isolated Celery-compatible
lock. ADR-0003 remains Proposed and the ADR index was left to its owner.

## Security Impact

No credentials, `.env` files, Gemini calls, production resources, external
application APIs, or real user data were used. The run used an internal
Docker network, no published ports or host mounts, and synthetic PostgreSQL
data. Cleanup targeted only resources labeled with this run ID.

## Tests Added or Updated

- Fake-executor tests cover candidate lock selection, no-Docker plan mode,
  candidate Dockerfile selection, name collision failure, and unsupported
  candidate rejection.
- Deterministic negative tests verify that a returned request, a still-running
  task, or a terminal non-cancel state is not classified as terminal cancellation.
- Candidate and historical isolated spike modules were checked with their
  respective locked mypy environments.

## Validation Results

| Command | Result |
| :--- | :--- |
| `uv lock --project backend/tests/spikes/s1/celery_compat --python 3.12` | Passed; resolved 61 packages. |
| `uv sync --project backend/tests/spikes/s1/celery_compat --frozen --group dev` | Passed; installed exact candidate and type-check environment in isolated `.venv`. |
| `python backend/tests/spikes/spike_s1_queue.py --candidate celery-compat --jobs 80 --seed 20261105 --timeout-seconds 120` | Passed; plan-only mode created no Docker resources. |
| `python backend/tests/spikes/spike_s1_queue.py --candidate celery-compat --run --jobs 80 --seed 20261105 --timeout-seconds 120` | Passed; 80/80 tasks completed, redelivery measured, cleanup reported no errors. |
| `uv run --directory backend --project tests/spikes/s1 --frozen --group dev pytest -q tests/unit/test_spike_s1_queue.py` | 43 passed. The isolated test environment emitted two non-fatal unknown pytest-asyncio config warnings. |
| `uv run --directory backend --no-sync pytest -q` | 146 passed. |
| `uv run --directory backend --no-sync mypy .` | Passed; 30 source files checked. |
| `uv run --directory backend --no-sync ruff check .` | Passed. |
| `uv run --project s1/celery_compat --frozen --group dev mypy --config-file s1/celery_compat/pyproject.toml s1/driver.py s1/runtime.py s1/metrics.py` (from `backend/tests/spikes`) | Passed; 3 source files checked. |
| `uv run --project tests/spikes/s1 --frozen --group dev mypy --config-file tests/spikes/s1/pyproject.toml tests/spikes/s1/driver.py tests/spikes/s1/runtime.py tests/spikes/s1/metrics.py tests/spikes/s1/arq_worker.py` (from `backend`) | Passed; 4 source files checked. |
| `npm run validate` | First attempt failed because the validator's child processes could not find backend `ruff`, `mypy`, or `pytest` on the inherited `PATH`; no code check was reported as passing by that attempt. |
| `$env:PATH = (Resolve-Path 'backend\.venv\Scripts').Path + ';' + $env:PATH; npm run validate` | Passed: 10 passed, 0 failed, 4 skipped because no base branch was available. Documentation check covered 134 Markdown files. |

## Documentation Updated

Updated the S1 architecture report and ADR-0003. The shared changelog and ADR
index were not edited because those are maintained by the parent owner.

## Database / API Impact

No application schema or API changes. The run created and dropped only the
synthetic `s1_bench` schema in its disposable PostgreSQL container.

## Risks and Limitations

- This was one bounded Celery-only candidate run, not a paired or statistically
  stable comparison with the historical run.
- The revoke request returned but no worker acknowledgment or terminal
  cancellation was observed; the task remained `running` at four seconds.
- Worker-loss recovery completed attempt 2 after 6,986.865 ms. The synthetic
  effect table retained one row across two attempts; this is not exactly-once
  proof for real effects.
- Maximum memory (252,077,670 bytes) covers the driver container and worker
  children, not Redis or PostgreSQL. It was limited to 768 MiB; CPU limit was
  one core. See the raw result for all samples and environment metadata.
- The raw JSON's numeric measurements were not changed after the run. Its
  compatibility label and active queue settings were normalized to identify
  the actual Redis 8 candidate and omit inactive ARQ settings.

## Approval Required

Owner approval in the current request covered the exact-pinned isolated
Celery/Kombu/Redis install, run-labelled temporary Docker resources and
synthetic disposable PostgreSQL data. No backend runtime dependency was changed.

## Remaining Work

Owner review/acceptance of the queue recommendation is still required. Before
Phase 06, design and implement the durable/cooperative cancellation and terminal
acknowledgment/reconciliation strategy described in the S1 report. No queue is
accepted by this change.
