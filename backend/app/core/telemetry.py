"""OpenTelemetry Distributed Tracing and W3C Trace Context Implementation.

Provides W3C traceparent parsing/generation, hierarchical span creation,
context preservation across async tasks, and span decorators for LangGraph,
Model Gateway, and MCP tool dispatches (Task P16-T002, Decision D12).
"""

from __future__ import annotations

import functools
import logging
import re
import secrets
import time
from collections.abc import AsyncIterator, Callable, Iterator
from contextlib import asynccontextmanager, contextmanager
from contextvars import ContextVar
from dataclasses import dataclass, field
from typing import Any, ParamSpec, TypeVar

logger = logging.getLogger("maya.telemetry")

# W3C Trace Context regex: version-trace_id-parent_id-trace_flags
W3C_TRACEPARENT_PATTERN = re.compile(
    r"^([0-9a-f]{2})-([0-9a-f]{32})-([0-9a-f]{16})-([0-9a-f]{2})$", re.IGNORECASE
)

P = ParamSpec("P")
R = TypeVar("R")


@dataclass(frozen=True)
class TraceContext:
    """Immutable W3C distributed trace context."""

    trace_id: str
    span_id: str
    trace_flags: str = "01"  # "01" indicates sampled
    trace_state: str = ""

    def to_traceparent(self) -> str:
        """Format as a W3C traceparent header value."""
        return f"00-{self.trace_id.lower()}-{self.span_id.lower()}-{self.trace_flags.lower()}"

    @classmethod
    def from_traceparent(cls, header: str | None) -> TraceContext | None:
        """Parse incoming W3C traceparent HTTP/WebSocket header."""
        if not header:
            return None
        match = W3C_TRACEPARENT_PATTERN.match(header.strip())
        if not match:
            return None
        version, trace_id, parent_id, trace_flags = match.groups()
        # Version ff is explicitly invalid in W3C spec
        if version == "ff" or trace_id == "0" * 32 or parent_id == "0" * 16:
            return None
        return cls(trace_id=trace_id, span_id=parent_id, trace_flags=trace_flags)

    @classmethod
    def generate(cls, parent: TraceContext | None = None) -> TraceContext:
        """Generate a new TraceContext, preserving trace_id if parent is provided."""
        trace_id = parent.trace_id if parent is not None else secrets.token_hex(16)
        span_id = secrets.token_hex(8)
        flags = parent.trace_flags if parent is not None else "01"
        return cls(trace_id=trace_id, span_id=span_id, trace_flags=flags)


@dataclass
class Span:
    """Represents an active or completed OpenTelemetry-compatible span."""

    name: str
    context: TraceContext
    parent_span_id: str | None = None
    start_time: float = field(default_factory=time.time)
    end_time: float | None = None
    attributes: dict[str, Any] = field(default_factory=dict)
    events: list[dict[str, Any]] = field(default_factory=list)
    status: str = "OK"  # "OK" or "ERROR"
    status_description: str | None = None

    @property
    def trace_id(self) -> str:
        return self.context.trace_id

    @property
    def span_id(self) -> str:
        return self.context.span_id

    @property
    def duration_ms(self) -> float:
        if self.end_time is None:
            return (time.time() - self.start_time) * 1000.0
        return (self.end_time - self.start_time) * 1000.0

    def set_attribute(self, key: str, value: Any) -> None:
        """Assign attribute to span."""
        self.attributes[key] = value

    def add_event(self, name: str, attributes: dict[str, Any] | None = None) -> None:
        """Add a timestamped event to this span."""
        self.events.append(
            {
                "name": name,
                "timestamp": time.time(),
                "attributes": attributes or {},
            }
        )

    def set_status(self, status: str, description: str | None = None) -> None:
        """Set final span execution status."""
        self.status = status
        self.status_description = description

    def finish(self) -> None:
        """Mark span as finished."""
        if self.end_time is None:
            self.end_time = time.time()


# Context variable tracking the current active span in the async context
_current_span_var: ContextVar[Span | None] = ContextVar("maya_current_span", default=None)


