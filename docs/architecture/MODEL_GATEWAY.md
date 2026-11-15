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

| Operation             | Method Signature                                    | Primary Responsibilities                                                                                                                                                                                                                                                                                                                  |
| :-------------------- | :-------------------------------------------------- | :---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| **Text Generation**   | `generate_text(alias, contents, ...)`               | Unified text generation with multi-model fallback and usage metering.                                                                                                                                                                                                                                                                     |
| **Streaming**         | `generate_stream(alias, contents, ..., tools=None)` | Async chunk streaming (an async generator). A blocked first chunk is metered from its usage metadata or an input estimate when accounting succeeds. After the first chunk, failures raise `StreamInterruptedError` with estimated partial usage; cancellation or early close attempts the same accounting and closes the provider stream. |
| **Structured Output** | `generate_structured(alias, contents, schema, ...)` | Parses output into Pydantic models. Triggers 1 structured self-repair turn if invalid before failing closed.                                                                                                                                                                                                                              |
| **Tool Calling**      | `generate_with_tools(alias, contents, tools, ...)`  | Emits structured function call requests. **The gateway never executes tools.**                                                                                                                                                                                                                                                            |
| **Embeddings**        | `embed_content(alias, contents, ...)`               | Estimates and attempts to record usage for each received response before enforcing 768 dimensions or rejecting empty vectors. Fallback is opt-in only (`allow_fallback=False` by default).                                                                                                                                                |
| **Token Counting**    | `count_tokens(alias, contents)`                     | Computes token counts for prompt sizing and context budgeting.                                                                                                                                                                                                                                                                            |

---

## 3. Role Aliases & Fallback Resolution

Models are referenced by logical aliases defined in the canonical registry (`app.gateway.registry`):

| Alias            | Target Model ID                     | Transport   | Input / 1M | Output / 1M | Cached / 1M | Fallback Chain                                |
| :--------------- | :---------------------------------- | :---------- | :--------- | :---------- | :---------- | :-------------------------------------------- |
| `orchestrator`   | `gemini-3.8-flash`                  | REST        | \$0.75     | \$3.75      | \$0.075     | `orchestrator` -> `lite`                      |
| `heavy`          | `gemini-4-argon`                    | REST        | \$2.00     | \$10.00     | \$0.100     | `heavy` -> `orchestrator` -> `lite`           |
| `lite`           | `gemini-3.5-flash-lite`             | REST        | \$0.30     | \$2.50      | \$0.030     | `lite` -> `orchestrator`                      |
| `embed`          | `gemini-embedding-2`                | REST        | \$0.20     | -           | -           | `embed` (fallback opt-in to `embed-fallback`) |
| `embed-fallback` | `gemini-embedding-001`              | REST        | Unknown    | Unknown     | Unknown     | None (terminating)                            |
| `live`           | `gemini-3.8-live`                   | WebSocket   | \$1.50     | \$9.00      | -           | `live` -> `live-thinking` (Registry-only)     |
| `live-thinking`  | `gemini-3.8-live-extended-thinking` | WebSocket   | \$1.50     | \$9.00      | -           | `live-thinking` -> `live` (Registry-only)     |
| `decision`       | `gemini-decision-1`                 | Decision    | \$0.10     | -           | -           | None (Registry-only; see ADR-0029)            |
| `research`       | `deep-research-preview-04-2026`     | Managed     | Unknown    | Unknown     | Unknown     | None (Custom plan required)                   |
| `tts`            | `gemini-3.8-flash-tts`              | Unsupported | Unknown    | Unknown     | Unknown     | None (Post-MVP; disabled)                     |

### Fallback Rules

- Traversals are cycle-safe and strictly terminating.
- Enabled aliases must resolve to valid, non-disabled fallback models or startup validation fails.
- Non-REST aliases (`live`, `decision`, `research`, `tts`) raise `UnsupportedAliasTransportError` if called via REST gateway methods.
- Embeddings fallback (`embed` -> `embed-fallback`) is disabled by default because different embedding models produce vectors in non-comparable spaces.

---

## 4. Resilience and Error Handling

The resilience executor (`app.gateway.resilience`) manages errors according to HTTP status and semantics:

