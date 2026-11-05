# Phase 16 — Production Deployment and Operations

## A. Phase Overview

- **Phase Number:** Phase 16
- **Phase Name:** Production Deployment and Operations
- **Objective:** Establish the production deployment infrastructure, high-availability reverse proxy with sticky WebSocket routing, OpenTelemetry distributed tracing, Prometheus and Grafana observability dashboards, automated key rotation and memory decay cron jobs, database backup/recovery policies (RPO <= 1h, RTO <= 4h), operational runbooks, and the final Production Readiness Review (PRR).
- **Why This Phase is Necessary:** As established in [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §22 and Decision D12, deploying an enterprise digital employee requires robust operational foundations. Real-time audio streams require sticky ingress proxies, multi-agent workflows demand end-to-end distributed tracing, token expenditures require real-time spend alarms, and disaster recovery procedures must be verified against actual backup restorations.
- **Expected Outcome:** Production Docker Compose and Kubernetes manifests; TLS reverse proxy with WebSocket sticky routing; OpenTelemetry instrumentation exporting traces to Jaeger/Tempo; Prometheus metrics scraped from FastAPI and Redis; Grafana dashboards tracking token spend and latencies; automated backup scripts; and published operational runbooks for API outages, model churn, and rate limits.
- **Priority:** High
- **Estimated Complexity:** High
- **Estimated Duration:** 15 business days.

---

## B. Prerequisites

- **Required Completed Phases:** All preceding phases: Phase 01 through Phase 15.
- **Required Infrastructure:** Cloud hosting environment (AWS, GCP, or bare metal); managed PostgreSQL 17 with pgvector; Redis 7.4 with AOF; S3-compatible object storage; DNS domain with TLS certificates.
- **Required Credentials or External Services:** Cloud provider IAM credentials, production Google Gemini API keys, production OAuth client credentials, domain TLS certificates.
- **Required Architectural Decisions:** [`MAYA_AI_FINAL_PLAN.md`](../../../MAYA_AI_FINAL_PLAN.md) §22 (Deployment topology), Decision D9 (Single-node Redis with AOF for MVP), Decision D12 (99.9% availability target).
- **Required Documentation:** [`templates/operational-runbook/TEMPLATE.md`](../../../templates/operational-runbook/TEMPLATE.md), [`docs/operations/BRANCH_PROTECTION.md`](../../operations/BRANCH_PROTECTION.md).

---

## C. Scope

### Included Features

- Container Deployment Topology:
  - Production Docker Compose configuration (docker/docker-compose.prod.yml (planned artifact)).
  - Baseline Kubernetes manifests (Deployments, StatefulSets, Services, Ingress, NetworkPolicies).
  - Non-root container security contexts across all services.
- TLS Reverse Proxy and Ingress Routing:
  - Reverse proxy (Nginx or Envoy) enforcing HTTPS and WSS termination.
  - Sticky session routing for `/api/v1/voice/live` WebSockets to guarantee state affinity.
  - Rate limiting and WAF rules shielding API endpoints from denial-of-service traffic.
- Full-Stack Observability Suite:
  - OpenTelemetry (OTel) instrumentation spanning FastAPI endpoints, LangGraph orchestrator nodes, and MCP tool dispatches.
  - Prometheus metrics exporter: token consumption by model alias, voice p95 latency, TTFT, MCP connection status, queue depth, error rates.
  - Grafana dashboards: Executive Spend Dashboard, Agent Fleet Health, System Latency & Error Ledger.
- Scheduled Operational Cron Jobs:
  - Master key rotation job (AES-256-GCM key rotation <= 90 days).
  - Memory decay and archive maintenance job (`archive_stale_memories` running daily).
  - MCP schema-hash drift monitor running hourly.
- Database Backup and Disaster Recovery:
  - Automated daily snapshots and continuous WAL archiving.
  - Disaster recovery verification validating Recovery Point Objective (RPO <= 1 hour) and Recovery Time Objective (RTO <= 4 hours).
- Operational Runbooks:
  - Runbook 1: Gemini Live API Outage (Graceful degradation to text chat).
  - Runbook 2: Model Deprecation / Churn (Updating alias configuration without downtime).
  - Runbook 3: Rate-Limit Quota Storms (Leaky-bucket tuning and backoff policies).
  - Runbook 4: Database Disaster Recovery (Restoring from WAL archives).

### Excluded Features

- Multi-region active-active database clustering (post-MVP per Decision D9).
- Automated self-healing infrastructure autoscaling on bare metal.

### Deferred Features

- Multi-cloud hybrid failover topologies.

### Dependencies on Future Phases

- None (Final implementation phase; ongoing operations).

---

## D. Implementation Breakdown

### Task P16-T001: Production Container and Ingress Manifests

- **Task ID:** P16-T001
- **Task Name:** Build Production Docker Compose and Ingress Configuration
- **Detailed Description:** Create docker/docker-compose.prod.yml (planned artifact) and docker/nginx/nginx.conf (planned artifact). Configure:
  - TLS 1.3 termination with modern cipher suites.
  - Sticky session hashing on client IP or session cookie for `/api/v1/voice/live` WebSockets.
  - Strict security headers (`HSTS`, `Content-Security-Policy`, `X-Frame-Options: DENY`, `X-Content-Type-Options: nosniff`).
  - FastAPI replicas running behind Gunicorn/Uvicorn worker managers.
  - Network segmentation: internal database and Redis ports not exposed to the public network.
- **Expected Files:** docker/docker-compose.prod.yml (planned artifact), docker/nginx/nginx.conf (planned artifact), docker/nginx/Dockerfile (planned artifact).
- **Dependencies:** None.
- **Assigned Agent Role:** DevOps Infrastructure Lead.
- **Required Tests:** `docker compose -f docker/docker-compose.prod.yml config` and TLS configuration audit.
- **Documentation Updates:** docs/operations/DEPLOYMENT_GUIDE.md (planned artifact).
- **Acceptance Criteria:** Production compose validates; TLS terminates cleanly; WebSockets maintain sticky affinity; database ports are inaccessible from outside the internal network.

### Task P16-T002: OpenTelemetry Distributed Tracing Instrumentation

- **Task ID:** P16-T002
- **Task Name:** Implement OpenTelemetry Tracing across FastAPI, LangGraph, and MCP
- **Detailed Description:** Implement backend/app/core/telemetry.py (planned artifact) using `opentelemetry-sdk` and `opentelemetry-instrumentation-fastapi`. Instrument:
  - Incoming HTTP and WebSocket requests (inject `trace_id` and `span_id`).
  - LangGraph node transitions (record subtask ID, node name, duration).
  - ModelGateway calls (record model alias, prompt tokens, candidate tokens, spend USD).
  - MCP tool dispatches (record tool name, server name, status, duration).
  - Export spans via OTLP gRPC to Jaeger, Tempo, or cloud tracing backends.
- **Expected Files:** backend/app/core/telemetry.py (planned artifact), backend/tests/integration/test_telemetry.py (planned artifact).
- **Dependencies:** Phase 01, Phase 03, Phase 05.
- **Assigned Agent Role:** Observability & Site Reliability Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_telemetry.py`.
- **Documentation Updates:** docs/operations/OBSERVABILITY.md (planned artifact).
- **Acceptance Criteria:** End-to-end trace shows full request path from HTTP ingress -> LangGraph nodes -> Model Gateway -> MCP tool execution; token spend and duration recorded on spans.

### Task P16-T003: Prometheus Metrics Exporter and Grafana Dashboards

- **Task ID:** P16-T003
- **Task Name:** Build Prometheus Metrics and Provision Grafana Dashboards
- **Detailed Description:** Implement Prometheus metrics in backend/app/core/metrics.py (planned artifact):
  - `maya_token_usage_total{alias, type}`: Counter.
  - `maya_spend_usd_total{workspace_id}`: Counter.
  - `maya_voice_latency_seconds`: Histogram.
  - `maya_task_duration_seconds{status}`: Histogram.
  - `maya_mcp_server_health{server_name}`: Gauge (1=healthy, 0=degraded).
  - Create Grafana dashboard JSON models in `docker/grafana/dashboards/` displaying spend by workspace, error rates, voice latencies, and queue depth.
- **Expected Files:** backend/app/core/metrics.py (planned artifact), docker/grafana/dashboards/maya_overview.json (planned artifact), docker/prometheus/prometheus.yml (planned artifact).
- **Dependencies:** P16-T002.
- **Assigned Agent Role:** Observability Engineer.
- **Required Tests:** Metric endpoint scrape test (`curl http://localhost:8000/metrics`).
- **Documentation Updates:** docs/operations/OBSERVABILITY.md (planned artifact).
- **Acceptance Criteria:** `/metrics` exposes valid Prometheus metrics; Grafana renders charts with accurate telemetry; spend alert thresholds trigger on budget spikes.

### Task P16-T004: Scheduled Maintenance Jobs

- **Task ID:** P16-T004
- **Task Name:** Implement Key Rotation and Memory Decay Cron Automation
- **Detailed Description:** Implement CLI maintenance runners in `backend/app/cli/`:
  - `rotate_master_key`: Generate new AES-256-GCM key, update active `key_id`, re-encrypt stored secrets in background batches.
  - `run_memory_decay`: Execute `UPDATE memory_items SET expires_at = now() WHERE permanent = false AND last_accessed_at < now() - interval '180 days'`.
  - `audit_mcp_schemas`: Query all connected MCP servers, recompute tool schema hashes, and alert on any detected drift.
  - Package runners as Kubernetes CronJobs or standalone Docker cron containers.
- **Expected Files:** backend/app/cli/maintenance.py (planned artifact), docker/cron/crontab (planned artifact), backend/tests/integration/test_maintenance_jobs.py (planned artifact).
- **Dependencies:** Phase 02, Phase 11.
- **Assigned Agent Role:** Site Reliability Engineer.
- **Required Tests:** `pytest backend/tests/integration/test_maintenance_jobs.py`.
- **Documentation Updates:** docs/operations/MAINTENANCE_JOBS.md (planned artifact).
- **Acceptance Criteria:** Master key rotates without downtime or decrypt failure; stale memories archive accurately; schema drift triggers administrative alert.

### Task P16-T005: Backup Strategy, Disaster Recovery, and RPO/RTO Validation

- **Task ID:** P16-T005
- **Task Name:** Implement Automated Backups and Execute Disaster Recovery Drill
- **Detailed Description:** Implement scripts/operations/backup_database.sh (planned artifact) using `pg_dump` with encryption and S3 upload. Configure continuous PostgreSQL WAL archiving to S3. Execute disaster recovery simulation:
  - Wipe primary database volume.
  - Restore from latest snapshot and replay WAL logs up to the failure point.
  - Measure elapsed recovery time (RTO target <= 4 hours).
  - Verify data loss gap (RPO target <= 1 hour).
  - Document drill results in docs/operations/DISASTER_RECOVERY_REPORT.md (planned artifact).
- **Expected Files:** scripts/operations/backup_database.sh (planned artifact), scripts/operations/restore_database.sh (planned artifact), docs/operations/DISASTER_RECOVERY_REPORT.md (planned artifact).
- **Dependencies:** Phase 02.
- **Assigned Agent Role:** Database Administrator & SRE.
- **Required Tests:** End-to-end backup and restore execution test in isolated staging environment.
- **Documentation Updates:** docs/operations/DISASTER_RECOVERY_REPORT.md (planned artifact).
- **Acceptance Criteria:** Backup executes and verifies checksums; disaster recovery drill proves RPO <= 1h and RTO <= 4h; zero corrupted tables.

### Task P16-T006: Operational Runbooks and Production Readiness Review (PRR)

- **Task ID:** P16-T006
- **Task Name:** Publish Operational Runbooks and Complete PRR
- **Detailed Description:** Create comprehensive operational runbooks:
  - docs/operations/runbooks/RUNBOOK_01_LIVE_API_OUTAGE.md (planned artifact): Procedure to switch voice sessions to text degraded mode with TTS fallback.
  - docs/operations/runbooks/RUNBOOK_02_MODEL_CHURN.md (planned artifact): Step-by-step instructions to update alias mappings in config during model deprecation.
  - docs/operations/runbooks/RUNBOOK_03_RATE_LIMIT_STORMS.md (planned artifact): Tuning leaky-bucket burst allowances and provider quotas.
  - docs/operations/runbooks/RUNBOOK_04_DATABASE_FAILOVER.md (planned artifact): Restoring database and promoting standby instances.
  - Conduct Production Readiness Review audit against all NFRs, security policies, and SLA commitments.
- **Expected Files:** `docs/operations/runbooks/*.md`, docs/operations/PRODUCTION_READINESS_REVIEW.md (planned artifact).
- **Dependencies:** P16-T001 through P16-T005.
- **Assigned Agent Role:** Principal Software Architect & Operations Lead.
- **Required Tests:** Verification of runbook command syntax and emergency procedures.
- **Documentation Updates:** docs/operations/PRODUCTION_READINESS_REVIEW.md (planned artifact).
- **Acceptance Criteria:** All 4 runbooks published and verified; PRR signed off by lead architect confirming compliance with all 14 architectural decisions.

---

## E. File and Folder Changes

- **Files to Create:**
  - docker/docker-compose.prod.yml (planned artifact)
  - docker/nginx/nginx.conf (planned artifact)
  - docker/nginx/Dockerfile (planned artifact)
  - docker/prometheus/prometheus.yml (planned artifact)
  - docker/grafana/dashboards/maya_overview.json (planned artifact)
  - backend/app/core/telemetry.py (planned artifact)
  - backend/app/core/metrics.py (planned artifact)
  - backend/app/cli/maintenance.py (planned artifact)
  - scripts/operations/backup_database.sh (planned artifact)
  - scripts/operations/restore_database.sh (planned artifact)
  - backend/tests/integration/test_telemetry.py (planned artifact)
  - backend/tests/integration/test_maintenance_jobs.py (planned artifact)
  - docs/operations/DEPLOYMENT_GUIDE.md (planned artifact)
  - docs/operations/OBSERVABILITY.md (planned artifact)
  - docs/operations/MAINTENANCE_JOBS.md (planned artifact)
  - docs/operations/DISASTER_RECOVERY_REPORT.md (planned artifact)
  - docs/operations/PRODUCTION_READINESS_REVIEW.md (planned artifact)
  - docs/operations/runbooks/RUNBOOK_01_LIVE_API_OUTAGE.md (planned artifact)
  - docs/operations/runbooks/RUNBOOK_02_MODEL_CHURN.md (planned artifact)
  - docs/operations/runbooks/RUNBOOK_03_RATE_LIMIT_STORMS.md (planned artifact)
  - docs/operations/runbooks/RUNBOOK_04_DATABASE_FAILOVER.md (planned artifact)
- **Files to Modify:**
  - `backend/app/main.py`
  - `README.md`
- **Files to Avoid Modifying:**
  - `MAYA_AI_FINAL_PLAN.md`
- **Relevant Directories:** `docker/`, `scripts/operations/`, `docs/operations/`.

---

## F. API and Data Contracts

### 1. Prometheus Metrics Scrape Endpoint

- **`GET /metrics`**
  - Response: Prometheus text format 0.0.4 exposing counters, gauges, and histograms.

### 2. Distributed Tracing Header Contract

- **W3C Trace Context:** `traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01`

---

## G. Security Requirements

- **Production Network Isolation:** Database, Redis, and internal worker communication must remain on private, non-routable container networks.
- **TLS Configuration:** Minimum TLS version 1.2 (TLS 1.3 preferred); HSTS enabled with `max-age=31536000; includeSubDomains`.
- **Zero Plaintext Secrets:** Production environment variables injected via cloud secret managers (AWS Secrets Manager, GCP Secret Manager, or HashiCorp Vault).

---

## H. Testing Strategy

- **Disaster Recovery Drill:** Wipe staging database; execute automated restore from S3 WAL archive; assert recovery achieves RPO <= 1h and RTO <= 4h.
- **Failover Runbook Testing:** Simulate Gemini Live API network block; execute Runbook 01; verify client transitions gracefully to degraded text chat with TTS.
- **Metrics Accuracy Tests:** Execute 100 test requests; query `/metrics` and assert counter matches exact request count and token expenditure.

---

## I. Definition of Done

1. Production Docker Compose and TLS reverse proxy deploy cleanly.
2. OpenTelemetry traces export end-to-end requests across services.
3. Prometheus scrapes `/metrics` and Grafana displays operational dashboards.
4. Maintenance jobs for key rotation, memory decay, and schema auditing function.
5. Disaster recovery drill verifies RPO <= 1h and RTO <= 4h.
6. All 4 operational runbooks are published and verified.
7. Production Readiness Review (PRR) is approved and recorded.

---

## J. Phase Exit Gate

- **Demonstrated Proof:** Successful execution of staging deployment with TLS, OTel tracing, and Prometheus metrics active; signed and approved `PRODUCTION_READINESS_REVIEW.md`.
- **Project Complete:** MAYA AI platform is verified, hardened, and ready for production operations.
