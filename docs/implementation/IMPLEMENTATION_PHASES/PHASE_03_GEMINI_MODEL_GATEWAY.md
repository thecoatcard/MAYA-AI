# Phase 03 — Gemini AI Integration and Model Gateway

## A. Phase Overview

- **Phase Number:** Phase 03
- **Phase Name:** Gemini AI Integration and Model Gateway
- **Objective:** Build the centralized Model Gateway abstracting model operations behind stable role aliases, with startup model discovery, retry/fallback behavior, usage metering, and context caching. Enforce budget limits only after the owner confirms the proposed caps.
- **Why This Phase is Necessary:** As established in [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) Decision D13, hard-coding model IDs inside agent code causes brittle failures when models churn, deprecate, or experience regional rate limits. A centralized gateway decouples agent logic from specific models, enforces financial guardrails, and provides transparent fallback without breaking ongoing execution.
- **Expected Outcome:** A `ModelGateway` using an explicitly pinned, compatibility-verified `google-genai` SDK; an MVP alias registry and startup checks; typed text, streaming, structured, tool-call, embedding, and token-counting operations; and tested usage, budget, retry, and failure handling.
- **Priority:** Critical
- **Estimated Complexity:** High
- **Estimated Duration:** 8 business days.

---

## B. Prerequisites