1. **429 (Rate Limit / Quota) and Timeouts:**
   - With one key (or none): retries up to 4 times per model before falling back to the secondary model. With several keys the call moves to another key immediately instead (section 11).
   - Exponential backoff: base delays of `[2.0, 4.0, 8.0, 16.0]` seconds with full uniform jitter (`uniform(0, delay)`).
2. **5xx Server Errors (500, 502, 503, 504):**
   - Immediate failover to the fallback model in the traversal chain without retrying on the failing model.
3. **4xx Client Errors (400, 401, 403) and Safety Blocks:**
   - Never retried, never fallen back; fails immediately. The SDK returns blocked prompts and responses instead of raising, so the gateway detects `prompt_feedback.block_reason` and blocking finish reasons. A first-chunk block raises `ContentBlockedError` after attempting to record its usage. A later block is reported as an interrupted stream, including its available usage.
4. **Classification uses types and status codes only.** `google.genai.errors.APIError.code` decides 408/429 (backoff), 5xx (failover) and everything else (fail). Transport failures from httpx, httpx2 or aiohttp are recognised by exception class name (timeouts back off; connection failures fail over). Error _text_ is never searched, because messages can contain identifiers that merely look like status codes. Unrecognised errors are non-retryable. `GatewayError` subclasses (budget, block, validation) are never retried.
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
- **404 handler:** `handle_provider_cache_404` purges a reference the provider no longer knows. It is not called by the gateway because provider cache references are not attached to requests.
- **Chat prompt prefix:** Chat selects one of two fixed system instructions; when task submission is available it also sends one fixed `submit_task` declaration. The request-specific message and history are sent as contents. A deterministic test checks the static system instruction and tool declaration across requests. This layout may support Gemini implicit caching; no provider cache hit is assumed or measured.
- **Explicit caching is not wired:** the installed SDK exposes `client.caches.create(config=...)` and a `cached_content_token_count` usage field, but its source does not state a minimum cache size. The chat prefix is short (the instructions are each under 2,000 characters), so available offline evidence does not establish that explicit caching is eligible. No cache setting is exposed and the gateway does not use `ContextCacheManager`. Provider minimums and storage pricing remain unverified; explicit caching requires that evidence before implementation.

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

## 8a. Function calling on the streaming path

`generate_stream(..., tools=[ToolDefinition, ...])` declares the functions to the model and surfaces each call on `GatewayStreamChunk.tool_calls` (`ToolCallRequest`: `call_id`, `tool_name`, `arguments`, opaque `thought_signature`) next to the text deltas. The gateway never executes a tool. The attempt is admitted with `for_tool_call=True` and recorded as a tool turn when a call was made, so the tool-turn cap applies; fallback, retry (until the first chunk), metering, sanitising and partial-usage accounting are unchanged, and `tools=None` changes nothing. To continue after a call the caller opens a new stream whose `contents` end with `tool_call_content(call)` (model turn, signature echoed) and `tool_response_content(call, result)` (user turn). Chat uses this for `submit_task` ([ADR-0016](../adr/0016-chat-function-calling-and-classifier.md)). Not tested against the real Gemini API.

---

## 9. Verification status and known limitations

- **Never tested against the real Gemini API.** Every test uses `FakeGeminiClient`, which returns real `google.genai.types` objects. Contract tests (`test_sdk_contract.py`) pin the fake to the real SDK's calling convention (the real `generate_content_stream` is a coroutine that must be awaited) and check client construction offline, but provider behavior (error shapes, streaming, limits, prices) is unverified until the owner runs the smoke tool.
- **Context cache:** reference store and 404 purge only; see section 6.
- **Structured output** relies on the prompt plus local validation and one repair turn; provider-side JSON mode (`response_schema`) is not used.
- **Prices:** figures come from the master plan and several are marked unverified there; overridden model ids are priced as unknown.
- **Budgets:** caps approved (O6, 2026-11-05); `log_only` stays the default until the gateway runs real tasks. The exit gate still waits for the owner-run live smoke check.
- **Ambient SDK configuration:** besides the key and Vertex mode (pinned in code), the SDK reads `GOOGLE_GENAI_HTTP_CLIENT` to pick its HTTP library; error classification works for all of them.

