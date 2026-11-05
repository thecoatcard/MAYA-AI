# Health API

The implemented local API exposes only health probes. Neither probe requires
authentication. They must not be used to expose infrastructure diagnostics.

| Route | Success | Failure |
| :--- | :--- | :--- |
| `GET /api/v1/healthz` | 200, `{"status":"alive"}` | Process/network failure if API is unavailable |
| `GET /api/v1/readyz` | 200, `{"status":"ready"}` | 503, `{"status":"unavailable"}` if either dependency probe fails |

Readiness performs PostgreSQL `SELECT 1` followed by Redis `PING` within one
bounded timeout. Known connection, database, Redis and timeout failures emit a
generic structured error event. No exception detail, dependency name, URL or
per-service latency is returned. Unexpected programming errors are not disguised as success.

Every request gets a server-generated UUID in `X-Request-ID` and a generated
trace ID in structured application logs. Incoming correlation headers are not trusted.
API documentation is available at `/docs`; the OpenAPI schema is at `/openapi.json`.

Startup fails if either service is unavailable and attempts to close both pools.
Once running, readiness can report a dependency outage while liveness remains available.
