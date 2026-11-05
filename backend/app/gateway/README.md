# Module: model gateway

- **Status:** In progress (Phase 03 implementation; local tests only)
- **Owner:** Repository owner
- **Location:** `backend/app/gateway/`

## Responsibility

Centralized model gateway abstracting AI operations behind stable role aliases (`orchestrator`, `heavy`, `lite`, `embed`), providing cycle-safe fallback traversal, token and cost accounting, context caching, and input sanitization. The gateway never executes tools directly and is not wired to public API endpoints in Phase 03.

## Interfaces

- `ModelRegistry` (`registry.py`): canonical role aliases, spec lookups, model ID normalization, and terminating fallback chain resolution.
- `exceptions.py`: typed error hierarchy (`GatewayConfigurationError`, `UnsupportedAliasTransportError`, `GatewayFallbackExhaustedError`, `ToolCallCommitmentError`, `StreamInterruptedError`, `EmbeddingDimensionMismatchError`, `BudgetExceededError`).
- Planned interfaces (subsequent slices): `ModelGateway` protocol, `ResiliencePolicy`, `UsageTracker`, `CacheManager`, and `PromptSanitizer`.

## Dependencies

- Depends on: `app.core` (settings, logging, clock, Redis).
- Depended on by: Phase 04 (Chat), Phase 05 (Orchestrator), Phase 09 (Specialist Agents), and Phase 11 (Memory).

## Configuration

Settings are documented in the [configuration reference](../../../docs/guides/CONFIGURATION.md):
- `MAYA_GEMINI_API_KEY`: API key (optional in local tests; never logged).
- `MAYA_GEMINI_VERIFY_MODELS_ON_STARTUP`: startup discovery switch (defaults to false).
- `MAYA_GEMINI_REQUEST_TIMEOUT_SECONDS`: per-request timeout (defaults to 30.0).
- `MAYA_BUDGET_ENFORCEMENT_MODE`: enforcement mode (`log_only` by default, `enforce` when approved).
- `MAYA_GEMINI_BUDGET_TASK_USD_ALERT`: task budget alert threshold ($1.50 proposal).
- `MAYA_GEMINI_MAX_TOOL_TURNS_ALERT`: task tool turn alert threshold (10 turns proposal).

## Failure behavior

- 429 rate limits and timeouts back off with full jitter across 4 retries before falling back.
- 5xx errors trigger immediate failover to the secondary fallback model.
- 400, 401, 403, and safety blocks fail immediately without fallback.
- Stream interruptions raise `StreamInterruptedError` and record estimated usage without falling back.
- Once tool calls have started emitting, no retry or fallback is permitted.
