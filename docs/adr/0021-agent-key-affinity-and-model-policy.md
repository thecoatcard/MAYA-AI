# ADR-0021: Per-agent API key affinity, instant key failover and one agent model

- **Status:** Proposed (owner request, 2026-11-09)
- **Date:** 2026-11-09
- **Deciders:** Repository owner (approval pending)
- **Related:** [ADR-0020](0020-long-document-pipeline.md), [Model gateway](../architecture/MODEL_GATEWAY.md#11-agent-key-affinity-instant-failover-and-the-agent-model-policy-2026-11-09), [Agent request flow](../architecture/AGENT_REQUEST_FLOW.md#models-and-api-keys-all-agents)

## Context

The owner asked: "let each agent use different apis if we have in pool and when any request fails it quickly changes the api and switches to that, and for all agents use gemini-3.5-flash-lite or gemini-3.8-live". The pool holds 17 keys from different Google projects. Three problems: (1) key choice was plain round-robin, so one role's load and quota were mixed with every other's; (2) failover was not instant: after a 429, a timeout or a 5xx the policy slept 0 to 2 s (growing) before trying the next key even when other keys were healthy, allowed up to `len(pool)` key switches (17 attempts), let a hung attempt hold a key until the SDK timeout, and `generate_structured` never reached its next alias when all keys failed; (3) agent code hard-coded `orchestrator` and `heavy`.

## Decision

1. **Affinity.** A call may carry an `affinity` (the agent role or `chapter:{i}`). A fixed role order maps role `i` to key slot `i mod N`; chapters continue after the role keys (`(8 + i) mod N`); unknown names use a SHA-256 slot. A cooling or bad key is skipped in ring order and the mapping is restored when it recovers. No affinity keeps round-robin.
2. **Instant failover, bounded.** With several keys, a retryable failure or a dead key sends the next attempt to a different key with no sleep; a key is never retried within one call; at most `MAYA_GEMINI_MAX_KEY_ATTEMPTS` (default 4, 1 to 17) different keys are tried per model. Sleeps remain only when every eligible key is cooling or there is a single key. Every attempt has a hard timeout (`MAYA_GEMINI_REQUEST_TIMEOUT_SECONDS`). 401, 403 and 400 `API_KEY_INVALID` mark a key bad for the process lifetime (logged once, labelled) and the pool keeps working.
3. **One agent model.** `MAYA_AGENT_MODEL_ALIAS` (default `lite`; `lite`, `orchestrator` or `heavy`) is the alias of all agent and task calls, through `app/agents/model_policy.py`. `MAYA_AGENT_FALLBACK_ALIAS` (default empty) is the only fallback an agent call has. Chat and embeddings are unchanged.
4. **`gemini-3.8-live` is not used by agents.** It is a stateful audio WebSocket API with a 15 minute audio cap and no confirmed text-only or structured-output mode (MODEL_GATEWAY section 10). The setting refuses it at startup with a clear message. Voice keeps it.
5. **Observability.** Per-key counters (calls, failovers by reason, cooldowns by reason) and a bad-key gauge; `key#N` labels only; the startup log shows the pool size and the role to label map.

## Consequences

- Each role consumes the quota of its own project first, and a 10-chapter document uses ten projects in parallel; one project's exhaustion no longer stalls the others.
- Failover latency on a 429 or 5xx drops from seconds to one extra request. A hung attempt now costs at most the request timeout.
- Quality of `lite` for synthesis and chapters is unmeasured; if it is too weak, set `MAYA_AGENT_MODEL_ALIAS=orchestrator` (no code change) or set a fallback alias.
- Agent and task calls have their own per-attempt cap, `MAYA_AGENT_REQUEST_TIMEOUT_SECONDS` (default 150, at most 600), applied at both the asyncio and the SDK HTTP layer per request; chat keeps the 30 s cap. Worst case for one agent call is 4 key attempts x 150 s = 600 s per model; fast failures still switch instantly. Step budgets follow: context build 300 s, outline and chapter steps 600 s, research 420 s.
- Role keys are shared once a document has more than 8 chapters, or when the pool has fewer than 8 keys.
- No schema change, no migration, no new dependency.

## Alternatives

Hash-based affinity (uneven: two roles can share a key while others are idle); per-call random keys (no isolation of load); sleeping with jitter on every failure (the old behaviour: slow); trying every key in the pool per call (17 attempts to learn that the model is down); using `gemini-3.8-live` through the REST gateway (not possible: websocket transport, audio modality).

## Addendum 2026-11-09 (Task FA): grounded search through the gateway

Context: a real-Gemini run showed research results without sources, a whole research step failing on one transient 503, thin long documents, untracked agent spend, a Windows path-length failure, and a deck missing an item. Decisions: (1) `web_search` and the research agent use the gateway (key pool, failover, retries, metrics, cost); the connector is an adapter without a client; (2) 429/408/503 are one retryable class with the status preserved in logs; (3) page requests also need a words floor (0.65 x pages x 450) and a chapter under 75% of its word target is expanded once; (4) spend is counted in `maya_spend_usd_total` and copied to `tasks.spent_usd`; (5) the planner is capability-aware and refuses (`unsupported`, `needs_clarification`) instead of researching or inventing content, with no task row written. No schema change, migration or dependency. `MAYA_AGENT_SEARCH_ALIAS` was deliberately not added (no evidence that `lite` cannot ground).
