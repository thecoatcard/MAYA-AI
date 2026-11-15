"""Prometheus Metrics Exporter and Telemetry Registry.

Implements high-performance, zero-dependency Prometheus text format (version 0.0.4)
counters, gauges, and histograms for token spend, voice latency, and MCP health
(Task P16-T003, Decisions D9, D12).
"""

from __future__ import annotations

import logging
import threading
from collections import defaultdict
from collections.abc import Mapping
from typing import Any

logger = logging.getLogger("maya.metrics")


def _format_labels(labels: dict[str, str]) -> str:
    """Format label dictionary into Prometheus label string."""
    if not labels:
        return ""
    formatted = [f'{k}="{v}"' for k, v in sorted(labels.items())]
    return "{" + ",".join(formatted) + "}"


class Metric:
    """Base Prometheus metric class."""

    def __init__(self, name: str, documentation: str, label_names: list[str] | None = None) -> None:
        self.name = name
        self.documentation = documentation
        self.label_names = sorted(label_names or [])
        self._lock = threading.Lock()

    def _normalize_labels(self, labels: dict[str, Any] | None) -> tuple[tuple[str, str], ...]:
        if not labels:
            return ()
        return tuple(sorted((str(k), str(v)) for k, v in labels.items()))

    def render(self) -> list[str]:
        raise NotImplementedError


class Counter(Metric):
    """Cumulative metric that monotonically increases."""

    def __init__(self, name: str, documentation: str, label_names: list[str] | None = None) -> None:
        super().__init__(name, documentation, label_names)
        self._values: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)

    def inc(self, amount: float = 1.0, labels: dict[str, Any] | None = None) -> None:
        if amount < 0:
            raise ValueError("Counters can only be incremented by non-negative amounts")
        norm = self._normalize_labels(labels)
        with self._lock:
            self._values[norm] += amount

    def get(self, labels: dict[str, Any] | None = None) -> float:
        norm = self._normalize_labels(labels)
        with self._lock:
            return self._values[norm]

    def render(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.documentation}",
            f"# TYPE {self.name} counter",
        ]
        with self._lock:
            if not self._values:
                lines.append(f"{self.name} 0")
            else:
                for norm, val in sorted(self._values.items()):
                    lbl_str = _format_labels(dict(norm))
                    lines.append(f"{self.name}{lbl_str} {val}")
        return lines


class Gauge(Metric):
    """Metric that can arbitrarily increase or decrease."""

    def __init__(self, name: str, documentation: str, label_names: list[str] | None = None) -> None:
        super().__init__(name, documentation, label_names)
        self._values: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)

    def set(self, value: float, labels: dict[str, Any] | None = None) -> None:
        norm = self._normalize_labels(labels)
        with self._lock:
            self._values[norm] = float(value)

    def inc(self, amount: float = 1.0, labels: dict[str, Any] | None = None) -> None:
        norm = self._normalize_labels(labels)
        with self._lock:
            self._values[norm] += amount

    def dec(self, amount: float = 1.0, labels: dict[str, Any] | None = None) -> None:
        norm = self._normalize_labels(labels)
        with self._lock:
            self._values[norm] -= amount

    def get(self, labels: dict[str, Any] | None = None) -> float:
        norm = self._normalize_labels(labels)
        with self._lock:
            return self._values[norm]

    def render(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.documentation}",
            f"# TYPE {self.name} gauge",
        ]
        with self._lock:
            if not self._values:
                lines.append(f"{self.name} 0")
            else:
                for norm, val in sorted(self._values.items()):
                    lbl_str = _format_labels(dict(norm))
                    lines.append(f"{self.name}{lbl_str} {val}")
        return lines


