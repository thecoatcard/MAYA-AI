# Runbook: Model Deprecation and Alias Churn Management

- **Owner:** Model Gateway & Platform Architecture Team
- **Last verified:** 2026-11-07 by Principal Software Architect
- **Severity guide:** Sev-2 when Google deprecates a pinned model ID; Sev-3 for routine planned alias updates.

---

## Symptoms

1. **Startup Alert:** Backend container startup fails during `verify_gateway_at_startup` with:
   `ModelVerificationError: Pinned model ID 'gemini-X' for alias 'orchestrator' not found in models.list`.
2. **Log Signatures:** `google.genai.errors.NotFound: 404 Model not found or deprecated`.
3. **Provider Warning:** Google Cloud deprecation notice regarding retirement of an existing Gemini model snapshot.

---

## Impact

- If a model is deprecated without alias redirection, requests routed to that alias will throw `ModelNotFoundError` or failover to secondary models.
- System availability could degrade if fallback chains are not configured.

---

## Quick checks

1. Check active model alias mappings and startup verification logs:
   ```bash
   docker logs maya-backend | grep -E "gateway_verified|models.list"
   ```
2. Query Google GenAI `models.list` directly via CLI helper:
   ```bash
   python -m app.tools.gemini_smoke --list-models
   ```
3. Inspect `MAYA_GEMINI_MODEL_OVERRIDES` in the active environment.

---

## Diagnosis

| Observation | Likely cause | Next step |
| :---------- | :----------- | :-------- |
| 404 Model Not Found on specific alias | Upstream model version retired by Google | Update alias override in environment configuration |
| Startup check fails with `ModelVerificationError` | Pinned ID in `app.gateway.registry` was deprecated | Apply zero-downtime override via `MAYA_GEMINI_MODEL_OVERRIDES` |
| Increased latency on alias fallback | Primary model failed over to heavier secondary | Repin primary alias to newer recommended flash model |

---

## Mitigation

### Step 1: Query Supported Replacement Model IDs (Read-only)
Execute the verification smoke tool to discover available replacements:
```bash
python -m app.tools.gemini_smoke
```
Identify the verified successor model ID (e.g. `gemini-3.8-flash` replacing an older snapshot).

### Step 2: Apply Zero-Downtime Environment Override (Reversible)
Update the model alias mapping without changing application source code (Decision D13):
```bash
# Example: Point 'orchestrator' alias to updated model ID
export MAYA_GEMINI_MODEL_OVERRIDES='{"orchestrator": "gemini-3.8-flash"}'

# Apply to production compose
docker compose -f docker/docker-compose.prod.yml up -d --no-deps backend
```

### Step 3: Verify Startup Gate
Inspect backend container logs to confirm `verify_gateway_at_startup` succeeded:
```bash
docker logs --tail 50 maya-backend | grep "gateway_verified"
# Expected: "gateway_verified: all aliases confirmed available in models.list"
```

---

## Rollback

To revert to the default pinned registry defaults:
```bash
unset MAYA_GEMINI_MODEL_OVERRIDES
docker compose -f docker/docker-compose.prod.yml up -d --no-deps backend
```

---

## Escalation

- Escalate to Lead AI Architect if Google deprecates an entire model category (e.g., changes to Live audio protocol).

---

## After the incident

1. Submit a pull request updating `DEFAULT_ALIASES` in [`backend/app/gateway/registry.py`](../../../backend/app/gateway/registry.py).
2. Update pricing constants in [`backend/app/gateway/cost_tracker.py`](../../../backend/app/gateway/cost_tracker.py) if the new model carries different rates.
3. Update [`docs/guides/CONFIGURATION.md`](../../guides/CONFIGURATION.md).