---

## 10. Live model in agents: evaluation (research only, 2026-11-09)

Question: can `gemini-3.8-live` (transport `websocket`, registry price $1.50 in / $9.00 out per 1M) run inside agents instead of REST models? Nothing is wired; the gateway still refuses websocket aliases for REST calls.

Findings from Google's pages (fetched 2026-11-09):

| Question | Result | Source |
| :-- | :-- | :-- |
| `google_search` tool | Supported for Gemini 3.8 Live; can be combined with function declarations in one session | https://aistudio.google.com/docs/live-api/tools |
| Function calling | Supported (sync and async) on 3.8 Live; async only on 3.8 Live Extended Thinking; the client must send tool responses manually | https://aistudio.google.com/docs/live-api/capabilities , .../tools |
| Text-only response modality | **Unverified.** The pages only show `AUDIO`, and state the native audio models "only support AUDIO response modality" (text via output transcription) | https://aistudio.google.com/docs/live-api/capabilities |
| Structured / JSON output | **Unverified.** Not mentioned in the Live pages | same |
| Session limit | Audio-only sessions 15 minutes, audio+video 2 minutes, extendable with session management | https://aistudio.google.com/docs/live-api/capabilities |
| Context window | 128k tokens for native audio output models, 32k for other Live models; model-specific figure for 3.8 Live not stated (the registry assumes 128k in, 64k out: unverified) | same |
| Not supported on 3.8 Live | Google Maps, code execution (page is self-contradictory), URL context | .../tools |
| Pricing (paid, standard, text) | Live (3.8 Live, 3.8 Live Extended Thinking, 3.1 Flash Live): $0.75 in / $4.50 out. Flash 3.8: $0.75 / $3.75 through 2026-12-31, then $1.50 / $7.50. Flash-Lite 3.5: $0.30 / $2.50. Audio on Live: $3.00 in / $12.00 out. Search grounding (Gemini 3+): 5,000 free requests per month, then $14 per 1,000 | https://aistudio.google.com/docs/pricing (last 3,573 characters not read) |

Verdict: **not suitable as a general agent model, do not wire it.** The Live API is a stateful WebSocket session built for audio, with a 15 minute audio cap, a smaller context, manual tool-response handling and no confirmed text-only or structured-output mode. Our agents need request/response text, validated JSON and retry/fallback, which the REST models give. Live stays for the voice path. If an agent ever needs live voice I/O, that is a separate design.

Registry discrepancy to resolve (owner): the registry prices `live` at $1.50 / $9.00, but the pricing page lists $0.75 / $4.50 for text on 3.8 Live. Verify against the console before changing `registry.py`, which this task did not touch. Also note Flash 3.8 ($0.75 / $3.75) doubles from 2027-01-01 per the same page.

---

## 11. Agent key affinity, instant failover and the agent model policy (2026-11-09)

Status: Implemented locally; tested with fake clients only. Never run against the real Gemini and never against a real multi-project key pool. Decision record: [ADR-0021](../adr/0021-agent-key-affinity-and-model-policy.md) (Proposed).

### 11.1 Role to key mapping (affinity)

Every agent and task model call passes an `affinity` (the agent role, or `chapter:{i}` for a long-document chapter) through `generate_text`, `generate_structured` and `generate_stream` to `KeyPool.select(affinity, exclude)`. The map is deterministic and stable across restarts, over the ORDERED list of effective keys (`MAYA_GEMINI_API_KEY` first, then `MAYA_GEMINI_API_KEYS` in order, duplicates and placeholders dropped), numbered `key#1..key#N`:

