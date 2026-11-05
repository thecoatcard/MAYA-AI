# ADR-0007: Gemini model gateway architecture, resilience, and token accounting

- **Status:** Proposed
- **Date:** 2026-11-05
- **Deciders:** Repository owner, AI architecture team
- **Related:** [Phase 03](../implementation/IMPLEMENTATION_PHASES/PHASE_03_GEMINI_MODEL_GATEWAY.md), [Model Gateway Architecture](../architecture/MODEL_GATEWAY.md), [ADR-0004](0004-local-foundation.md), master plan section 7, Decision D13

## Context

MAYA AI requires an abstraction over LLM providers to decouple agent application logic from upstream SDKs and model IDs. Master plan §7 and Decision D13 establish Google Gemini as the core model family across 4 role aliases: `orchestrator` (`gemini-3.8-flash`), `heavy` (`gemini-4-argon`), `lite` (`gemini-3.5-flash-lite`), and `embed` (`gemini-embedding-2`).

Key requirements and challenges identified during Phase 03 planning:
1. **Fallback Traversal:** Need cycle-safe, terminating resolution paths so failed requests smoothly degrade without infinite loops or unbounded latency.
2. **Resilience Policy:** API rate limits (429) and timeouts require exponential backoff with full jitter before model failover; 5xx errors require immediate failover; 4xx and safety blocks must never retry or fall back.
3. **Tool Call Commitment:** Once a model turn emits tool calls, non-idempotent side effects may be triggered; therefore, retries or fallbacks after tool calls have started must be strictly forbidden (`ToolCallCommitmentError`).
4. **Structured Output Self-Repair:** JSON responses failing Pydantic schema validation require exactly one corrective retry turn providing the schema and error description before failing closed.
5. **Accurate Financial Metering:** Floating-point rounding drift is unacceptable for billing and budget enforcement. Token usage must use exact `Decimal` math and integer micro-USD in Redis (`HINCRBY`) shielded against task cancellation.
6. **Multi-Tenant Context Caching:** Large workspace context caches must be isolated by `workspace_id`, with automatic 404 recovery when provider-side caches expire before local Redis TTLs.
7. **Offline-First Safety:** Agents must never incur unexpected financial charges. Automated tests and agents must run strictly offline using high-fidelity test doubles (`FakeGeminiClient`), while live checks require an owner-run smoke tool with an explicit INR 100 confirmation gate.

## Decision

1. **Centralized Gateway Package (`backend/app/gateway`):**
   Implement a unified `ModelGateway` providing 6 typed operations:
   - `generate_text`: standard text generation with fallback resilience.
   - `generate_stream`: chunk streaming with pre-chunk retry and mid-flight interruption safety (`StreamInterruptedError` with `is_usage_estimate=True`).
   - `generate_structured`: strict Pydantic model validation with exactly one self-repair turn.
   - `generate_with_tools`: tool call generation without local tool execution.
   - `embed_content`: strictly enforces 768 output dimensions with opt-in fallback only.
   - `count_tokens`: offline token estimation / counting.

2. **Model Registry & Terminating Fallback Graph:**
   All operations target logical aliases rather than hardcoded model strings. Enabled aliases resolve terminating paths (`orchestrator` -> `lite`; `heavy` -> `orchestrator` -> `lite`; `lite` -> `orchestrator`; `embed` -> `embed-fallback`). Startup validation rejects unresolvable, cyclic, or disabled fallback targets. Model ID overrides are permitted via validated configuration for REST aliases only.

3. **Injected Resilience Policy:**
   `ResiliencePolicy` implements 4 retries for 429 and timeouts with base delays `(2.0, 4.0, 8.0, 16.0)` and full jitter (`uniform(0, delay)`). Clock, sleep, and random generators are injected to ensure 100% deterministic test execution.

4. **Exact Decimal Accounting and Redis Budgeting:**
   - Financial rates are stored as `Decimal` per 1M tokens. Unknown prices (`research`, `tts`, `embed-fallback`, or overridden aliases) are flagged `price_unknown=True` and `is_estimated_cost_complete=False`.
   - USD costs convert to micro-USD integers via `ROUND_UP` to guarantee budgets never undercount.
   - Redis updates use atomic pipelines (`HINCRBY cost_micro_usd`, `HINCRBY tool_turns`, `HINCRBY total_tokens`, `EXPIRE 86400`) wrapped in `asyncio.shield`.
   - `log_only` mode logs warnings when alert thresholds ($1.50 / 10 turns) are exceeded; `enforce` mode raises `BudgetExceededError`.

5. **Context Cache Management & 404 Recovery:**
   Redis stores provider cache references under `maya:cache:{workspace_id}:{model_id}:{content_hash}` with mandatory `workspace_id`. If a provider returns 404, the local key is purged and the request retries uncached.

6. **Input Sanitization:**
   All inputs are validated for UTF-8 encodability (lone surrogates rejected), ASCII control characters stripped (preserving `\n`, `\r`, `\t`), and length limits enforced.

7. **Lifespan Integration & Owner-Only Smoke Tool:**
   - FastAPI lifespan runs offline registry checks on startup. Remote model discovery is disabled by default (`gemini_verify_models_on_startup=False`) and gracefully skipped if no key is set.
   - A standalone diagnostic tool (`backend/app/tools/gemini_smoke.py`) is provided for repository owner execution only, guarded by `--confirm-paid-calls-up-to-inr-100`.

## Consequences

### Positive
- Unified, typed interface for all LLM interactions across all agent phases.
- Zero floating-point rounding drift in token metering and billing.
- Complete isolation across tenants for context caching and task budgets.
- High resilience to transient provider hiccups and rate limits without manual intervention.
- Automated tests run 100% offline, deterministic, and fast without external dependencies.

### Negative / Trade-offs
- Redis is required for atomic multi-turn task budgeting and context cache tracking (mocked in unit tests; verified via test containers).
- Secondary embedding fallback produces a non-comparable vector space, necessitating `allow_fallback=False` by default.