class Histogram(Metric):
    """Samples observations and counts them in configurable buckets."""

    DEFAULT_BUCKETS = (0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0, 10.0)

    def __init__(
        self,
        name: str,
        documentation: str,
        label_names: list[str] | None = None,
        buckets: tuple[float, ...] | list[float] | None = None,
    ) -> None:
        super().__init__(name, documentation, label_names)
        b = sorted(set(buckets or self.DEFAULT_BUCKETS))
        self.buckets = tuple(b)
        # norm_labels -> (bucket_counts, sum, count)
        self._counts: dict[tuple[tuple[str, str], ...], int] = defaultdict(int)
        self._sums: dict[tuple[tuple[str, str], ...], float] = defaultdict(float)
        self._bucket_counts: dict[tuple[tuple[str, str], ...], dict[float, int]] = defaultdict(
            lambda: {bound: 0 for bound in self.buckets}
        )

    def observe(self, value: float, labels: dict[str, Any] | None = None) -> None:
        val = float(value)
        norm = self._normalize_labels(labels)
        with self._lock:
            self._counts[norm] += 1
            self._sums[norm] += val
            b_dict = self._bucket_counts[norm]
            for bound in self.buckets:
                if val <= bound:
                    b_dict[bound] += 1

    def render(self) -> list[str]:
        lines = [
            f"# HELP {self.name} {self.documentation}",
            f"# TYPE {self.name} histogram",
        ]
        with self._lock:
            if not self._counts:
                # Emit empty histogram representation
                for bound in self.buckets:
                    lines.append(f'{self.name}_bucket{{le="{bound}"}} 0')
                lines.append(f'{self.name}_bucket{{le="+Inf"}} 0')
                lines.append(f"{self.name}_sum 0")
                lines.append(f"{self.name}_count 0")
            else:
                for norm in sorted(self._counts.keys()):
                    base_labels = dict(norm)
                    b_dict = self._bucket_counts[norm]
                    count = self._counts[norm]
                    val_sum = self._sums[norm]

                    for bound in self.buckets:
                        lbls = dict(base_labels)
                        lbls["le"] = str(bound)
                        lines.append(f"{self.name}_bucket{_format_labels(lbls)} {b_dict[bound]}")

                    lbls_inf = dict(base_labels)
                    lbls_inf["le"] = "+Inf"
                    lines.append(f"{self.name}_bucket{_format_labels(lbls_inf)} {count}")

                    lines.append(f"{self.name}_sum{_format_labels(base_labels)} {val_sum}")
                    lines.append(f"{self.name}_count{_format_labels(base_labels)} {count}")
        return lines


class PrometheusRegistry:
    """Thread-safe Prometheus metric registry."""

    def __init__(self) -> None:
        self._metrics: dict[str, Metric] = {}
        self._lock = threading.Lock()

    def register(self, metric: Metric) -> None:
        with self._lock:
            self._metrics[metric.name] = metric

    def unregister(self, name: str) -> None:
        with self._lock:
            self._metrics.pop(name, None)

    def generate_latest(self) -> str:
        """Render all registered metrics in Prometheus text format (0.0.4)."""
        output: list[str] = []
        with self._lock:
            for metric in self._metrics.values():
                output.extend(metric.render())
        return "\n".join(output) + "\n"


# Global Prometheus Registry
REGISTRY = PrometheusRegistry()

# 1. Token consumption by model alias and token type (prompt, candidate, cached)
TOKEN_USAGE_TOTAL = Counter(
    "maya_token_usage_total",
    "Cumulative count of tokens consumed through Model Gateway",
    ["alias", "type"],
)
REGISTRY.register(TOKEN_USAGE_TOTAL)

# 2. Cumulative spend USD by workspace
SPEND_USD_TOTAL = Counter(
    "maya_spend_usd_total",
    "Cumulative spend in USD tracked across workspaces",
    ["workspace_id"],
)
REGISTRY.register(SPEND_USD_TOTAL)

# 3. Voice end-to-end latency (seconds) with SLA target of p95 <= 850ms (Decision D12)
VOICE_LATENCY_SECONDS = Histogram(
    "maya_voice_latency_seconds",
    "End-to-end voice latency in seconds (SLA target p95 <= 0.85s)",
    buckets=[0.05, 0.10, 0.15, 0.25, 0.45, 0.60, 0.85, 1.00, 2.00, 5.00],
)
REGISTRY.register(VOICE_LATENCY_SECONDS)

# 4. Task execution duration (seconds) by status
TASK_DURATION_SECONDS = Histogram(
    "maya_task_duration_seconds",
    "Duration of asynchronous task execution in seconds",
    ["status"],
    buckets=[0.5, 1.0, 2.5, 5.0, 10.0, 30.0, 60.0, 120.0, 300.0, 600.0],
)
REGISTRY.register(TASK_DURATION_SECONDS)

# 5. MCP server connectivity health status (1 = healthy, 0 = degraded)
MCP_SERVER_HEALTH = Gauge(
    "maya_mcp_server_health",
    "Health status of registered MCP servers (1=healthy, 0=degraded)",
    ["server_name"],
)
REGISTRY.register(MCP_SERVER_HEALTH)

