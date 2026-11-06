# Model gateway architecture

- **Status:** Implemented locally (Phase 03)
- **Package:** `backend/app/gateway/`
- **Related ADR:** [ADR-0007](../adr/0007-gemini-model-gateway-architecture.md)
- **Configuration Reference:** [CONFIGURATION.md](../guides/CONFIGURATION.md)

## 1. Overview and Responsibility

The Model Gateway provides a centralized, typed interface for all Large Language Model interactions across MAYA AI. It decouples high-level agent reasoning (Phases 04, 05, 09, 11) from specific provider SDKs and concrete model versions.

Key responsibilities:
- Unified execution across 6 typed operations.
- Dynamic role alias resolution with terminating cycle-safe fallback traversal.
- Exponential backoff with full jitter on 429/timeouts and immediate failover on 5xx.
- Tool-call commitment: the gateway never executes tools and returns tool requests only after a complete response and its accounting step.
- `Decimal` pricing arithmetic (thinking tokens billed as output, tool-use prompt tokens as input), atomic integer micro-USD Redis increments and a distributed per-task admission lease that prevents concurrent attempts from passing against stale counters. This does not constitute a provider-enforced hard spend cap.
- A workspace-scoped Redis store for provider context-cache references with a 404 purge handler (provider cache creation and attachment are not implemented yet; see sections 6 and 9).
- Input prompt sanitization (UTF-8, lone surrogates, control characters; an optional size limit that callers must opt into).
- Structured output validation with single-turn self-repair.

```
                              +---------------------------+
                              | Application Layers (P04+) |
                              +---------------------------+
                                            |
                                            v
                              +---------------------------+
                              |       ModelGateway        |
                              +---------------------------+
                                  |         |         |
                  +---------------+         |         +---------------+
                  v                         v                         v
        +-------------------+     +-------------------+     +-------------------+
        | Input Sanitizer   |     | Model Registry    |     | Resilience Policy |
        | & Output Validator|     | & Fallback Graph  |     | & Tool Commitment |
        +-------------------+     +-------------------+     +-------------------+
                                            |
                  +-------------------------+-------------------------+
                  v                                                   v
        +-------------------+                               +-------------------+
        | BudgetTracker     |                               | ContextCache      |
        | (Redis Micro-USD) |                               | (Redis Namespace) |
        +-------------------+                               +-------------------+
                  |                                                   |
                  +-------------------------+-------------------------+
                                            v
                              +---------------------------+
                              |    Client Adapter (SDK)   |
                              +---------------------------+
```

---

## 2. API Operations Contract

The gateway exposes 6 primary asynchronous methods:

| Operation | Method Signature | Primary Responsibilities |
| :--- | :--- | :--- |
| **Text Generation** | `generate_text(alias, contents, ...)` | Unified text generation with multi-model fallback and usage metering. |
| **Streaming** | `generate_stream(alias, contents, ...)` | Async chunk streaming (an async generator). A blocked first chunk is metered from its usage metadata or an input estimate when accounting succeeds. After the first chunk, failures raise `StreamInterruptedError` with estimated partial usage; cancellation or early close attempts the same accounting and closes the provider stream. |
| **Structured Output** | `generate_structured(alias, contents, schema, ...)` | Parses output into Pydantic models. Triggers 1 structured self-repair turn if invalid before failing closed. |
| **Tool Calling** | `generate_with_tools(alias, contents, tools, ...)` | Emits structured function call requests. **The gateway never executes tools.** |
| **Embeddings** | `embed_content(alias, contents, ...)` | Estimates and attempts to record usage for each received response before enforcing 768 dimensions or rejecting empty vectors. Fallback is opt-in only (`allow_fallback=False` by default). |
| **Token Counting** | `count_tokens(alias, contents)` | Computes token counts for prompt sizing and context budgeting. |

---

## 3. Role Aliases & Fallback Resolution

Models are referenced by logical aliases defined in the canonical registry (`app.gateway.registry`):

