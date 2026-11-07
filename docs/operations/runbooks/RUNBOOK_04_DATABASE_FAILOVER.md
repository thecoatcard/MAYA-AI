# Runbook: Database Failover and Disaster Recovery Restoration

- **Owner:** Database Administrator & Infrastructure SRE Team
- **Last verified:** 2026-11-07 by Principal SRE & Database Lead
- **Severity guide:** Sev-1 for any primary database outage or corruption event.

---

## Symptoms

1. **Dashboard Alert:** `PostgresDown` or `DependencyProbeFailed` firing in Grafana.
2. **HTTP Readiness Fails:** Backend returns `HTTP 503 Service Unavailable` on `/api/v1/readyz`.
3. **Log Signatures:** `asyncpg.exceptions.CannotConnectNowError`, `psycopg.OperationalError: server closed the connection unexpectedly`, or `DependencyUnavailable: Required local service unavailable`.

---

## Impact

- All mutating and transactional REST API endpoints return 503.
- In-flight agent subtasks pause at checkpoint boundary (Decision D6: LangGraph checkpoints guarantee resume without duplicate side effects).
- Read-only queries against local memory fail.

---

## Quick checks

1. Check database container status and healthcheck output:
   ```bash
   docker compose -f docker/docker-compose.prod.yml ps postgres
   ```
2. Inspect PostgreSQL container error logs:
   ```bash
   docker logs --tail 100 maya-postgres
   ```
3. Test direct PostgreSQL connectivity from within internal network:
   ```bash
   docker exec -it maya-backend python -c "import socket; print(socket.create_connection(('postgres', 5432), 2))"
   ```

---

## Diagnosis

| Observation | Likely cause | Next step |
| :---------- | :----------- | :-------- |
| Primary container stopped / OOMKilled | Container engine resource exhaustion | Restart container and inspect memory limits |
| Disk I/O error or filesystem corruption | Storage volume degradation | Activate Disaster Recovery Protocol and restore from backup |
| Connection pool exhausted (> 100 connections) | Leaked connections from worker tasks | Terminate idle connections; restart backend worker pool |

---

## Mitigation

### Procedure A: Fast Recovery (Container Restart)
If database process stopped due to transient host pressure:
```bash
docker compose -f docker/docker-compose.prod.yml restart postgres
# Wait for healthcheck:
docker compose -f docker/docker-compose.prod.yml ps postgres
```

### Procedure B: Full Disaster Recovery Restoration (RTO <= 4h, RPO <= 1h)
If the storage volume is unrecoverable or corrupted:

1. **Provision Clean Volume:**
   ```bash
   # Stop corrupted service
   docker compose -f docker/docker-compose.prod.yml stop postgres
   # Create replacement volume
   docker volume create maya-production_postgres_data_restored
   ```

2. **Locate and Download Latest S3 Snapshot:**
   ```bash
   aws s3 cp s3://maya-production-backups/backups/latest.dump /var/backups/maya/latest.dump
   aws s3 cp s3://maya-production-backups/backups/latest.dump.sha256 /var/backups/maya/latest.dump.sha256
   ```

3. **Execute Automated Restoration Script:**
   ```bash
   chmod +x scripts/operations/restore_database.sh
   ./scripts/operations/restore_database.sh /var/backups/maya/latest.dump
   ```

4. **Verify Smoke Test and RLS Isolation:**
   ```bash
   docker exec -it maya-postgres psql -U maya_admin -d maya -c "SELECT count(*) FROM pg_policy;"
   # Confirm RLS policies are active
   ```

5. **Restart Backend and Resume Queued Work:**
   ```bash
   docker compose -f docker/docker-compose.prod.yml up -d --no-deps backend
   curl -k https://localhost/api/v1/readyz
   # Expected: {"status":"ready"}
   ```

---

## Rollback

If restored standby exhibits data inconsistency:
1. Re-run restoration pointing to the preceding verified daily snapshot.
2. Replay continuous WAL archive segments up to the exact pre-failure timestamp.

---

## Escalation

- Escalate to Lead Database Architect and Infrastructure Director immediately upon Sev-1 declaration.

---

## After the incident

1. Document RTO and RPO metrics achieved during the incident.
2. File postmortem according to [`docs/operations/DISASTER_RECOVERY_REPORT.md`](../DISASTER_RECOVERY_REPORT.md).
3. Verify WAL archiving is operational and syncing to remote storage.