- **Required Completed Phases:** Phase 01 (Core Infrastructure and Application Foundation).
- **Required Infrastructure:** Python runtime environment; Redis 7.4 (for rate limit state and token caches).
- **Required Credentials or External Services:** Gemini credentials for live checks. Any live API call requires explicit approval first and is kept out of deterministic tests.
- **Required Architectural Decisions:** [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §7 and Decision D13. Open Decision O1 is not confirmed; do not make local Ollama support or a provider-abstraction layer an MVP requirement.
- **Required Documentation:** [`docs/architecture/SYSTEM_OVERVIEW.md`](../../architecture/SYSTEM_OVERVIEW.md), [`docs/guides/CONFIGURATION.md`](../../guides/CONFIGURATION.md).

---

## C. Scope

### Included Features

- Centralized Model Gateway interface (backend/app/gateway/model_gateway.py (planned artifact)).
- Configurable registry for the model aliases needed by MVP:
- `orchestrator`, `heavy`, `lite`, `live`, `live-thinking`, `research`, and `embed` per the master plan; `tts` is post-MVP and must not block startup.
- Normalize API model names (including any `models/` prefix) before comparing registry IDs.
- Startup validation checks enabled MVP aliases. Unavailable post-MVP aliases do not fail startup.
- The master plan's default fallback edges contain a cycle. Resolve the configured graph so each request has a bounded, terminating fallback traversal before enabling those defaults.
- Resilience policies:
  - HTTP 429: Full jitter exponential backoff (2s, 4s, 8s, 16s), then trigger fallback model.
  - HTTP 5xx: Immediate failover to secondary model in fallback chain.
  - Malformed JSON / Structured Output Error: Exactly one self-repair prompt attempt; if repair fails, trigger fallback.
- Token and financial accounting:
  - Real-time token consumption tracking, including prompt, candidate, thinking, tool-use, and cached tokens when reported by the provider.
  - Spend computation per request using pricing schedule from master plan §7.1.
  - Budget accounting and enforcement using owner-approved per-task and tool-turn limits. The master-plan values ($1.50 and 10 turns) remain proposals until confirmed.
- Context caching for eligible long-lived prompts and tool schemas, with cache references scoped by workspace in the application.

### Excluded Features

- LangGraph orchestration graph wiring (deferred to Phase 05).
- WebAudio streaming and voice activity detection in browser (deferred to Phase 12).
- Vector database similarity search (deferred to Phase 11).

### Deferred Features

- Fine-tuned custom model endpoints.
- Local Ollama/Gemma inference (not approved for MVP; revisit Open Decision O1 with the owner).

### Dependencies on Future Phases

- Phase 04 (Chat), Phase 05 (Orchestrator), Phase 09 (Specialist Agents), and Phase 11 (Memory) all route model requests exclusively through this gateway.

---

## D. Implementation Breakdown

### Task P03-T001: Model Registry and Configuration Schema

- **Task ID:** P03-T001
- **Task Name:** Define Model Registry Configuration and Alias Mappings
- **Detailed Description:** Create backend/app/gateway/registry.py (planned artifact) with Pydantic schemas defining `ModelSpec` (model ID, transport, max input/output tokens, cost per 1M input/output/cached tokens, fallback alias). Define the aliases needed for MVP. Enable approved alias overrides through configuration. Pricing and context figures marked unverified in master-plan §7 must be rechecked before enforcing cost decisions.
- **Expected Files:** backend/app/gateway/registry.py (planned artifact), `backend/app/core/config.py`.
- **Dependencies:** None.
- **Assigned Agent Role:** AI Infrastructure Engineer.
- **Required Tests:** `pytest backend/tests/unit/test_model_registry.py`.
- **Documentation Updates:** `docs/guides/CONFIGURATION.md`.
- **Acceptance Criteria:** Registry loads the required MVP aliases; cost models match §7.1; configured fallback traversal is explicitly tested to terminate; environment overrides succeed cleanly.

### Task P03-T002: Model Discovery and Startup Health Check

- **Task ID:** P03-T002
- **Task Name:** Implement Startup `models.list` Verification
- **Detailed Description:** Implement `verify_models_on_startup()` in backend/app/gateway/model_gateway.py (planned artifact). During startup, verify only enabled MVP aliases with the provider's model listing. Normalize provider model names before comparison. Unavailable post-MVP aliases such as TTS must not prevent startup; an enabled alias without a viable terminating fallback must fail clearly.
- **Expected Files:** backend/app/gateway/model_gateway.py (planned artifact), `backend/app/main.py`.
- **Dependencies:** P03-T001.
- **Assigned Agent Role:** AI Infrastructure Engineer.
- **Required Tests:** Deterministic startup tests use mocked responses. An opt-in live check is separate and requires explicit external-service approval.
- **Documentation Updates:** `docs/architecture/SYSTEM_OVERVIEW.md`.
- **Acceptance Criteria:** Startup verifies enabled MVP aliases; model names are normalized; missing optional/post-MVP aliases do not block startup; an enabled alias without a viable terminating fallback produces an explicit startup error.

### Task P03-T003: Core Model Gateway Execution with Fallback Engine

- **Task ID:** P03-T003
- **Task Name:** Build Request Dispatcher with Retries and Degraded Mode
- **Detailed Description:** Implement typed text, structured-output, streaming, tool-call, embedding, and token-counting operations. Define mid-stream failure and cancellation behavior. Apply retries/fallback only when safe, with exponential backoff and jitter for 429 and fallback for eligible 5xx errors. Bound traversal to a terminating configured fallback chain; include circuit breaking without replaying non-idempotent tool calls.
- **Expected Files:** backend/app/gateway/model_gateway.py (planned artifact), backend/app/gateway/exceptions.py (planned artifact).
- **Dependencies:** P03-T002.
- **Assigned Agent Role:** Senior AI Engineer.
- **Required Tests:** Deterministically simulate 429, 5xx, timeout, stream interruption, cancellation, and tool-call failure; verify no unsafe duplicate tool call on retry.
- **Documentation Updates:** The model-gateway architecture documentation, once created.
- **Acceptance Criteria:** Retry and fallback behavior is bounded and tested; streaming errors are surfaced explicitly; non-idempotent tool calls are not silently replayed.

### Task P03-T004: Structured Output Validation and Single-Turn Self-Repair

- **Task ID:** P03-T004
- **Task Name:** Implement Schema Validation and Output Self-Repair
- **Detailed Description:** Implement Pydantic schema validation for model structured outputs. If model output fails schema parsing, initiate exactly one repair turn passing the raw invalid output and validation error back to the model with an explicit correction prompt. If the second attempt fails, raise `StructuredOutputError` and fail over to the secondary model.
- **Expected Files:** backend/app/gateway/validator.py (planned artifact), backend/app/gateway/model_gateway.py (planned artifact).
- **Dependencies:** P03-T003.
- **Assigned Agent Role:** Senior AI Engineer.
- **Required Tests:** `pytest backend/tests/unit/test_structured_repair.py`.
- **Documentation Updates:** The model-gateway architecture documentation, once created.
- **Acceptance Criteria:** Valid JSON passes directly; malformed JSON triggers one repair turn; successful repair returns parsed Pydantic object; repeated failure triggers fallback.

### Task P03-T005: Token Metering, Financial Cost Tracking, and Budget Guardrails

- **Task ID:** P03-T005
- **Task Name:** Implement Cost Calculator and Task Budget Limiter
- **Detailed Description:** Implement `UsageTracker` for prompt, candidate, thinking, tool-use, and cached-token usage where returned, calculating cost with verified pricing data. Implement `BudgetManager` only after the owner confirms the per-task and tool-turn caps; count retries and repair work so they cannot bypass an approved cap.
- **Expected Files:** backend/app/gateway/cost_tracker.py (planned artifact), backend/app/gateway/model_gateway.py (planned artifact).
- **Dependencies:** P03-T001.
- **Assigned Agent Role:** Backend Systems Engineer.
- **Required Tests:** `pytest backend/tests/unit/test_cost_tracker.py`.
- **Documentation Updates:** The model-gateway architecture documentation, once created.
- **Acceptance Criteria:** Cost calculations account for token categories and cached pricing; usage is recorded without exposing prompt secrets. Budget enforcement uses only owner-approved limits.

### Task P03-T006: Context Caching Lifecycle Management

- **Task ID:** P03-T006
- **Task Name:** Implement Gemini Context Caching for System Prompts
- **Detailed Description:** Implement `CacheManager` using the provider's context-caching API where supported. Store cache references in Redis under workspace-scoped application keys; handle provider-assigned cache names and expiration without cross-workspace reuse.
- **Expected Files:** backend/app/gateway/cache_manager.py (planned artifact), backend/app/gateway/model_gateway.py (planned artifact).
- **Dependencies:** P03-T003.
- **Assigned Agent Role:** AI Performance Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_context_cache.py`.
- **Documentation Updates:** The model-gateway architecture documentation, once created.
- **Acceptance Criteria:** Eligible repeated prompts can reuse a correctly scoped cache; expiration and provider errors are handled explicitly. Record measured savings without an unapproved percentage gate.

---

## E. File and Folder Changes

- **Files to Create:**
  - backend/app/gateway/__init__.py (planned artifact)
  - backend/app/gateway/registry.py (planned artifact)
  - backend/app/gateway/model_gateway.py (planned artifact)
  - backend/app/gateway/cost_tracker.py (planned artifact)
  - backend/app/gateway/cache_manager.py (planned artifact)
  - backend/app/gateway/validator.py (planned artifact)
  - backend/app/gateway/exceptions.py (planned artifact)
  - backend/tests/unit/test_model_registry.py (planned artifact)
  - backend/tests/unit/test_gateway_fallback.py (planned artifact)
  - backend/tests/unit/test_structured_repair.py (planned artifact)
  - backend/tests/unit/test_cost_tracker.py (planned artifact)
  - backend/tests/integration/test_model_startup_check.py (planned artifact)
  - backend/tests/integration/test_context_cache.py (planned artifact)
  - model-gateway architecture documentation
- **Files to Modify:**
  - `backend/app/core/config.py`
  - `backend/app/main.py`
  - `docs/guides/CONFIGURATION.md`
- **Files to Avoid Modifying:**
  - `MAYA_AI_FINAL_PLAN.md`
- **Relevant Directories:** `backend/app/gateway/`, `docs/architecture/`.

---

## F. API and Data Contracts

### 1. Internal Model Gateway Interface

```python
class ModelGateway:
    async def generate_text(
        self,
        alias: str,
        contents: list[Any],
        system_instruction: str | None = None,
        temperature: float = 0.7,
        max_output_tokens: int | None = None,
        task_id: str | None = None,
        workspace_id: str | None = None
    ) -> GatewayResponse: ...

    async def generate_structured(
        self,
        alias: str,
        contents: list[Any],
        response_schema: type[T],
        system_instruction: str | None = None,
        task_id: str | None = None,
        workspace_id: str | None = None
    ) -> T: ...

```

Required operations beyond the two illustrated methods: text streaming, tool calls, embeddings, and token counting. Define typed request/response, cancellation, and error contracts for each before implementation.

### 2. Gateway Response Contract

```python
class GatewayResponse(BaseModel):
    text: str
    model_id: str
    alias_used: str
    was_fallback: bool
    input_tokens: int
    output_tokens: int
    cached_tokens: int
    estimated_cost_usd: Decimal
```

---

## G. Security Requirements

- **Secret Handling:** `GEMINI_API_KEY` stored exclusively in environment/vault; never echoed in responses, errors, or logs.
- **Data Isolation:** Prompts from different tenants must not share context cache instances; scope cache references by workspace in the application rather than assuming the provider accepts caller-chosen cache names.
- **Financial Protection:** Enforce a hard budget stop only after the owner confirms the proposed $1.50 per-task and 10-tool-turn values or replaces them with approved limits.
- **Input Sanitization:** Stripping control characters and validating UTF-8 boundaries before transmitting data to upstream APIs.

---

## H. Testing Strategy

- **Mock Resilience Testing:** Use the repository's approved test tooling to simulate Google API 429, 500, 503, and connection dropouts, verifying backoff timing and fallback paths. Any new test dependency needs prior approval.
- **Live Integration Testing:** Keep separate from deterministic tests; run live generation/embedding checks only after explicit approval to contact Gemini and incur any associated cost.
- **Cost Precision Tests:** Assert spend math against known token counts with zero rounding drift.
- **Self-Repair Tests:** Inject invalid JSON responses to verify that the repair turn successfully restores valid Pydantic model outputs.

---

## I. Definition of Done

1. Gateway resolves all enabled MVP aliases and validates only those aliases at startup.
2. Deterministic startup tests cover model-name normalization and missing/optional aliases; any live check is separately approved.
3. Deterministic tests cover registry, metering, cancellation, streaming interruption, bounded fallback, and negative cases.
4. Fallback graph traversal terminates for every enabled alias; retries do not duplicate non-idempotent tool effects.
5. Usage metering is correct; budget enforcement has approved caps and raises `BudgetExceededError` before exceeding them.
6. Gateway architecture documentation records the implemented operation and error contracts.

---

## J. Phase Exit Gate

- **Demonstrated Proof:** Deterministic tests cover fallback, startup validation, API operation contracts, and token/cost accounting. Any live-service evidence is separately approved and reported.
- **Dependent Phase Unlocked:** Phases 04 and 05 may begin after this phase and their other prerequisites pass. This phase cannot pass its exit gate while the budget-limit decision is open if budget enforcement remains required.
