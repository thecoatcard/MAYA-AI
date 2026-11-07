# Observability, Distributed Tracing, and Prometheus Metrics

This document details the observability architecture, OpenTelemetry distributed tracing, Prometheus metrics exporter, and Grafana dashboards for MAYA AI, fulfilling [`MAYA_AI_FINAL_PLAN.md`](../../MAYA_AI_FINAL_PLAN.md) (§22, Decisions D9, D12) and [`PHASE_16_PRODUCTION_DEPLOYMENT.md`](../implementation/IMPLEMENTATION_PHASES/PHASE_16_PRODUCTION_DEPLOYMENT.md).

---

## 1. Observability Architecture

MAYA AI employs a multi-tiered observability framework designed for sub-second tracing across asynchronous boundaries, real-time token spend accounting, and strict SLA compliance monitoring:

```
               ┌────────────────────────────────────────────────────────┐
               │                   W3C Trace Context                    │
               │  traceparent: 00-4bf92f3577b34da6a3ce929d0e0e4736-...  │
               └───────────────────────────┬────────────────────────────┘
                                           │
 ┌─────────────────────────────────────────▼─────────────────────────────────────────┐
 │ FastAPI Ingress Middleware                                                        │
 │ ├── Generates or preserves W3C traceparent and correlation IDs                     │
 │ ├── Starts root span: http.get / http.post                                        │
 │ └── Increments maya_http_requests_total & records request latency                 │
 └─────────────────────────┬─────────────────────────────────────────────────────────┘
                           │
       ┌───────────────────┴───────────────────┐
       │                                       │
┌──────▼─────────────────────────┐   ┌─────────▼─────────────────────────┐
│ LangGraph Orchestrator Nodes   │   │ Gemini Live Voice Engine          │
│ ├── @trace_langgraph_node      │   │ ├── Measures audio turn latency   │
│ └── Subtask execution spans    │   │ └── Observes maya_voice_latency   │
└──────┬─────────────────────────┘   └───────────────────────────────────┘
       │
       ├───────────────────────────────────────┐
       │                                       │
┌──────▼─────────────────────────┐   ┌─────────▼─────────────────────────┐
│ Model Gateway Calls            │   │ MCP Tool Server Dispatches        │
│ ├── trace_model_gateway_call   │   │ ├── trace_mcp_tool_dispatch       │
│ ├── Prompt/candidate tokens    │   │ ├── Server & tool status          │
│ └── maya_token_usage_total     │   │ └── maya_mcp_server_health        │
└────────────────────────────────┘   └───────────────────────────────────┘
```

---

## 2. Distributed Tracing Specification

### W3C Trace Context Standard
All incoming and outgoing requests pass W3C Trace Context headers:
- `traceparent: 00-{trace_id}-{parent_id}-{trace_flags}`
  - `version`: `00`
  - `trace_id`: 16-byte random hex string (32 characters)
  - `parent_id` / `span_id`: 8-byte random hex string (16 characters)
  - `trace_flags`: `01` (recorded / sampled)

### Python Instrumentation API
The tracing engine lives in [`backend/app/core/telemetry.py`](../../backend/app/core/telemetry.py):

```python
from app.core.telemetry import (
    trace_span,
    trace_langgraph_node,
    trace_model_gateway_call,
    trace_mcp_tool_dispatch,
    tracer,
)

# 1. Tracing LangGraph orchestrator nodes
@trace_langgraph_node("planner_node", subtask_id="task-123")
async def execute_planner(state: dict) -> dict:
    ...

# 2. Recording Model Gateway executions on active span
trace_model_gateway_call(
    alias="orchestrator",
    prompt_tokens=420,
    candidate_tokens=150,
    spend_usd=0.0035,
)

# 3. Recording MCP tool dispatches
trace_mcp_tool_dispatch(
    tool_name="git_commit",
    server_name="git",
    status="success",
    duration_ms=45.2,
)
```

---

## 3. Prometheus Metrics Catalogue

Metrics are exposed in standard Prometheus text exposition format (version 0.0.4) on the endpoint `GET /metrics`.

