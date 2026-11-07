# Phase 16 — Production Deployment and Operations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Establish production deployment infrastructure, TLS reverse proxy with sticky WebSocket routing, OpenTelemetry distributed tracing, Prometheus metrics exporter and Grafana dashboard models, automated maintenance jobs (key rotation, memory decay, schema audit), database backup and disaster recovery automation (RPO <= 1h, RTO <= 4h), comprehensive operational runbooks, and the final Production Readiness Review (PRR).

**Architecture:** Production-grade operational ecosystem:
1. `ProductionContainerTopology` (`docker/docker-compose.prod.yml`, `docker/nginx/`): TLS 1.3 termination, sticky WebSocket routing for `/api/v1/voice/live`, private network isolation;
2. `ObservabilitySuite` (`backend/app/core/telemetry.py`, `backend/app/core/metrics.py`, `docker/prometheus/`, `docker/grafana/`): W3C distributed tracing, Prometheus metrics scrape endpoint (`GET /metrics`), executive spend dashboards;
3. `MaintenanceAutomation` (`backend/app/cli/maintenance.py`): master secret rotation, stale memory archival, and MCP schema hash drift monitor;
4. `DisasterRecoveryEngine` (`scripts/operations/backup_database.sh`, `restore_database.sh`): automated snapshotting and continuous WAL archiving;
5. `OperationalRunbooks` (`docs/operations/runbooks/`): step-by-step incident response procedures for Live API outages, model deprecation, rate limit storms, and database failover;
6. `ProductionReadinessReview` (`docs/operations/PRODUCTION_READINESS_REVIEW.md`): final certification sign-off across all 14 architectural decisions.

**Tech Stack:** Docker, Nginx, Prometheus, Grafana, OpenTelemetry, Python 3.12, FastAPI, PostgreSQL 17, Redis 7.4.

**Spec:** `MAYA_AI_FINAL_PLAN.md` (§22, Decisions D9, D12), `docs/implementation/IMPLEMENTATION_PHASES/PHASE_16_PRODUCTION_DEPLOYMENT.md`.

## Global Constraints

- **Decision D9 (Redis Topology):** Single-node Redis with AOF persistence on port 16379 for MVP.
- **Decision D12 (High Availability & SLA):** 99.9% availability target; p95 voice latency <= 850 ms; barge-in <= 150 ms.
- **RPO / RTO Guarantees:** Recovery Point Objective (RPO) <= 1 hour; Recovery Time Objective (RTO) <= 4 hours.
- **Zero Plaintext Credentials:** Secrets injected strictly via environment variables or secret managers.
- **Zero Regressions:** All existing backend tests, frontend tests, and monorepo governance checks must pass.

---

### Task 1: Production Container & Ingress Manifests

**Files:**
- Create: `docker/docker-compose.prod.yml`
- Create: `docker/nginx/nginx.conf`
- Create: `docker/nginx/Dockerfile`
- Create: `docs/operations/DEPLOYMENT_GUIDE.md`

- [x] **Step 1: Write `docker-compose.prod.yml` with private networks, healthchecks, and non-root services**
- [x] **Step 2: Write `nginx.conf` with TLS termination, HSTS, security headers, and sticky WebSocket proxying**
- [x] **Step 3: Author `docs/operations/DEPLOYMENT_GUIDE.md`**

---

### Task 2: OpenTelemetry Distributed Tracing & Prometheus Metrics

**Files:**
- Create: `backend/app/core/telemetry.py`
- Create: `backend/app/core/metrics.py`
- Modify: `backend/app/main.py`
- Create: `docker/prometheus/prometheus.yml`
- Create: `docker/grafana/dashboards/maya_overview.json`
- Test: `backend/tests/integration/test_telemetry.py`
- Create: `docs/operations/OBSERVABILITY.md`

- [x] **Step 1: Implement `telemetry.py` with W3C trace context, span generation, and OTel exporter configuration**
- [x] **Step 2: Implement `metrics.py` with Prometheus counters, gauges, histograms, and `/metrics` endpoint**
- [x] **Step 3: Write tests in `backend/tests/integration/test_telemetry.py` and run with pytest**
- [x] **Step 4: Author `docs/operations/OBSERVABILITY.md` and configuration files**

---

### Task 3: Scheduled Operational Maintenance Jobs

**Files:**
- Create: `backend/app/cli/maintenance.py`
- Test: `backend/tests/integration/test_maintenance_jobs.py`
- Create: `docs/operations/MAINTENANCE_JOBS.md`

- [x] **Step 1: Implement `rotate_master_key`, `run_memory_decay`, and `audit_mcp_schemas` in `maintenance.py`**
- [x] **Step 2: Write tests in `backend/tests/integration/test_maintenance_jobs.py` and run with pytest**
- [x] **Step 3: Author `docs/operations/MAINTENANCE_JOBS.md`**

---

### Task 4: Database Backup & Disaster Recovery Automation

**Files:**
- Create: `scripts/operations/backup_database.sh`
- Create: `scripts/operations/restore_database.sh`
- Create: `docs/operations/DISASTER_RECOVERY_REPORT.md`

- [x] **Step 1: Implement `backup_database.sh` and `restore_database.sh` with checksum verification and error traps**
- [x] **Step 2: Author `docs/operations/DISASTER_RECOVERY_REPORT.md` detailing RPO <= 1h and RTO <= 4h drills**

---

### Task 5: Operational Runbooks & Production Readiness Review (PRR)

**Files:**
- Create: `docs/operations/runbooks/RUNBOOK_01_LIVE_API_OUTAGE.md`
- Create: `docs/operations/runbooks/RUNBOOK_02_MODEL_CHURN.md`
- Create: `docs/operations/runbooks/RUNBOOK_03_RATE_LIMIT_STORMS.md`
- Create: `docs/operations/runbooks/RUNBOOK_04_DATABASE_FAILOVER.md`
- Create: `docs/operations/PRODUCTION_READINESS_REVIEW.md`

- [x] **Step 1: Author Runbooks 1 through 4 following standard operational templates**
- [x] **Step 2: Author `docs/operations/PRODUCTION_READINESS_REVIEW.md` providing final sign-off across all 14 decisions**

---

### Task 6: Roadmap Status, Documentation & Final Monorepo Certification

**Files:**
- Modify: `docs/implementation/IMPLEMENTATION_STATUS.md`
- Modify: `CHANGELOG.md`
- Modify: `README.md`

- [x] **Step 1: Update `docs/implementation/IMPLEMENTATION_STATUS.md` marking Phase 16 as Implemented & Verified locally**
- [x] **Step 2: Update `CHANGELOG.md` and `README.md`**
- [x] **Step 3: Run `npm run docs:check`**
- [x] **Step 4: Run monorepo fast validation (`npm run validate:fast`)**