| Alias | Target Model ID | Transport | Input / 1M | Output / 1M | Cached / 1M | Fallback Chain |
| :--- | :--- | :--- | :--- | :--- | :--- | :--- |
| `orchestrator` | `gemini-3.8-flash` | REST | \$0.75 | \$3.75 | \$0.075 | `orchestrator` -> `lite` |
| `heavy` | `gemini-4-argon` | REST | \$2.00 | \$10.00 | \$0.100 | `heavy` -> `orchestrator` -> `lite` |
| `lite` | `gemini-3.5-flash-lite` | REST | \$0.30 | \$2.50 | \$0.030 | `lite` -> `orchestrator` |
| `embed` | `gemini-embedding-2` | REST | \$0.20 | - | - | `embed` (fallback opt-in to `embed-fallback`) |
| `embed-fallback` | `gemini-embedding-001` | REST | Unknown | Unknown | Unknown | None (terminating) |
| `live` | `gemini-3.8-live` | WebSocket | \$1.50 | \$9.00 | - | `live` -> `live-thinking` (Registry-only) |
| `live-thinking` | `gemini-3.8-live-extended-thinking`| WebSocket | \$1.50 | \$9.00 | - | `live-thinking` -> `live` (Registry-only) |
| `research` | `deep-research-preview-04-2026` | Managed | Unknown | Unknown | Unknown | None (Custom plan required) |
| `tts` | `gemini-3.8-flash-tts` | Unsupported | Unknown | Unknown | Unknown | None (Post-MVP; disabled) |

### Fallback Rules
- Traversals are cycle-safe and strictly terminating.
- Enabled aliases must resolve to valid, non-disabled fallback models or startup validation fails.
- Non-REST aliases (`live`, `research`, `tts`) raise `UnsupportedAliasTransportError` if called via REST gateway methods.
- Embeddings fallback (`embed` -> `embed-fallback`) is disabled by default because different embedding models produce vectors in non-comparable spaces.

---

## 4. Resilience and Error Handling

The resilience executor (`app.gateway.resilience`) manages errors according to HTTP status and semantics:

1. **429 (Rate Limit / Quota) and Timeouts:**
   - Retries up to 4 times per model before falling back to the secondary model.
   - Exponential backoff: base delays of `[2.0, 4.0, 8.0, 16.0]` seconds with full uniform jitter (`uniform(0, delay)`).
2. **5xx Server Errors (500, 502, 503, 504):**
   - Immediate failover to the fallback model in the traversal chain without retrying on the failing model.
3. **4xx Client Errors (400, 401, 403) and Safety Blocks:**
   - Never retried, never fallen back; fails immediately. The SDK returns blocked prompts and responses instead of raising, so the gateway detects `prompt_feedback.block_reason` and blocking finish reasons. A first-chunk block raises `ContentBlockedError` after attempting to record its usage. A later block is reported as an interrupted stream, including its available usage.
4. **Classification uses types and status codes only.** `google.genai.errors.APIError.code` decides 408/429 (backoff), 5xx (failover) and everything else (fail). Transport failures from httpx, httpx2 or aiohttp are recognised by exception class name (timeouts back off; connection failures fail over). Error *text* is never searched, because messages can contain identifiers that merely look like status codes. Unrecognised errors are non-retryable. `GatewayError` subclasses (budget, block, validation) are never retried.
5. **Tool Call Commitment Rule:**
   - `ResiliencePolicy.execute(is_tool_call_committed=True)` raises `ToolCallCommitmentError` instead of retrying or falling back. `generate_with_tools` is non-streaming: tool requests are not returned until the response's accounting step completes. The flag exists for a future streaming-with-tools operation. An accounting error can still cause an additional provider attempt before any requests are returned; see section 5.
6. **Streaming Resilience:**
   - A status error while opening the stream, or an error before the first chunk arrives, retries or falls back; the abandoned stream is closed.
   - Failures after the first chunk abort the stream and raise `StreamInterruptedError` with partial text and `is_usage_estimate=True`; accounting is attempted against the budget. The error message carries the exception class only, never provider text.