| Metric Name | Type | Labels | Description / Target |
| :--- | :--- | :--- | :--- |
| `maya_token_usage_total` | Counter | `alias`, `type` | Cumulative token consumption by model alias (`orchestrator`, `heavy`, `lite`, `live`) and token category (`prompt`, `candidate`, `cached`). |
| `maya_spend_usd_total` | Counter | `workspace_id` | Cumulative financial spend in USD partitioned by workspace tenant. |
| `maya_voice_latency_seconds` | Histogram | *None* | End-to-end voice latency in seconds. **Decision D12 SLA: p95 <= 850 ms** under normal load. |
| `maya_task_duration_seconds` | Histogram | `status` | Execution duration in seconds for asynchronous worker subtasks (`completed`, `failed`). |
| `maya_mcp_server_health` | Gauge | `server_name` | Connectivity health status of registered MCP servers (`1` = healthy, `0` = degraded). |
| `maya_http_requests_total` | Counter | `method`, `endpoint`, `status` | Ingress HTTP request volume across REST API routes. |
| `maya_http_request_duration_seconds` | Histogram | `method`, `endpoint` | Ingress HTTP request latency distribution. |

### Scraping the `/metrics` Endpoint

```bash
curl -s http://localhost:8000/metrics | grep maya_
```

Example scrape output:
```prometheus
# HELP maya_token_usage_total Cumulative count of tokens consumed through Model Gateway
# TYPE maya_token_usage_total counter
maya_token_usage_total{alias="orchestrator",type="prompt"} 145020.0
maya_token_usage_total{alias="orchestrator",type="candidate"} 38210.0

# HELP maya_voice_latency_seconds End-to-end voice latency in seconds (SLA target p95 <= 0.85s)
# TYPE maya_voice_latency_seconds histogram
maya_voice_latency_seconds_bucket{le="0.05"} 12
maya_voice_latency_seconds_bucket{le="0.1"} 84
maya_voice_latency_seconds_bucket{le="0.45"} 320
maya_voice_latency_seconds_bucket{le="0.85"} 412
maya_voice_latency_seconds_bucket{le="+Inf"} 418
maya_voice_latency_seconds_sum 198.4
maya_voice_latency_seconds_count 418

# HELP maya_mcp_server_health Health status of registered MCP servers (1=healthy, 0=degraded)
# TYPE maya_mcp_server_health gauge
maya_mcp_server_health{server_name="filesystem"} 1.0
maya_mcp_server_health{server_name="github"} 1.0
```

---

## 4. Grafana Dashboards

The pre-built operational dashboard is stored in [`docker/grafana/dashboards/maya_overview.json`](../../docker/grafana/dashboards/maya_overview.json) and auto-provisioned via [`docker/grafana/provisioning/`](../../docker/grafana/provisioning/):

1. **Voice Engine & Real-Time Audio Panel:** Real-time p95, p90, and p50 latency tracking with alert thresholds at 600 ms (warning) and 850 ms (critical SLA violation).
2. **MCP Tool Server Health Grid:** Instant visual health matrix of all active tool servers.
3. **Model Gateway Token Usage & Financial Velocity:** Stacked timeseries showing prompt/candidate tokens across aliases and cumulative workspace burn rate.
4. **Task Orchestrator Throughput:** Task completion rates, p95 execution durations, and error ratios.

---

## 5. Alerting Policies & Runbook Triggers

| Alert Name | Condition | Severity | Associated Runbook |
| :--- | :--- | :--- | :--- |
| `VoiceLatencySLAViolation` | `histogram_quantile(0.95, rate(maya_voice_latency_seconds_bucket[5m])) > 0.85` for 3m | Critical | [`RUNBOOK_01_LIVE_API_OUTAGE.md`](runbooks/RUNBOOK_01_LIVE_API_OUTAGE.md) |
| `LiveApiOutage` | Rate of 5xx errors on `/api/v1/voice/live` > 5% for 2m | Critical | [`RUNBOOK_01_LIVE_API_OUTAGE.md`](runbooks/RUNBOOK_01_LIVE_API_OUTAGE.md) |
| `ModelQuotaExhaustion` | Rate of HTTP 429 errors from Google GenAI > 2% for 2m | High | [`RUNBOOK_03_RATE_LIMIT_STORMS.md`](runbooks/RUNBOOK_03_RATE_LIMIT_STORMS.md) |
| `McpServerDown` | `maya_mcp_server_health == 0` for 1m | High | Investigation / restart supervisor |
| `TaskSpendCapExceeded` | Single task spend > $1.50 or tool turns > 10 | Medium | Automatic suspension & admin alert |