# 6. HTTP requests count and latency
HTTP_REQUESTS_TOTAL = Counter(
    "maya_http_requests_total",
    "Total incoming HTTP requests by method, route, and status",
    ["method", "endpoint", "status"],
)
REGISTRY.register(HTTP_REQUESTS_TOTAL)

HTTP_REQUEST_DURATION_SECONDS = Histogram(
    "maya_http_request_duration_seconds",
    "HTTP request latency in seconds",
    ["method", "endpoint"],
    buckets=[0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1.0, 2.5, 5.0],
)
REGISTRY.register(HTTP_REQUEST_DURATION_SECONDS)

# 7. Gemini API key pool state
GEMINI_KEY_POOL_SIZE = Gauge(
    "maya_gemini_key_pool_size",
    "Total number of Gemini API keys configured in the key pool",
)
REGISTRY.register(GEMINI_KEY_POOL_SIZE)

GEMINI_KEY_POOL_COOLING = Gauge(
    "maya_gemini_key_pool_cooling",
    "Number of Gemini API keys currently in cooldown",
)
REGISTRY.register(GEMINI_KEY_POOL_COOLING)

GEMINI_KEY_POOL_BAD = Gauge(
    "maya_gemini_key_pool_bad",
    "Number of Gemini API keys marked invalid or unauthorized for the process lifetime",
)
REGISTRY.register(GEMINI_KEY_POOL_BAD)

# Per-key series use the ``key#N`` label only (never key text); at most one series per pool key.
GEMINI_KEY_CALLS_TOTAL = Counter(
    "maya_gemini_key_calls_total",
    "Gemini provider attempts by key label, model alias and outcome (ok or error)",
    label_names=["key", "alias", "outcome"],
)
REGISTRY.register(GEMINI_KEY_CALLS_TOTAL)

GEMINI_KEY_FAILOVERS_TOTAL = Counter(
    "maya_gemini_key_failovers_total",
    "Attempts moved off a key to another key, by the failed key's label and the reason",
    label_names=["key", "reason"],
)
REGISTRY.register(GEMINI_KEY_FAILOVERS_TOTAL)

GEMINI_KEY_COOLDOWNS_TOTAL = Counter(
    "maya_gemini_key_cooldowns_total",
    "Cooldowns or invalidations applied to a key, by label and reason",
    label_names=["key", "reason"],
)
REGISTRY.register(GEMINI_KEY_COOLDOWNS_TOTAL)

# Chat replies that claimed a background task was started when none was (a guard corrects them).
CHAT_FALSE_TASK_CLAIM_TOTAL = Counter(
    "maya_chat_false_task_claim_total",
    "Chat replies that claimed a task was started although no task was created this turn",
)
REGISTRY.register(CHAT_FALSE_TASK_CLAIM_TOTAL)

# Where one chat turn spends its time. ``phase``: recall (memory embedding before the model call),
# first_chunk (model call opened to first chunk), first_delta (turn start to the first text sent)
# and total. ``outcome``: completed, failed or cancelled; recall also uses hit, empty, timeout,
# error and skipped. Labels never carry user content, ids or model text.
CHAT_TURN_PHASE_SECONDS = Histogram(
    "maya_chat_turn_phase_seconds",
    "Seconds a chat turn spends in each phase (recall, first_chunk, first_delta, total)",
    label_names=["phase", "outcome"],
    buckets=[0.1, 0.25, 0.5, 1.0, 2.0, 3.0, 5.0, 8.0, 13.0, 21.0, 34.0, 60.0, 120.0],
)
REGISTRY.register(CHAT_TURN_PHASE_SECONDS)


def route_label(scope: Mapping[str, Any]) -> str:
    """Low-cardinality ``endpoint`` label: the route template, never the raw path (UUIDs)."""
    route = scope.get("route")
    template = getattr(route, "path", None)
    if isinstance(template, str) and template:
        return template
    return "unmatched"


def generate_latest(registry: PrometheusRegistry | None = None) -> str:
    """Generate Prometheus 0.0.4 text output for scraping."""
    reg = registry or REGISTRY
    return reg.generate_latest()


DECISION_CALLS_TOTAL = Counter(
    "maya_decision_calls_total",
    "Gemini decision model calls by purpose and outcome (never content)",
    label_names=["purpose", "outcome"],
)
REGISTRY.register(DECISION_CALLS_TOTAL)
