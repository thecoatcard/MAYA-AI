"""Integration tests for OpenTelemetry distributed tracing and Prometheus metrics.

Validates W3C trace context, parent-child span hierarchy, metrics exposition,
and FastAPI /metrics scraping (Task P16-T002, Task P16-T003).
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest
from fastapi.testclient import TestClient

from app.core.metrics import (
    MCP_SERVER_HEALTH,
    SPEND_USD_TOTAL,
    TASK_DURATION_SECONDS,
    TOKEN_USAGE_TOTAL,
    VOICE_LATENCY_SECONDS,
    Counter,
    Gauge,
    Histogram,
    PrometheusRegistry,
)
from app.core.telemetry import (
    TraceContext,
    Tracer,
    trace_langgraph_node,
    trace_mcp_tool_dispatch,
    trace_model_gateway_call,
    tracer,
)
from app.main import create_app
from tests.support.settings import make_settings


def test_w3c_trace_context_lifecycle() -> None:
    """Validate W3C traceparent header serialization, parsing, and error handling."""
    ctx = TraceContext(trace_id="4bf92f3577b34da6a3ce929d0e0e4736", span_id="00f067aa0ba902b7")
    header = ctx.to_traceparent()
    assert header == "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"

    parsed = TraceContext.from_traceparent(header)
    assert parsed is not None
    assert parsed.trace_id == "4bf92f3577b34da6a3ce929d0e0e4736"
    assert parsed.span_id == "00f067aa0ba902b7"
    assert parsed.trace_flags == "01"

    # Invalid cases
    assert TraceContext.from_traceparent(None) is None
    assert TraceContext.from_traceparent("invalid-header") is None
    # Version ff is invalid
    assert (
        TraceContext.from_traceparent("ff-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01")
        is None
    )
    # All zero trace_id is invalid
    assert (
        TraceContext.from_traceparent("00-00000000000000000000000000000000-00f067aa0ba902b7-01")
        is None
    )


def test_span_hierarchy_and_context_propagation() -> None:
    """Ensure child spans inherit parent trace_id and record parent_span_id."""
    test_tracer = Tracer("test-service")

    with test_tracer.span("parent.operation", attributes={"tenant": "workspace-1"}) as parent:
        assert test_tracer.get_current_span() == parent
        parent_trace_id = parent.trace_id

        with test_tracer.span("child.operation") as child:
            assert child.trace_id == parent_trace_id
            assert child.parent_span_id == parent.span_id
            child.set_attribute("step", "fetch_records")

        assert parent.duration_ms >= 0.0

    assert len(test_tracer.recorded_spans) == 2
    child_span, parent_span = test_tracer.recorded_spans[0], test_tracer.recorded_spans[1]
    assert child_span.name == "child.operation"
    assert parent_span.name == "parent.operation"
    assert child_span.trace_id == parent_span.trace_id


@pytest.mark.asyncio
async def test_trace_langgraph_node_and_mcp_dispatch() -> None:
    """Verify LangGraph node decorators and MCP tool dispatch span telemetry."""

    @trace_langgraph_node("planner_node", subtask_id="task-42")
    async def sample_node(payload: dict[str, str]) -> dict[str, str]:
        # Emulate Model Gateway call
        trace_model_gateway_call(
            alias="orchestrator",
            prompt_tokens=150,
            candidate_tokens=45,
            spend_usd=0.0012,
        )
        # Emulate MCP tool dispatch
        trace_mcp_tool_dispatch(
            tool_name="read_file",
            server_name="filesystem",
            status="success",
            duration_ms=12.5,
        )
        return {"result": payload.get("input", "") + "_processed"}

    result = await sample_node({"input": "test_query"})
    assert result["result"] == "test_query_processed"

    # Verify spans recorded on global tracer
    recent_spans = tracer.recorded_spans
    assert len(recent_spans) >= 1
    node_span = next(s for s in reversed(recent_spans) if "planner_node" in s.name)
    assert node_span.attributes.get("langgraph.node") == "planner_node"
    assert node_span.attributes.get("subtask.id") == "task-42"


def test_prometheus_metrics_registry() -> None:
    """Test custom Counter, Gauge, and Histogram metrics exposition."""
    registry = PrometheusRegistry()
    counter = Counter("test_counter_total", "Test counter metric", ["agent"])
    gauge = Gauge("test_gauge_status", "Test gauge metric", ["resource"])
    histogram = Histogram("test_latency_seconds", "Test histogram metric", buckets=[0.1, 0.5, 1.0])

    registry.register(counter)
    registry.register(gauge)
    registry.register(histogram)

    # Counter mutations
    counter.inc(5.0, {"agent": "research"})
    counter.inc(2.0, {"agent": "research"})
    assert counter.get({"agent": "research"}) == 7.0

    # Gauge mutations
    gauge.set(10.0, {"resource": "db_pool"})
    gauge.inc(2.0, {"resource": "db_pool"})
    gauge.dec(1.0, {"resource": "db_pool"})
    assert gauge.get({"resource": "db_pool"}) == 11.0

    # Histogram observations
    histogram.observe(0.05)
    histogram.observe(0.4)
    histogram.observe(0.9)

    output = registry.generate_latest()
    assert "# HELP test_counter_total Test counter metric" in output
    assert "# TYPE test_counter_total counter" in output
    assert 'test_counter_total{agent="research"} 7.0' in output
    assert 'test_gauge_status{resource="db_pool"} 11.0' in output
    assert 'test_latency_seconds_bucket{le="0.1"} 1' in output
    assert 'test_latency_seconds_bucket{le="0.5"} 2' in output
    assert 'test_latency_seconds_bucket{le="1.0"} 3' in output
    assert 'test_latency_seconds_bucket{le="+Inf"} 3' in output


def test_fastapi_metrics_scrape_endpoint() -> None:
    """Verify GET /metrics endpoint returns valid Prometheus 0.0.4 text format."""
    # Track some test production metrics
    TOKEN_USAGE_TOTAL.inc(120.0, {"alias": "orchestrator", "type": "prompt"})
    SPEND_USD_TOTAL.inc(0.045, {"workspace_id": "ws-alpha"})
    VOICE_LATENCY_SECONDS.observe(0.35)
    TASK_DURATION_SECONDS.observe(4.2, {"status": "completed"})
    MCP_SERVER_HEALTH.set(1.0, {"server_name": "filesystem"})

    mock_res = MagicMock()
    mock_res.check = AsyncMock()
    mock_res.verify_runtime_role = AsyncMock()
    mock_res.close = AsyncMock()

    with patch("app.main.Resources", return_value=mock_res):
        app = create_app(make_settings(), auth_service=MagicMock())
        client = TestClient(app)

        # 1. Trigger request with W3C traceparent header
        trace_header = "00-4bf92f3577b34da6a3ce929d0e0e4736-00f067aa0ba902b7-01"
        resp = client.get("/api/v1/healthz", headers={"traceparent": trace_header})
        assert resp.status_code == 200
        assert "traceparent" in resp.headers
        # Assert trace_id is preserved from incoming header
        assert "4bf92f3577b34da6a3ce929d0e0e4736" in resp.headers["traceparent"]

        # 2. Scrape /metrics endpoint
        metrics_resp = client.get("/metrics")
        assert metrics_resp.status_code == 200
        assert "text/plain" in metrics_resp.headers["content-type"]
        assert "version=0.0.4" in metrics_resp.headers["content-type"]

        body = metrics_resp.text
        assert "# HELP maya_token_usage_total" in body
        assert "# TYPE maya_token_usage_total counter" in body
        assert 'maya_token_usage_total{alias="orchestrator",type="prompt"} 120.0' in body
        assert 'maya_mcp_server_health{server_name="filesystem"} 1.0' in body
        assert "# HELP maya_voice_latency_seconds" in body
        assert "maya_http_requests_total" in body
