# Module: model gateway

- **Status:** Implemented locally (Phase 03; exit gate blocked on O6)
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
- `cost_tracker.py` (`BudgetTracker`): exact Decimal cost math, integer micro-USD conversions, atomic Redis increments, and cancellation shielding.
- `cache_manager.py` (`ContextCacheManager`): multi-tenant context cache management (`maya:cache:{workspace_id}:{model_id}:{content_hash}`) with 404 recovery.
- `sanitizer.py`: UTF-8 verification, lone surrogate rejection, and ASCII control character stripping.
- `validator.py`: Pydantic structured output validation, code fence stripping, and self-repair prompt generation.
- `startup.py`: startup configuration validation and config-gated remote model discovery.
- `client_adapter.py`: explicit SDK client factory without implicit environment credential leakage.
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
- 400, 401, 403, and safety blocks fail immediately without fallback.
- Stream interruptions raise `StreamInterruptedError` and record estimated usage without falling back.
- Once tool calls have started emitting, no retry or fallback is permitted (`ToolCallCommitmentError`).