- Fixed roles, in this order: `planner, executor, research, document, email, pm, coding, longdoc`. Role number `i` (0-based) uses `key#((i mod N) + 1)`. Distinct roles get distinct keys first; they only wrap when there are more roles than keys.
- `chapter:{i}` uses `key#(((8 + i) mod N) + 1)`: chapters continue after the role keys in ring order, so parallel chapters spread over the rest of the pool.
- Any other affinity name uses a SHA-256 based stable slot (not Python's `hash`).
- If the mapped key is cooling or marked bad, the next healthy key in ring order serves the call; if every eligible key is cooling, the one recovering soonest is used. The mapping itself never changes: when the cooldown ends the call goes back to its own key.
- No affinity means plain round-robin, as before (chat, embeddings, token counts).

Example with 3 keys: planner `key#1`, executor `key#2`, research `key#3`, document `key#1`, email `key#2`, pm `key#3`, coding `key#1`, longdoc `key#2`; `chapter:1`, `chapter:2`, `chapter:3` use `key#3`, `key#1`, `key#2`.

Example with 17 keys (one per Google project): planner `key#1`, executor `key#2`, research `key#3`, document `key#4`, email `key#5`, pm `key#6`, coding `key#7`, longdoc (outline) `key#8`. `chapter:0..8` use `key#9..key#17` and `chapter:9` wraps to `key#1`. Chapter numbers in a document start at 1, so a 10-chapter run (`chapter:1..10`) uses `key#10..key#17`, then `key#1` and `key#2`: ten different projects in parallel. A role key is only shared with a chapter once a document has more than 8 chapters.

The pool size and the role to label map are logged once at startup (`gemini_key_pool_ready`). Only `key#N` labels are ever logged, put in metrics or stored; key text never is.

### 11.2 Instant failover rules

Where failover was not instant before this change (`app/gateway/resilience.py`, the old retry block, lines 207 to 232): after a 429, a timeout or a 5xx on one key the policy slept a jittered 0 to 2 s (then 0 to 4 s, and so on) BEFORE trying the next key, even when other keys were healthy. The loop also allowed `len(pool)` key switches (17 attempts with 17 keys), a hung attempt held its key until the SDK's own timeout, and `generate_structured` never reached its next alias when every key had failed (the error escaped the alias loop).

Now, with more than one key:

1. A retryable failure (429 or RESOURCE_EXHAUSTED, 5xx or UNAVAILABLE, 408, a timeout, a connection error) sends the very next attempt to a DIFFERENT key with no sleep. The failed key is marked cooling (a `Retry-After` hint is honoured; defaults 60 s for quota, 10 s for unavailable).
2. A call never retries a key it already tried. It tries at most `MAYA_GEMINI_MAX_KEY_ATTEMPTS` (default 4, 1 to 17) different keys per model: quota exhaustion on four distinct projects in a row means the model or region is the problem, not one key. Then it falls back to the configured fallback alias, if any, otherwise raises `GatewayFallbackExhaustedError` (class and status only, never provider text).
3. A backoff sleep happens only when no healthy eligible key is left (every remaining key is cooling), or when the pool has a single key (then the old rule applies: 4 retries with jittered exponential backoff).
4. Every attempt has a hard per-attempt timeout: `MAYA_GEMINI_REQUEST_TIMEOUT_SECONDS` (default 30, at most 300) for chat, embeddings, memory and voice, and `MAYA_AGENT_REQUEST_TIMEOUT_SECONDS` (default 150, at most 600) for calls that go through `model_policy.agent_call_kwargs` (agents and tasks: chapters, long specs and grounded synthesis legitimately take 30 to 120 s). An attempt that hangs is cancelled at its cap and fails over then, not after a longer total deadline; fast failures (429, 503, reset) still switch at once whatever the cap. For a stream the cap covers opening the stream up to the first chunk. Both layers honour the per-call cap: the asyncio cap (`attempt_timeout_seconds` on `ResiliencePolicy.execute`) and the SDK's HTTP timeout, set per request through `GenerateContentConfig(http_options=HttpOptions(timeout=ms))` (the installed google-genai merges it over the client options, so retries stay disabled; no second set of clients is needed). Worst case for one agent call: `MAYA_GEMINI_MAX_KEY_ATTEMPTS` x the agent timeout (4 x 150 = 600 s per model, a second 600 s on the fallback alias if one is set), and `generate_structured` can make a second (repair) call. Step budgets are sized to match: context build 300 s, outline and chapter steps 600 s, research 390 s plus 30 s (its stage caps are 90, 120 and 180 s), document agent unchanged (it makes no model call).
5. A dead key: 401, 403, or 400 with `API_KEY_INVALID` / "API key not valid" marks that key bad for the process lifetime (logged once with its label), the call fails over at once, and the pool keeps working. If every key is bad the next call raises a clear configuration error. A plain 400 (bad request) is still never retried and never blames the key.
6. Streaming switches keys only before the first chunk (unchanged). Model calls have no side effects, so a switch cannot duplicate one. A switch does not consume the call's retry budget.
7. Selection is O(pool) under one lock; 8 concurrent callers are tested.

Metrics (labels `key#N`, alias and reason only): `maya_gemini_key_calls_total{key,alias,outcome}`, `maya_gemini_key_failovers_total{key,reason}` (reason: quota, unavailable, timeout, connection, invalid_key), `maya_gemini_key_cooldowns_total{key,reason}`, the existing `maya_gemini_key_pool_size` and `maya_gemini_key_pool_cooling`, and the new gauge `maya_gemini_key_pool_bad`. `GatewayResponse.key_label` carries the label of the key that served a text call.

Not covered: `generate_with_tools` still uses one client without key failover (no agent uses it).

### 11.4 Key faults versus model-wide overload, last-resort model (2026-11-13)

A 503 or a timeout on a busy model fails on every key, so burning more keys only costs time. Rules (`ResiliencePolicy.execute`, all calls, chat and tasks alike):

| Failure | What happens |
| :-- | :-- |
| 429 / RESOURCE_EXHAUSTED on one key | That key cools for the (model, tool) bucket (60 s or the provider's retry delay); the next key is tried at once. Up to `MAYA_GEMINI_MAX_QUOTA_KEY_ATTEMPTS` (default 8, at most 64, never below `MAYA_GEMINI_MAX_KEY_ATTEMPTS`) different keys. |
| Every key cooling for the model | No sleep: the chain moves to the next model (the last model of a chain still waits and retries). |
| 503 / 5xx / timeout / connection error | Counted per bucket. Two such failures on DIFFERENT keys inside 30 s mean the model is overloaded: the remaining keys are skipped, the chain moves to the next model at once, and the bucket is shared-cooled for 15 s so concurrent calls skip it first; it is tried again after that and always when it is the last model left. `MAYA_GEMINI_MAX_KEY_ATTEMPTS` (default 4) still caps the keys per model. A success clears the state. |
| 401 / 403 / key invalid | Key marked bad, next key (unchanged). |
| Interactive chat | Same rules plus the first-chunk cap (20 s) and total deadline (75 s) (unchanged). |
| Structured output | `generate_structured` walks the same candidate chain alias by alias. |
| Grounded search | Same rules on the `(model, grounding)` bucket; the `search` alias has no further fallback. |
| Embeddings, `count_tokens` | No last-resort model (different vector spaces / model-specific counts). |
| Live voice connect (`gemini-3.8-live`) | Tries the live chain (`live`, `live-thinking`); a quota failure rotates through up to 8 other keys per model; the user sees the plain overload text. |
| Image / TTS | Image: own key rotation (`services/image_gen.py`). TTS alias is disabled (post-MVP). |

**Last-resort model.** Plain generation chains (`lite`, `orchestrator`, `heavy`; chat, agents, structured output) end at the `search` alias (`gemini-2.5-flash`, follows `GEMINI_MODEL_OVERRIDES`): `heavy > orchestrator > lite > search`. It is unpriced (`price_unknown`, cost `None`), so cost accounting reports it as unknown, never zero. Calls that ask for thought summaries do not fall back to it (not guaranteed there). `registry.candidate_chain(...)` builds the list, de-duplicated (cycle safe).

**Startup verification.** With `MAYA_GEMINI_VERIFY_MODELS_ON_STARTUP=true` a missing model is logged (`gateway_startup_model_unavailable`) and marked unavailable: fallback chains skip it. It no longer stops startup (a failed model-list call still does).

**Final error.** When everything failed, chat, task step failures and the live voice frame show: "Gemini is overloaded right now. MAYA tried N models and M keys; please try again in a minute." (`GatewayFallbackExhaustedError.user_message`; no key labels, no provider text). Each failover decision logs one INFO line `gateway_failover reason=<quota|unavailable|timeout|connection> alias= model= attempt= next=<next_key|model:ALIAS>`. Not verified against the live Gemini API.

### 11.3 Agent model policy

`MAYA_AGENT_MODEL_ALIAS` (default `lite`, that is `gemini-3.5-flash-lite`) is the single model alias of every agent and task call: the planner and its repair call, research (plan, grounded search, synthesis), the context builder and the document and email structured calls (including repair and re-generation), the plain-text executor, and the long-document outline, chapters and expansion. They all take it from `app/agents/model_policy.py`; no agent or task file names an alias any more. Allowed values are the enabled REST text aliases `lite`, `orchestrator` and `heavy`. `live` and `live-thinking` are refused at settings validation with "live is a stateful audio WebSocket API, use it for voice only".

`MAYA_AGENT_FALLBACK_ALIAS` is empty by default: an agent call then never falls back to another model, it fails honestly after its key attempts. Set it (for example `orchestrator`) and a call that exhausted its keys on the agent alias is tried once more, with fresh key attempts, on exactly that alias (not on the registry chain). Chat keeps its own alias and its registry fallback chain; embeddings are unchanged.

Structured output on `lite`: `generate_structured` instructs the model through the prompt and validates the JSON in the gateway (one self-repair turn); it sends no provider `response_schema`, so `lite` needs no different parameters. The `lite` registry entry (1 048 576 input, 65 536 output tokens, $0.30 in / $2.50 out / $0.03 cached per 1M) matches the Google pricing in section 10. Long chapters and the outline pass an explicit `max_output_tokens` of 65 536 (the registry output limit of every agent-capable model). It is a ceiling, not a target: billing is per token produced, and a chapter is no longer cut off by a smaller default.

Why `live` is not used by agents: section 10 (a stateful audio WebSocket API with a 15 minute audio cap, no confirmed text-only or structured-output mode). Voice keeps it.

Not measured: whether `lite` writes synthesis and 5-page chapters as well as `heavy` or `orchestrator` did (the expand/trim round after the real print is the safety net for length; nothing checks quality), and how 17 real keys from different projects behave under quota. Both need a run with real keys.

## 12. Grounded search and spend (Task FA, 2026-11-09)

Grounded web search is an ordinary gateway call (`enable_google_search=True`, agent alias, affinity `research`), used by the research agent and by the MCP `web_search` tool through `GoogleSearchConnector`, which holds no client. A 503 on one key fails over to the next key inside the same call with no sleep; 429, 408 and 503 are all treated as retryable "rate limited" by the task worker. Real priced usage increments `maya_spend_usd_total{workspace_id}` (an unpriced model adds nothing, never zero; a failed attempt's reservation is not counted), `ModelGateway.budget_tracker` exposes the per-task counters, and the worker mirrors them into `tasks.spent_usd`. API keys are normalised (`core/api_key_text.py`: whitespace, matching quotes, trailing `,;`) before de-duplication, so one key written in two shapes is one pool entry; the gauge `maya_gemini_key_pool_size` is the de-duplicated count (a larger number than the owner's key count means more distinct keys were configured, plus any test keys a harness added).

## 23. Stream continuation and MAX_TOKENS (2026-11-13)

After the first chunk the gateway cannot fail over, so the chat service recovers: on `StreamInterruptedError` or a 45 s stall it opens a new stream (another key or model) whose contents are the previous contents plus the text so far as a model turn and a continue instruction; deltas are stitched to the same message with a repeated overlap trimmed (`app/services/stream_continuation.py`). `finish_reason=MAX_TOKENS` continues the same way. Caps: 3 interruption recoveries and 8 MAX_TOKENS continuations per reply. The output budget per call is the orchestrator's `context_output_limit` (65,536) via `output_budget`.

Limits that remain on purpose: the 600 s reply deadline (`MAYA_CHAT_REPLY_DEADLINE_SECONDS`); the continuation caps above; thought summaries (THOUGHT_MAX); untrusted-text size caps (web pages, files, mail bodies, tool results); 2000-character Notion `rich_text`; email report HTML 40,000 characters; task-result chat message 14,000 characters (`task_result.py`) and its 2,500-token summary call; voice (live) limits; decision-model input clips.
