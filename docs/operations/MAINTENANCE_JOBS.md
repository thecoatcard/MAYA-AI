# Scheduled Operational Maintenance Jobs

This manual specifies the operational schedule, execution procedures, and security controls for MAYA AI's automated maintenance tasks matching [`MAYA_AI_FINAL_PLAN.md`](../../MAYA_AI_FINAL_PLAN.md) (§14, §22, Decisions D4, D7, D11) and [`PHASE_16_PRODUCTION_DEPLOYMENT.md`](../implementation/IMPLEMENTATION_PHASES/PHASE_16_PRODUCTION_DEPLOYMENT.md).

---

## 1. Maintenance Jobs Inventory

| Job Name | Schedule | Target Tables | Primary Purpose | SLA / Constraint |
| :--- | :--- | :--- | :--- | :--- |
| **`rotate_master_key`** | Every 90 Days | `mcp_servers` | Re-encrypts all stored secrets under a new AES-256-GCM key. | NFR: <= 90-day key lifetime. Zero decrypt failures. |
| **`run_memory_decay`** | Daily at 02:00 UTC | `memory_items` | Archives non-permanent memories unaccessed for > 180 days. | Decision D7: `expires_at = now()` on stale items. |
| **`audit_mcp_schemas`** | Hourly | `mcp_servers` | Recomputes tool schema hashes and flags supply-chain drift. | Decision D11: Tool poisoning defense. |

---

## 2. Job 1: Master Key Rotation (`rotate_master_key`)

### Security Standard
MAYA AI encrypts third-party API tokens and environment secrets in `mcp_servers.env_vars_encrypted` using AES-256-GCM with unique 12-byte nonces. In accordance with the NFR security policy, master encryption keys must rotate at least once every 90 days.

### Execution Procedure
The runner decrypts secrets under `old_key`, re-encrypts under `new_key`, updates `key_id`, and commits atomically:

```python
from app.cli.maintenance import rotate_master_key, generate_master_key

new_key_bytes, new_key_id = generate_master_key()
result = await rotate_master_key(
    session,
    old_key=current_master_key_bytes,
    new_key=new_key_bytes,
    new_key_id=new_key_id,
)
```

### Verification
1. Confirm re-encrypted record count matches `SELECT count(*) FROM mcp_servers WHERE env_vars_encrypted IS NOT NULL`.
2. Verify test decryption succeeds across all updated rows before retiring the old key.

---

## 3. Job 2: Memory Decay & Archival (`run_memory_decay`)

### Architectural Background (Decision D7 & Defect #16)
To keep retrieval indices fast and prevent outdated context from degrading agent performance, episodic and knowledge memory items that are not marked `permanent = true` and have not been accessed for 180 days are expired.

Per Defect #16, exponential decay affects memory ranking during retrieval, but stale memories are definitively marked for garbage collection via `expires_at`:

```sql
UPDATE memory_items
SET expires_at = now()
WHERE permanent = false
  AND expires_at IS NULL
  AND last_accessed_at < now() - interval '180 days';
```

### CLI Execution
```bash
python -m app.cli.maintenance --job decay
```

---

## 4. Job 3: MCP Schema Drift Audit (`audit_mcp_schemas`)

### Supply-Chain Defense (Decision D11)
External MCP servers could theoretically suffer supply-chain compromise or alter tool descriptions to perform indirect prompt injection. The hourly audit calculates the SHA-256 canonical hash of the live tool manifest:

$$\text{hash} = \text{SHA-256}(\text{canonical\_json}(\text{sorted\_tools}))$$

If `observed_hash != server.schema_hash`:
- An alert is emitted to the operational monitoring channel.
- Server is flagged for quarantine.

### CLI Execution
```bash
python -m app.cli.maintenance --job audit
```

---

## 5. Kubernetes CronJob Manifests

In Kubernetes environments, maintenance jobs are deployed as isolated batch `CronJob` workloads:

```yaml
apiVersion: batch/v1
kind: CronJob
metadata:
  name: maya-memory-decay
  namespace: maya-production
spec:
  schedule: "0 2 * * *"  # Daily at 02:00 UTC
  concurrencyPolicy: Forbid
  successfulJobsHistoryLimit: 3
  failedJobsHistoryLimit: 5
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: decay-runner
              image: ghcr.io/maya-ai/backend:0.1.0
              command: ["python", "-m", "app.cli.maintenance", "--job", "decay"]
              envFrom:
                - secretRef:
                    name: maya-production-secrets
---
apiVersion: batch/v1
kind: CronJob
metadata:
  name: maya-mcp-schema-audit
  namespace: maya-production
spec:
  schedule: "0 * * * *"  # Hourly
  concurrencyPolicy: Forbid
  jobTemplate:
    spec:
      template:
        spec:
          restartPolicy: OnFailure
          containers:
            - name: schema-audit-runner
              image: ghcr.io/maya-ai/backend:0.1.0
              command: ["python", "-m", "app.cli.maintenance", "--job", "audit"]
              envFrom:
                - secretRef:
                    name: maya-production-secrets
```