7. **No SDK-level retries.** The client is built with `HttpRetryOptions(attempts=1)`; `retry_options=None` would leave the SDK's own implicit retry active. It is also built with `vertexai=False`, so an ambient `GOOGLE_GENAI_USE_VERTEXAI=true` cannot switch it to Vertex AI.
8. **Log hygiene.** Logs and gateway exception messages carry the exception class and HTTP status only. Prompts, model output, validation values, cache names and raw provider error text are never logged.

---

## 5. Token Metering, Pricing, and Redis Budgeting

Calculated costs use `Decimal` arithmetic; their accuracy still depends on the usage and rates supplied:
- **Cost Formulation (per 1M-token rates):**
  - input tokens = (`prompt_token_count` - `cached_content_token_count`) + `tool_use_prompt_token_count`, at the input rate;
  - cached tokens at the cached rate;
  - output tokens = `candidates_token_count` + `thoughts_token_count`, at the output rate (thinking is billed as output).
- **Unknown cost is `None`, never `0`.** If the price is unknown (`research`, `tts`, `embed-fallback`, any overridden model id) or a rate needed for the reported tokens is missing, `estimated_cost_usd` is `None` and `is_estimated_cost_complete` is `False`.
- **Estimated usage.** When the provider reports no usage (interrupted streams, a response without usage metadata, embeddings), tokens are estimated at about 4 characters per token and flagged `is_usage_estimate=True`.
- **Micro-USD Conversion:**
  USD costs are converted to integer micro-USD using `ROUND_UP`. This avoids rounding down the calculated amount; it cannot correct estimated token counts, missing charges or unverified prices:
  $$\text{micro\_usd} = \text{int}\left((\text{cost\_usd} \times 1,000,000).\text{quantize}(1, \text{ROUND\_UP})\right)$$
- **Redis Storage:**
  Task budgets are stored in Redis under `maya:budget:{workspace_id}:{task_id}` via atomic pipelines:
  - `HINCRBY cost_micro_usd <micro_usd>`
  - `HINCRBY tool_turns <1>` (when `is_tool_turn=True`)
  - `HINCRBY total_tokens <tokens>`
  - `HINCRBY unpriced_calls <1>` when the cost is unknown (the money counter excludes that cost; estimates and unverified rates mean the counter is not necessarily a lower bound on the actual bill)
  - `EXPIRE 86400` (24-hour retention)
- **Cancellation Protection:**
  Redis writes are shielded from caller cancellation on a best-effort basis. For a blocked or interrupted stream, an accounting failure is logged by exception type without replacing the original block, interruption, cancellation or close. Shielding cannot guarantee persistence if Redis fails, a write's outcome is unknown, or the process exits.
- **Enforcement Modes:**
  - `log_only` (default): logs a warning `budget_alert_threshold_exceeded` when spend exceeds `$1.50` or tool turns exceed `10`.
  - `enforce`: with a tracker, an atomic Redis operation checks the recorded cost and tool-turn caps and claims an exclusive per-task admission lease before each task-scoped provider attempt. Concurrent attempts for the same task are refused while another attempt is being accounted. For a known-price model, `record_usage` still raises if one response crosses the cap. The owner approved the caps on 2026-11-05 (O6: $1.50 per task, 10 tool turns); enforcement is opt-in (`MAYA_BUDGET_ENFORCEMENT_MODE=enforce`) and not active by default.
  - These checks apply only when `task_id` is supplied; calls without a task, including `count_tokens`, are not task-budgeted. The tracker itself is optional, so a gateway without one cannot check prior usage or record counters.
- **Unpriced enforcement:** In `enforce` mode with a task-scoped tracker and a configured monetary cap, calls are rejected before reaching the provider if the applicable model price is unknown or incomplete. This includes unknown model overrides and missing applicable rates. In `log_only` mode and calls without a task scope, unknown prices remain recorded in `unpriced_calls` without being added to money totals.
- **Limitations:** Redis admission checks and claims a per-task lease atomically; concurrent gateway attempts for one task do not pass against stale counters. The lease is released after accounting and expires with the budget retention period as crash recovery. A single known-price response can still cross the financial cap, provider failures may incur unreported charges, and the 24-hour key expiry can erase counters during a long or resumed task. Estimated tokens and unverified rates are not reliable upper bounds. This is not a provider-enforced hard spend cap.
- **Accounting failures:** Redis admission errors fail closed before a provider attempt. Response accounting is performed before the lease is released, including first-chunk blocked streams and malformed embedding responses; accounting errors do not trigger another provider attempt. Partial-stream accounting remains best-effort so an accounting error does not replace the original stream error, cancellation, or close. Deferred shielded failures are observed and logged by exception type only; an uncertain Redis write has no idempotent recovery.

