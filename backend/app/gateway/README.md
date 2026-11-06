# Module: model gateway

- **Status:** Implemented locally (Phase 03; exit gate waits for the owner-run live check)
- **Owner:** Repository owner
- **Location:** `backend/app/gateway/`
- **Architecture Documentation:** [MODEL_GATEWAY.md](../../../docs/architecture/MODEL_GATEWAY.md)
- **Architecture Decision Record:** [ADR-0007](../../../docs/adr/0007-gemini-model-gateway-architecture.md)

## Responsibility

Centralized model gateway abstracting AI operations behind stable role aliases (`orchestrator`, `heavy`, `lite`, `embed`), providing cycle-safe fallback traversal, token and cost accounting, context caching, resilience, and input sanitization. The gateway never executes tools directly and is not wired to public API endpoints in Phase 03.

## Implemented Components

- `model_gateway.py` (`ModelGateway`): unified engine coordinating the 6 operations (`generate_text`, `generate_stream`, `generate_structured`, `generate_with_tools`, `embed_content`, `count_tokens`).
- `registry.py` (`ModelRegistry`): role alias specifications, model normalization, and terminating fallback chain traversal.
- `resilience.py` (`ResiliencePolicy`): backoff logic (429 retries with full jitter, immediate 5xx failover, tool call commitment guards).
- `cost_tracker.py` (`BudgetTracker`): exact Decimal cost math, integer micro-USD conversions, atomic Redis increments, and per-attempt distributed admission leases with reserved-cost reconciliation.
- `cache_manager.py` (`ContextCacheManager`): workspace-scoped Redis store for provider cache references (`maya:cache:{workspace_id}:{model_id}:{content_hash}`) with a 404 purge handler. Not yet used by `ModelGateway`; provider cache creation and attachment are not implemented.
- `sanitizer.py`: UTF-8 verification, lone surrogate rejection, and ASCII control character stripping in strings and nested lists/dicts (typed SDK objects pass through).
- `validator.py`: Pydantic structured output validation, code fence stripping, and self-repair prompt generation.
- `startup.py`: startup configuration validation and config-gated remote model discovery.
- `client_adapter.py`: explicit SDK client factory (explicit key, `vertexai=False`, SDK retries disabled with `HttpRetryOptions(attempts=1)`).
- `exceptions.py`: complete typed exception hierarchy.

## Dependencies

- Depends on: `app.core` (settings, logging, clock, Redis), `google.genai`.
- Depended on by: Phase 04 (Chat), Phase 05 (Orchestrator), Phase 09 (Specialist Agents), and Phase 11 (Memory).

## Configuration

Settings are documented in the [configuration reference](../../../docs/guides/CONFIGURATION.md):
- `MAYA_GEMINI_API_KEY`: API key (optional in local tests; never logged).
- `MAYA_GEMINI_MODEL_OVERRIDES`: JSON object overriding model IDs for REST aliases.
- `MAYA_GEMINI_VERIFY_MODELS_ON_STARTUP`: startup discovery switch (defaults to false).
- `MAYA_GEMINI_REQUEST_TIMEOUT_SECONDS`: per-request timeout (defaults to 30.0).
- `MAYA_BUDGET_ENFORCEMENT_MODE`: enforcement mode (`log_only` by default, `enforce` when approved).
- `MAYA_GEMINI_BUDGET_TASK_USD_ALERT`: task budget alert threshold ($1.50 proposal).
- `MAYA_GEMINI_MAX_TOOL_TURNS_ALERT`: task tool turn alert threshold (10 turns proposal).
- `MAYA_GEMINI_BUDGET_TASK_USD_CAP`: task budget cap for enforce mode (proposal).
- `MAYA_GEMINI_MAX_TOOL_TURNS_CAP`: task tool turns cap for enforce mode (proposal).

## Failure Behavior

- 429 rate limits and timeouts back off with full jitter across 4 retries before falling back.
- 5xx errors trigger immediate failover to the secondary fallback model.
- 400, 401, 403, and provider content blocks (`ContentBlockedError`) fail immediately without fallback.
- Streams retry or fall back only until the first chunk; later failures, cancellation and early close record estimated usage, and failures raise `StreamInterruptedError`.
- `generate_with_tools` is non-streaming, so tool calls are never seen from an attempt that is later retried; `ToolCallCommitmentError` guards the policy for a future streaming variant.
- Errors are classified by type and status code only; logs and exceptions carry the exception class and HTTP status, never provider text.
- Unknown cost is `None` (never `0`); in enforce mode a task-scoped call is rejected before the provider when its applicable price is unknown.
- `generate_with_tools` accepts optional `max_output_tokens`; enforced generation/stream/tool calls clamp this value to the model's registered output limit and send that ceiling in the provider request.
- Enforced billable calls require both `task_id` and `workspace_id` and an available budget tracker. Calls without this scope, with unknown required pricing, or without a finite output-token bound are rejected before contacting the provider. `count_tokens` is not treated as a billable generation/embedding operation.
- Each enforced generation attempt atomically reserves a conservative upper-bound cost using the model's registered context-input limit, the applicable input price, and a provider-requested output-token ceiling no greater than the model's registered output limit. Embedding attempts reserve the registered input limit per submitted item. Retries and fallbacks make separate reservations for their respective model specifications. Concurrent attempts are checked against both recorded spend and outstanding reservations.
- On responses with provider usage metadata, usage accounting and reservation reconciliation are one atomic Redis operation. For estimated/missing usage (embeddings, partial streams, provider errors, and cancellation before a response), the full reservation is conservatively recorded before the lease can be released; no refund is inferred from an estimate. If accounting fails or is cancelled, the reservation remains held; the lease is released only after accounting finishes, or expires after the task-counter retention period for crash recovery. Redis admission and accounting errors fail closed.
- `log_only` continues to meter/alert without requiring scope, reserving budget, or constraining provider output.
- Reservations are conservative estimates, not mathematical provider-billing hard caps. Registry rates and model context/output limits are not provider-enforced billing controls, and provider billing/usage can differ from reported metadata. Exact provider-side hard caps are impossible without provider-enforced controls; a response charged above the reservation is recorded and causes the normal post-accounting cap check to fail, but the excess cannot be prevented retroactively.