class Tracer:
    """In-process OpenTelemetry tracer maintaining active span context."""

    def __init__(self, service_name: str = "maya-service") -> None:
        self.service_name = service_name
        self.recorded_spans: list[Span] = []

    def get_current_span(self) -> Span | None:
        """Return the current active span in the executing context."""
        return _current_span_var.get()

    def get_current_trace_id(self) -> str | None:
        """Return current trace ID or None if not within a trace."""
        span = self.get_current_span()
        return span.trace_id if span is not None else None

    def start_span(
        self,
        name: str,
        *,
        parent: TraceContext | Span | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Span:
        """Start a new span, deriving context from parent or ambient context."""
        parent_context: TraceContext | None = None
        parent_span_id: str | None = None

        if isinstance(parent, Span):
            parent_context = parent.context
            parent_span_id = parent.span_id
        elif isinstance(parent, TraceContext):
            parent_context = parent
            parent_span_id = parent.span_id
        else:
            current = self.get_current_span()
            if current is not None:
                parent_context = current.context
                parent_span_id = current.span_id

        context = TraceContext.generate(parent=parent_context)
        span = Span(
            name=name,
            context=context,
            parent_span_id=parent_span_id,
            attributes=attributes or {},
        )
        span.set_attribute("service.name", self.service_name)
        return span

    @contextmanager
    def span(
        self,
        name: str,
        *,
        parent: TraceContext | Span | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> Iterator[Span]:
        """Synchronous context manager for span lifecycle."""
        span = self.start_span(name, parent=parent, attributes=attributes)
        token = _current_span_var.set(span)
        try:
            yield span
        except Exception as exc:
            span.set_status("ERROR", str(exc))
            span.add_event("exception", {"exception.type": type(exc).__name__, "message": str(exc)})
            raise
        finally:
            span.finish()
            self.recorded_spans.append(span)
            _current_span_var.reset(token)

    @asynccontextmanager
    async def async_span(
        self,
        name: str,
        *,
        parent: TraceContext | Span | None = None,
        attributes: dict[str, Any] | None = None,
    ) -> AsyncIterator[Span]:
        """Asynchronous context manager for span lifecycle."""
        span = self.start_span(name, parent=parent, attributes=attributes)
        token = _current_span_var.set(span)
        try:
            yield span
        except Exception as exc:
            span.set_status("ERROR", str(exc))
            span.add_event("exception", {"exception.type": type(exc).__name__, "message": str(exc)})
            raise
        finally:
            span.finish()
            self.recorded_spans.append(span)
            _current_span_var.reset(token)


# Global tracer instance
tracer = Tracer("maya-backend")


def trace_span(
    name: str | None = None,
    *,
    attributes: dict[str, Any] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to trace synchronous or asynchronous function execution."""

    def decorator(func: Callable[P, R]) -> Callable[P, R]:
        span_name = name or func.__name__

        @functools.wraps(func)
        def sync_wrapper(*args: P.args, **kwargs: P.kwargs) -> R:
            with tracer.span(span_name, attributes=attributes):
                return func(*args, **kwargs)

        @functools.wraps(func)
        async def async_wrapper(*args: P.args, **kwargs: P.kwargs) -> Any:
            async with tracer.async_span(span_name, attributes=attributes):
                return await func(*args, **kwargs)  # type: ignore[misc]

        import asyncio

        if asyncio.iscoroutinefunction(func):
            return async_wrapper  # type: ignore[return-value]
        return sync_wrapper

    return decorator


def trace_langgraph_node(
    node_name: str,
    subtask_id: str | None = None,
    *,
    attributes: dict[str, Any] | None = None,
) -> Callable[[Callable[P, R]], Callable[P, R]]:
    """Decorator to record LangGraph node execution within a distributed trace."""
    node_attrs: dict[str, Any] = {
        "langgraph.node": node_name,
        "component": "langgraph_orchestrator",
    }
    if subtask_id:
        node_attrs["subtask.id"] = subtask_id
    if attributes:
        node_attrs.update(attributes)

    return trace_span(f"langgraph.node:{node_name}", attributes=node_attrs)


def trace_model_gateway_call(
    alias: str,
    prompt_tokens: int = 0,
    candidate_tokens: int = 0,
    spend_usd: float = 0.0,
    *,
    model_id: str | None = None,
) -> Span:
    """Record an executed Model Gateway call on the active span or create a span."""
    current = tracer.get_current_span()
    span = current if current is not None else tracer.start_span(f"gateway.call:{alias}")
    span.set_attribute("gen_ai.system", "google_genai")
    span.set_attribute("gen_ai.request.model_alias", alias)
    if model_id:
        span.set_attribute("gen_ai.response.model", model_id)
    span.set_attribute("gen_ai.usage.prompt_tokens", prompt_tokens)
    span.set_attribute("gen_ai.usage.completion_tokens", candidate_tokens)
    span.set_attribute("gen_ai.usage.total_tokens", prompt_tokens + candidate_tokens)
    span.set_attribute("gen_ai.usage.cost_usd", spend_usd)
    span.add_event(
        "model_gateway_completed",
        {
            "alias": alias,
            "tokens": prompt_tokens + candidate_tokens,
            "cost_usd": spend_usd,
        },
    )
    if current is None:
        span.finish()
        tracer.recorded_spans.append(span)
    return span


def trace_mcp_tool_dispatch(
    tool_name: str,
    server_name: str,
    status: str = "success",
    *,
    duration_ms: float = 0.0,
) -> Span:
    """Record an MCP tool dispatch on the active span or create an audit span."""
    current = tracer.get_current_span()
    span_name = f"mcp.tool:{server_name}.{tool_name}"
    span = current if current is not None else tracer.start_span(span_name)
    span.set_attribute("mcp.tool_name", tool_name)
    span.set_attribute("mcp.server_name", server_name)
    span.set_attribute("mcp.status", status)
    if duration_ms > 0:
        span.set_attribute("mcp.duration_ms", duration_ms)
    span.add_event(
        "mcp_dispatch_completed",
        {"tool": tool_name, "server": server_name, "status": status},
    )
    if current is None:
        span.finish()
        tracer.recorded_spans.append(span)
    return span
