# Production Deployment and Operations Guide

This guide details the deployment topology, network configuration, TLS ingress, and operational maintenance for MAYA AI in production, matching the architectural decisions in [`MAYA_AI_FINAL_PLAN.md`](../../MAYA_AI_FINAL_PLAN.md) (§22, Decisions D9, D12) and [`PHASE_16_PRODUCTION_DEPLOYMENT.md`](../implementation/IMPLEMENTATION_PHASES/PHASE_16_PRODUCTION_DEPLOYMENT.md).

---

## 1. Production Topology Overview

The MAYA AI production runtime is structured as a hardened container topology orchestrated through [`docker/docker-compose.prod.yml`](../../docker/docker-compose.prod.yml):

```
                        INTERNET (Clients & Web Browsers)
                                      │ HTTPS :443 / WSS
                          ┌───────────▼───────────┐
                          │   Nginx Ingress Proxy │
                          │   (TLS 1.3 Termination│
                          │   Sticky WS Routing)  │
                          └─────┬───────────────┬─┘
                 maya-public    │               │
            ┌───────────────────┘               └───────────────────┐
            │                                                       │
  ┌─────────▼─────────┐                                   ┌─────────▼─────────┐
  │   Next.js 15 Web  │ (Port 3000)                       │   FastAPI Backend │ (Port 8000)
  │   Container       │                                   │   Container       │
  │   (UID 10001)     │                                   │   (UID 10001)     │
  └───────────────────┘                                   └─────────┬─────────┘
                                                       maya-internal│
                                   ┌────────────────────────────────┼─────────────────┐
                                   │                                │                 │
                         ┌─────────▼─────────┐            ┌─────────▼─────────┐ ┌─────▼───────┐
                         │   PostgreSQL 17   │ (Port 5432)│   Redis 7.4       │ │ Prometheus  │
                         │   + pgvector      │            │   (AOF, Port 6379)│ │ & Grafana   │
                         │   (RLS enabled)   │            │   (Decision D9)   │ │ (Metrics)   │
                         └───────────────────┘            └───────────────────┘ └─────────────┘
```

### Key Architectural Characteristics

1. **Network Segmentation:**
   - `maya-public`: Ingress bridge network connecting `nginx`, `web`, and `backend`. Only ports `80` and `443` on Nginx are published to host interfaces.
   - `maya-internal`: Isolated internal bridge network (`internal: true`). PostgreSQL, Redis, Prometheus, and Grafana are strictly bound to this network with **zero exposed host ports**.
2. **Non-Root Runtime Enforcement:**
   - Backend runs under unprivileged UID `10001:10001` (`maya`).
   - Web frontend runs under unprivileged UID `10001:10001` (`nextjs`).
   - Prometheus runs under UID `65534:65534` (`nobody`).
   - Grafana runs under UID `472:472` (`grafana`).
3. **Sticky Session Routing (Decision D3 & D12):**
   - Ingress Nginx applies `ip_hash` upstream routing for `/api/v1/voice/live` WebSockets. This preserves voice state affinity and ensures sub-850 ms p95 audio latency.
   - Server-Sent Events (`/api/v1/tasks/{id}/events`) disable proxy buffering for real-time DAG execution telemetry.

---

## 2. Infrastructure Requirements

| Component | Minimum Specification | Recommended Production |
| :--- | :--- | :--- |
| **Compute** | 4 vCPU, 16 GB RAM | 8 vCPU, 32 GB RAM |
| **Storage** | 100 GB NVMe SSD | 500 GB NVMe SSD (provisioned IOPS) |
| **Host OS** | Ubuntu 24.04 LTS / Debian 12 | Linux with Docker 26+ & Docker Compose v2.27+ |
| **PostgreSQL** | v17 with `pgvector` & `pg_trgm` | Dedicated RDS/Aurora or Container with persistent NVMe volume |
| **Redis** | v7.4 with AOF persistence | Single-node Redis 7.4 with AOF (Decision D9) |

---

## 3. Environment Configuration & Secrets

Production secrets must never be committed to source control. Inject secrets through secure environment files or cloud secret managers (AWS Secrets Manager, GCP Secret Manager, or HashiCorp Vault).

```bash
# Core Application Database (Restricted App Login)
POSTGRES_DB=maya
POSTGRES_USER=maya_admin
POSTGRES_PASSWORD=<STRONG_RANDOM_ADMIN_PASSWORD>
POSTGRES_APP_PASSWORD=<STRONG_RANDOM_APP_PASSWORD>

# Redis Cache and Event Bus (Decision D9)
REDIS_PASSWORD=<STRONG_RANDOM_REDIS_PASSWORD>

# Cryptographic Keys (Min 32 bytes / 256 bits)
MAYA_JWT_SIGNING_KEY=<HEX_OR_BASE64_32_BYTES_KEY>
MAYA_OTP_HMAC_KEY=<HEX_OR_BASE64_32_BYTES_KEY>

# Gemini Model Gateway (Decision D13)
MAYA_GEMINI_API_KEY=<PRODUCTION_GEMINI_API_KEY>
MAYA_BUDGET_ENFORCEMENT_MODE=enforce

# Monitoring Credentials
GRAFANA_ADMIN_USER=admin
GRAFANA_ADMIN_PASSWORD=<STRONG_RANDOM_GRAFANA_PASSWORD>
```

---

## 4. Deployment Procedure

### Step 1: Pre-Flight Configuration Verification

Validate the Compose configuration and verify environment variable bindings:

```bash
cd docker
docker compose -f docker-compose.prod.yml config
```

### Step 2: TLS Certificate Provisioning

Place valid production certificates into the `nginx_certs` directory or volume:
- Certificate chain: `/etc/nginx/certs/fullchain.pem`
- Private key: `/etc/nginx/certs/privkey.pem` (mode `0600`)

### Step 3: Service Startup

Start services in detached mode:

```bash
docker compose -f docker-compose.prod.yml up -d
```

### Step 4: Health and Liveness Verification

Confirm healthchecks on all services:

```bash
docker compose -f docker-compose.prod.yml ps

# Check backend health directly through ingress
curl -k https://localhost/api/v1/healthz
# Expected response: {"status":"alive"}

curl -k https://localhost/api/v1/readyz
# Expected response: {"status":"ready"}
```

---

## 5. Zero-Downtime Rolling Update

When updating the backend or web containers:

```bash
# 1. Pull or build updated image
docker compose -f docker-compose.prod.yml build backend

# 2. Re-create backend container without interrupting database or redis
docker compose -f docker-compose.prod.yml up -d --no-deps backend

# 3. Verify healthcheck passes before updating next service
docker compose -f docker-compose.prod.yml ps backend
```

---

## 6. Rollback Procedure

If an unrecoverable defect is discovered post-deployment:

```bash
# 1. Tag or restore previous image artifact
docker tag ghcr.io/maya-ai/backend:previous ghcr.io/maya-ai/backend:0.1.0

# 2. Restart backend container with rolled back image
docker compose -f docker-compose.prod.yml up -d --no-deps backend

# 3. Verify readiness
curl -k https://localhost/api/v1/readyz
```
