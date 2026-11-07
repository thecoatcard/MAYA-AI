"""MCP Process Supervisor, Health Monitoring, and Fallback Routing (Phase 08)."""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from uuid import UUID

from app.mcp.transports.base import BaseTransport

logger = logging.getLogger(__name__)


class ServerHealthStatus(StrEnum):
    """Health status states for an MCP server process."""

    HEALTHY = "healthy"
    DEGRADED = "degraded"
    DEAD = "dead"


FALLBACK_ROUTES: dict[str, str] = {
    "github": "local_git",
    "brave": "gemini_search",
}


@dataclass(frozen=True, slots=True)
class SupervisorConfig:
    """Configuration for process supervision heartbeats and restart backoff."""

    ping_interval_seconds: float = 15.0
    ping_timeout_seconds: float = 5.0
    max_consecutive_failures: int = 3
    backoff_intervals: tuple[float, ...] = (1.0, 2.0, 4.0, 8.0)
    sigkill_timeout_seconds: float = 5.0


class ProcessSupervisor:
    """Supervises an MCP server's transport connection and health lifecycle."""

    def __init__(
        self,
        server_id: str | UUID,
        name: str,
        transport: BaseTransport,
        config: SupervisorConfig | None = None,
        on_status_change: (
            Callable[[str, ServerHealthStatus, ServerHealthStatus], None] | None
        ) = None,
    ) -> None:
        self.server_id = str(server_id)
        self.name = name
        self.transport = transport
        self.config = config or SupervisorConfig()
        self.on_status_change = on_status_change

        self._health_status: ServerHealthStatus = ServerHealthStatus.HEALTHY
        self._consecutive_failures: int = 0
        self._last_ping_at: datetime | None = None
        self._restart_count: int = 0
        self._ping_task: asyncio.Task[None] | None = None
        self._stopped: bool = False

    @property
    def health_status(self) -> ServerHealthStatus:
        return self._health_status

    @property
    def consecutive_failures(self) -> int:
        return self._consecutive_failures

    @property
    def last_ping_at(self) -> datetime | None:
        return self._last_ping_at

    @property
    def restart_count(self) -> int:
        return self._restart_count

    def get_fallback_server(self) -> str | None:
        """Return fallback adapter if this server is degraded or dead."""
        if self._health_status in (ServerHealthStatus.DEGRADED, ServerHealthStatus.DEAD):
            return FALLBACK_ROUTES.get(self.name)
        return None

    def _set_status(self, new_status: ServerHealthStatus) -> None:
        old_status = self._health_status
        if old_status != new_status:
            self._health_status = new_status
            logger.warning(
                "MCP Server %s (%s) health transitioned: %s -> %s",
                self.name,
                self.server_id,
                old_status.value,
                new_status.value,
            )
            if self.on_status_change:
                try:
                    self.on_status_change(self.server_id, old_status, new_status)
                except Exception as exc:  # noqa: BLE001
                    logger.error("Error in on_status_change callback: %s", exc)

    async def start(self) -> None:
        """Start the supervisor heartbeat loop."""
        self._stopped = False
        if not self.transport.is_running:
            await self.transport.start()
        self._ping_task = asyncio.create_task(self._ping_loop())

    async def stop(self) -> None:
        """Stop supervisor and gracefully terminate transport."""
        self._stopped = True
        if self._ping_task and not self._ping_task.done():
            self._ping_task.cancel()
            try:
                await self._ping_task
            except asyncio.CancelledError:
                pass
        await self.transport.close()

    async def _ping_loop(self) -> None:
        """Heartbeat loop executing periodic ping requests with backoff recovery."""
        while not self._stopped:
            try:
                await asyncio.sleep(self.config.ping_interval_seconds)
                if self._stopped:
                    break

                # Send ping
                await self.transport.send_request("ping", timeout=self.config.ping_timeout_seconds)

                # Ping succeeded
                self._consecutive_failures = 0
                self._last_ping_at = datetime.now(UTC)
                if self._health_status != ServerHealthStatus.HEALTHY:
                    self._restart_count = 0
                    self._set_status(ServerHealthStatus.HEALTHY)

            except asyncio.CancelledError:
                break
            except Exception as exc:  # noqa: BLE001
                self._consecutive_failures += 1
                logger.warning(
                    "MCP Server %s (%s) ping failed (%d/%d): %s",
                    self.name,
                    self.server_id,
                    self._consecutive_failures,
                    self.config.max_consecutive_failures,
                    exc,
                )

                if self._consecutive_failures >= self.config.max_consecutive_failures:
                    if self._health_status == ServerHealthStatus.HEALTHY:
                        self._set_status(ServerHealthStatus.DEGRADED)

                    # Trigger restart attempt if within backoff limits
                    if self._restart_count < len(self.config.backoff_intervals):
                        delay = self.config.backoff_intervals[self._restart_count]
                        self._restart_count += 1
                        logger.info(
                            "Attempting restart %d/%d for MCP Server %s in %.2fs",
                            self._restart_count,
                            len(self.config.backoff_intervals),
                            self.name,
                            delay,
                        )
                        await asyncio.sleep(delay)
                        if self._stopped:
                            break
                        try:
                            await self.transport.close()
                            await self.transport.start()
                        except Exception as restart_exc:  # noqa: BLE001
                            logger.error(
                                "MCP Server %s restart %d failed: %s",
                                self.name,
                                self._restart_count,
                                restart_exc,
                            )
                    else:
                        logger.error(
                            "MCP Server %s exhausted all %d restart attempts. Marking DEAD.",
                            self.name,
                            len(self.config.backoff_intervals),
                        )
                        self._set_status(ServerHealthStatus.DEAD)
                        break
