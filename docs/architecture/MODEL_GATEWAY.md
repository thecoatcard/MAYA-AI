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
- Strict tool-call commitment enforcement to prevent duplicate side effects.
- Exact `Decimal` token and financial accounting with atomic integer micro-USD Redis budgeting.
- Multi-tenant workspace context cache management with transparent 404 recovery.
- Input prompt sanitization (UTF-8, control characters, size limits).
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
| **Streaming** | `generate_stream(alias, contents, ...)` | Async chunk streaming. Resilience allowed pre-chunk; mid-flight drops raise `StreamInterruptedError` with estimated usage. |
| **Structured Output** | `generate_structured(alias, contents, schema, ...)` | Parses output into Pydantic models. Triggers 1 structured self-repair turn if invalid before failing closed. |
| **Tool Calling** | `generate_with_tools(alias, contents, tools, ...)` | Emits structured function call requests. **The gateway never executes tools.** |
| **Embeddings** | `embed_content(alias, contents, ...)` | Generates embeddings, strictly asserting 768 dimensions. Fallback is opt-in only (`allow_fallback=False` by default). |
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
   - Never retried, never fallen back; fails immediately.
4. **Tool Call Commitment Rule:**
   - Once a model response emits tool calls, non-idempotent actions may be initiated. Any subsequent failure in that turn cannot retry or fall back; it raises `ToolCallCommitmentError`.
5. **Streaming Resilience:**
   - Pre-chunk failures may retry or fall back.
   - Mid-flight failures (after chunk 1 has been yielded) abort the stream, emit `StreamInterruptedError` with partial text and `is_usage_estimate=True`, and update the budget tracker.

---

## 5. Token Metering, Pricing, and Redis Budgeting

Financial spend is tracked precisely using `Decimal` arithmetic:
- **Cost Formulation:**
  $$\text{cost} = \frac{\text{uncached\_tokens} \times \text{rate}_{\text{input}} + \text{cached\_tokens} \times \text{rate}_{\text{cached}} + \text{output\_tokens} \times \text{rate}_{\text{output}}}{1,000,000}$$
- **Micro-USD Conversion:**
  To eliminate floating-point drift and prevent budget undercounting, USD costs are converted to integer micro-USD using `ROUND_UP`:
  $$\text{micro\_usd} = \text{int}\left((\text{cost\_usd} \times 1,000,000).\text{quantize}(1, \text{ROUND\_UP})\right)$$
- **Redis Storage:**
  Task budgets are stored in Redis under `maya:budget:{workspace_id}:{task_id}` via atomic pipelines:
  - `HINCRBY cost_micro_usd <micro_usd>`
  - `HINCRBY tool_turns <1>` (when `is_tool_turn=True`)
  - `HINCRBY total_tokens <tokens>`
  - `EXPIRE 86400` (24-hour retention)
- **Cancellation Protection:**
  Redis writes are wrapped in `asyncio.shield` so task cancellation does not drop billing updates.
- **Enforcement Modes:**
  - `log_only` (default): logs a warning `budget_alert_threshold_exceeded` when spend exceeds `$1.50` or tool turns exceed `10`.
  - `enforce`: raises `BudgetExceededError` if configured financial or tool-turn caps are breached.

---

## 6. Workspace Context Caching & 404 Recovery

Large prompts (e.g. system instructions, memory dumps) are cached in Redis:
- **Key Schema:** `maya:cache:{workspace_id}:{model_id}:{content_hash}`
- **Mandatory Tenant Scope:** `workspace_id` is required; requests lacking a workspace ID raise `ValueError`.
- **404 Recovery:**
  If the provider API returns a 404 error (indicating the server-side cache expired before the local Redis TTL), the cache manager automatically purges the local Redis entry and transparently retries uncached, ensuring zero user-facing downtime.

---

## 7. Input Sanitization & Output Self-Repair

- **Sanitization:** Validates UTF-8 encoding (lone surrogates rejected with `InputSanitizationError`), strips ASCII control characters (preserving `\n`, `\r`, `\t`), and enforces length limits.
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
  - Supports `--verify-free-tier` (stops cleanly on 429 quota exhaustion).
  - Explicit confirmation flag `--confirm-paid-calls-up-to-inr-100` required for any paid API operations (hard cap INR 100).
  - Never prints or exposes API keys.
