# Runbook: Rate-Limit Quota Storms and Provider Throttling

- **Owner:** SRE & API Platform Team
- **Last verified:** 2026-11-07 by Production SRE Lead
- **Severity guide:** Sev-2 when provider 429 error rate exceeds 2% across 5 minutes; Sev-1 if task queues stall completely.

---

## Symptoms

1. **Dashboard Alert:** `ModelQuotaExhaustion` firing in Grafana.
2. **HTTP Status Code:** Backend receiving HTTP 429 `RESOURCE_EXHAUSTED` from Google GenAI API or returning HTTP 429 to clients.
3. **Queue Build-Up:** Celery/ARQ task queue depth increasing rapidly while worker throughput decreases.
4. **Log Signatures:** `google.genai.errors.ClientError: 429 Resource exhausted: Quota exceeded for quota metric 'GenerateContent requests'`.

---

## Impact

- Asynchronous subtasks experience extended queuing delay.
- Non-critical background research tasks stall.
- Real-time chat may experience throttling if token budgets are consumed by rogue loops.

---

## Quick checks

1. Check current token consumption velocity by model alias on the Grafana Overview dashboard.
2. Identify which workspace is generating peak token traffic:
   ```bash
   curl -s http://localhost:8000/metrics | grep maya_spend_usd_total | sort -k2 -n -r | head -n 5
   ```
3. Check Google Cloud Console Vertex AI / AI Studio quota utilization dashboard.

---

## Diagnosis

| Observation | Likely cause | Next step |
| :---------- | :----------- | :-------- |
| 429 on `heavy` alias only | Complex coding/reasoning tasks exhausting tier quota | Route bulk non-critical subtasks to `lite` alias |
| 429 across all aliases | Project-wide Requests Per Minute (RPM) quota exhausted | Request immediate quota increase from Google; throttle background queues |
| Single workspace consuming > 80% tokens | Rogue agent repair loop or automated batch script | Enforce workspace spend limit and cancel rogue task |

---

## Mitigation

### Step 1: Enforce Automatic Task Spend Caps (Read-only / Config)
Ensure rogue agent repair loops do not exceed financial boundaries:
```bash
# Enable strict task spend cap enforcement (Decision §23: $1.50 cap)
export MAYA_BUDGET_ENFORCEMENT_MODE=enforce
export MAYA_GEMINI_BUDGET_TASK_USD_CAP=1.50
export MAYA_GEMINI_MAX_TOOL_TURNS_CAP=10
```

### Step 2: Route Background Subtasks to Lighter Aliases (Reversible)
Adjust alias routing in environment configuration to shed load from `heavy` and `orchestrator`:
```bash
# Temporarily alias heavy to lite during quota crisis
export MAYA_GEMINI_MODEL_OVERRIDES='{"heavy": "gemini-3.5-flash-lite"}'
docker compose -f docker/docker-compose.prod.yml up -d --no-deps backend
```

### Step 3: Activate Leaky-Bucket Rate Limiter Backoff (Reversible)
Increase worker jitter and retry backoff delay for queued subtasks:
```bash
export MAYA_GATEWAY_RETRY_BACKOFF_FACTOR=2.5
export MAYA_GATEWAY_MAX_RETRIES=5
```

---

## Rollback

Once Google Cloud quota resets or increases:
1. Revert model alias overrides: `unset MAYA_GEMINI_MODEL_OVERRIDES`.
2. Restore standard retry backoff factor: `export MAYA_GATEWAY_RETRY_BACKOFF_FACTOR=1.0`.
3. Restart worker instances and monitor queue drain rate.

---

## Escalation

- If quota is exhausted across production, escalate to Lead SRE and request Google Cloud emergency quota expansion via Cloud Console.

---

## After the incident

1. Audit task spend telemetry across all workspaces for the incident window.
2. Re-evaluate token quota reservations for multi-agent tasks.
3. Update leaky-bucket thresholds in `backend/app/mcp/limiter.py`.