---

## 6. Workspace Context Cache References (partially implemented)

`ContextCacheManager` stores the provider-assigned cache name for a workspace in Redis:
- **Key Schema:** `maya:cache:{workspace_id}:{model_id}:{content_hash}`
- **Mandatory Tenant Scope:** `workspace_id` is required; calls without one raise `ValueError`, so a reference can never be shared across workspaces.
- **404 handler:** `handle_provider_cache_404` purges a reference the provider no longer knows.
- **Not implemented:** creating provider caches (`caches.create`), attaching a cache to `generate_*` calls, retrying uncached after a provider 404, and metering cache storage cost. `ModelGateway` does not use `ContextCacheManager` yet, so "eligible repeated prompts reuse a cache" is not met. These need live verification of provider limits (minimum cache size, storage pricing) and stay open until a paid-call cap is approved.

---

## 7. Input Sanitization & Output Self-Repair

- **Sanitization:** Validates UTF-8 encoding (lone surrogates rejected with `InputSanitizationError`) and strips ASCII control characters (preserving `\n`, `\r`, `\t`) in strings, including nested lists and dicts. `max_chars` is supported but the gateway does not set one, so the provider's own limits apply. Typed SDK objects (`types.Content`, `types.Part`) pass through unsanitized; pass strings or plain dicts.
- **Structured Output Self-Repair:**
  If `generate_structured` receives invalid JSON or fails Pydantic schema validation:
  1. It strips markdown code fences (` ```json `).
  2. Generates a corrective repair prompt containing the raw text, validation error, and expected JSON schema.
  3. Executes exactly 1 repair turn.
  4. If the repair turn fails validation, it raises `StructuredOutputError`. All repair tokens are counted toward the task budget.

---

## 8. Diagnostic Tools and Safety Gates

- **Smoke Tool (`backend/app/tools/gemini_smoke.py`):**
  - Strictly **OWNER-RUN ONLY**. Never run by autonomous agents.
  - Supports `--verify-free-tier` (the owner asserts the key is free-tier; the tool cannot check this; stops cleanly on 429 quota exhaustion).
  - Calls the SDK directly, not `ModelGateway`: it checks the key, model id and connectivity, and does not exercise the gateway's streaming, embedding or resilience paths.
  - Explicit confirmation flag `--confirm-paid-calls-up-to-inr-100` required for any paid API operations (hard cap INR 100).
  - Never prints or exposes API keys.

---

## 9. Verification status and known limitations

- **Never tested against the real Gemini API.** Every test uses `FakeGeminiClient`, which returns real `google.genai.types` objects. Contract tests (`test_sdk_contract.py`) pin the fake to the real SDK's calling convention (the real `generate_content_stream` is a coroutine that must be awaited) and check client construction offline, but provider behavior (error shapes, streaming, limits, prices) is unverified until the owner runs the smoke tool.
- **Context cache:** reference store and 404 purge only; see section 6.
- **Structured output** relies on the prompt plus local validation and one repair turn; provider-side JSON mode (`response_schema`) is not used.
- **Prices:** figures come from the master plan and several are marked unverified there; overridden model ids are priced as unknown.
- **Budgets:** caps approved (O6, 2026-11-05); `log_only` stays the default until the gateway runs real tasks. The exit gate still waits for the owner-run live smoke check.
- **Ambient SDK configuration:** besides the key and Vertex mode (pinned in code), the SDK reads `GOOGLE_GENAI_HTTP_CLIENT` to pick its HTTP library; error classification works for all of them.
