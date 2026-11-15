"""Unit tests for MCP Process Supervisor and Health Monitoring (Phase 08)."""

from __future__ import annotations

import asyncio

import pytest

from app.mcp.supervisor import (
    FALLBACK_ROUTES,
    ProcessSupervisor,
    ServerHealthStatus,
    SupervisorConfig,
)
from app.mcp.transports.base import BaseTransport, MCPTimeoutError, MCPTransportError


class MockTransport(BaseTransport):
    """Controllable mock transport for supervisor testing."""

    def __init__(self) -> None:
        super().__init__()
        self.ping_responses: list[dict[str, object] | Exception] = []
        self.ping_count = 0
        self.closed = False
        self.start_count = 0
        self._alive = True

    async def start(self) -> None:
        self.start_count += 1
        self._alive = True

    async def stop(self) -> None:
        self.closed = True
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive

    async def send_request(
        self,
        method: str,
        params: dict[str, object] | None = None,
        timeout: float = 30.0,  # noqa: ASYNC109
    ) -> dict[str, object]:
        if method == "ping":
            self.ping_count += 1
            if self.ping_responses:
                resp = self.ping_responses.pop(0)
                if isinstance(resp, Exception):
                    raise resp
                return resp
            return {"status": "ok"}
        return {}

    async def send_notification(self, method: str, params: dict[str, object] | None = None) -> None:
        pass

    async def close(self) -> None:
        self.closed = True
        self._is_running = False


@pytest.mark.asyncio
async def test_supervisor_initialization() -> None:
    transport = MockTransport()
    supervisor = ProcessSupervisor(
        server_id="test-srv-1",
        name="github",
        transport=transport,
        config=SupervisorConfig(ping_interval_seconds=0.1, ping_timeout_seconds=0.05),
    )

    assert supervisor.health_status == ServerHealthStatus.HEALTHY
    assert supervisor.consecutive_failures == 0
    assert supervisor.last_ping_at is None
    assert supervisor.restart_count == 0


@pytest.mark.asyncio
async def test_supervisor_successful_ping_heartbeat() -> None:
    transport = MockTransport()
    transport.ping_responses = [{"status": "ok"}, {"status": "pong"}]

    supervisor = ProcessSupervisor(
        server_id="test-srv-1",
        name="github",
        transport=transport,
        config=SupervisorConfig(ping_interval_seconds=0.05, ping_timeout_seconds=0.05),
    )

    await supervisor.start()
    await asyncio.sleep(0.12)
    await supervisor.stop()

    assert supervisor.health_status == ServerHealthStatus.HEALTHY
    assert supervisor.consecutive_failures == 0
    assert supervisor.last_ping_at is not None
    assert transport.ping_count >= 1


@pytest.mark.asyncio
async def test_supervisor_three_strikes_degrades() -> None:
    transport = MockTransport()
    # 3 failures in a row
    transport.ping_responses = [
        MCPTimeoutError("timeout 1"),
        MCPTimeoutError("timeout 2"),
        MCPTimeoutError("timeout 3"),
    ]

    status_changes: list[ServerHealthStatus] = []

    def on_status_change(srv_id: str, old: ServerHealthStatus, new: ServerHealthStatus) -> None:
        status_changes.append(new)

    supervisor = ProcessSupervisor(
        server_id="test-srv-1",
        name="github",
        transport=transport,
        config=SupervisorConfig(
            ping_interval_seconds=0.02,
            ping_timeout_seconds=0.02,
            max_consecutive_failures=3,
        ),
        on_status_change=on_status_change,
    )

    await supervisor.start()
    await asyncio.sleep(0.1)
    await supervisor.stop()

    assert supervisor.consecutive_failures >= 3
    assert supervisor.health_status in (ServerHealthStatus.DEGRADED, ServerHealthStatus.DEAD)
    assert ServerHealthStatus.DEGRADED in status_changes


@pytest.mark.asyncio
async def test_supervisor_exponential_backoff_and_death() -> None:
    transport = MockTransport()
    # Continuous failures
    transport.ping_responses = [MCPTransportError("crashed")] * 10

    supervisor = ProcessSupervisor(
        server_id="test-srv-1",
        name="github",
        transport=transport,
        config=SupervisorConfig(
            ping_interval_seconds=0.01,
            ping_timeout_seconds=0.01,
            max_consecutive_failures=1,
            backoff_intervals=(0.02, 0.04),
        ),
    )

    await supervisor.start()
    await asyncio.sleep(0.15)
    await supervisor.stop()

    assert supervisor.restart_count >= 2
    assert supervisor.health_status == ServerHealthStatus.DEAD


def test_supervisor_fallback_routing() -> None:
    assert FALLBACK_ROUTES.get("github") == "local_git"
    assert "brave" not in FALLBACK_ROUTES  # Brave Search was removed (2026-11-09)

    supervisor = ProcessSupervisor(
        server_id="test-srv-1",
        name="github",
        transport=MockTransport(),
    )
    supervisor._health_status = ServerHealthStatus.DEGRADED
    assert supervisor.get_fallback_server() == "local_git"

    supervisor._health_status = ServerHealthStatus.HEALTHY
    assert supervisor.get_fallback_server() is None
