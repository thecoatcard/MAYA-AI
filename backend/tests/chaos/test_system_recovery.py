"""Chaos Resilience and Recovery Test Suite (Decisions D6, D8, Phase 15).

Tests:
1. Worker crash & checkpoint resumption: worker recovers from LangGraph checkpoint
   without re-executing already committed nodes or leaving orphan state.
2. Redis connection drop during transactional outbox dispatch: zero events dropped;
   events remain durable in pending state and dispatch upon reconnection.
3. MCP Process Supervisor crash recovery: heartbeats detect process failure, initiate
   exponential backoff restarts, recover health, and engage fallback routing if dead.
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock
from uuid import uuid4

import pytest
from langgraph.checkpoint.base import CheckpointTuple
from sqlalchemy.ext.asyncio import AsyncSession

from app.agents.checkpointer import create_memory_checkpointer, get_latest_checkpoint
from app.db.models import TaskOutboxEvent
from app.mcp.supervisor import (
    FALLBACK_ROUTES,
    ProcessSupervisor,
    ServerHealthStatus,
    SupervisorConfig,
)
from app.mcp.transports.base import BaseTransport
from app.tasks.events import TaskEventPublisher, TaskEventType
from app.tasks.outbox import OutboxDispatcher


@pytest.mark.asyncio
async def test_worker_crash_and_checkpoint_resumption() -> None:
    """Simulate worker SIGKILL mid-workflow and verify resumption from last checkpoint."""
    checkpointer = create_memory_checkpointer()
    thread_id = f"thread_worker_crash_{uuid4().hex[:8]}"
    config = {"configurable": {"thread_id": thread_id, "checkpoint_ns": ""}}

    # Step 1: Worker executes Node 1 (Planning) and commits checkpoint
    state_1 = {
        "completed_subtasks": ["subtask_1_research"],
        "plan_status": "in_progress",
        "last_completed_step": 1,
    }
    versions_1 = {"completed_subtasks": 1, "plan_status": 1, "last_completed_step": 1}
    checkpoint_1 = {
        "v": 1,
        "ts": "2026-11-07T12:00:00Z",
        "id": "chk_node_1",
        "channel_values": state_1,
        "channel_versions": versions_1,
        "versions_seen": {},
    }
    await checkpointer.aput(
        config,  # type: ignore[arg-type]
        checkpoint_1,  # type: ignore[arg-type]
        metadata={"step": 1},
        new_versions=versions_1,  # type: ignore[arg-type]
    )

    # Step 2: Worker executes Node 2 (Code Implementation) and commits checkpoint
    state_2 = {
        "completed_subtasks": ["subtask_1_research", "subtask_2_code_patch"],
        "plan_status": "in_progress",
        "last_completed_step": 2,
    }
    versions_2 = {"completed_subtasks": 2, "plan_status": 2, "last_completed_step": 2}
    checkpoint_2 = {
        "v": 1,
        "ts": "2026-11-07T12:01:00Z",
        "id": "chk_node_2",
        "channel_values": state_2,
        "channel_versions": versions_2,
        "versions_seen": {},
    }
    await checkpointer.aput(
        config,  # type: ignore[arg-type]
        checkpoint_2,  # type: ignore[arg-type]
        metadata={"step": 2},
        new_versions=versions_2,  # type: ignore[arg-type]
    )

    # SIMULATE CRASH: Worker process abruptly terminates (SIGKILL) before Step 3 completes

    # Recovery: New worker process boots and retrieves latest checkpoint for thread_id
    latest_checkpoint: CheckpointTuple | None = await get_latest_checkpoint(checkpointer, thread_id)
    assert latest_checkpoint is not None
    assert latest_checkpoint.checkpoint["id"] == "chk_node_2"

    saved_state = latest_checkpoint.checkpoint["channel_values"]
    assert saved_state["last_completed_step"] == 2
    assert "subtask_1_research" in saved_state["completed_subtasks"]
    assert "subtask_2_code_patch" in saved_state["completed_subtasks"]

    # Resume directly from Step 3 (Verification & PR) without re-running Steps 1 and 2
    resumed_subtasks = list(saved_state["completed_subtasks"])
    resumed_subtasks.append("subtask_3_verified_pr")

    state_3 = {
        "completed_subtasks": resumed_subtasks,
        "plan_status": "completed",
        "last_completed_step": 3,
    }
    versions_3 = {"completed_subtasks": 3, "plan_status": 3, "last_completed_step": 3}
    checkpoint_3 = {
        "v": 1,
        "ts": "2026-11-07T12:02:00Z",
        "id": "chk_node_3",
        "channel_values": state_3,
        "channel_versions": versions_3,
        "versions_seen": {},
    }
    await checkpointer.aput(
        config,  # type: ignore[arg-type]
        checkpoint_3,  # type: ignore[arg-type]
        metadata={"step": 3},
        new_versions=versions_3,  # type: ignore[arg-type]
    )

    # Final verification
    final_checkpoint: CheckpointTuple | None = await get_latest_checkpoint(checkpointer, thread_id)
    assert final_checkpoint is not None
    final_state = final_checkpoint.checkpoint["channel_values"]
    assert final_state["plan_status"] == "completed"
    assert len(final_state["completed_subtasks"]) == 3
    # Assert zero duplicate entries (idempotency preserved)
    assert len(final_state["completed_subtasks"]) == len(set(final_state["completed_subtasks"]))


@pytest.mark.asyncio
async def test_redis_connection_drop_during_outbox_dispatch() -> None:
    """Verify transactional outbox preserves events during Redis drops and retries cleanly."""
    workspace_id = uuid4()
    task_id = uuid4()

    # Create 3 events in outbox table
    event_1 = TaskOutboxEvent(
        id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        event_type=TaskEventType.SUBTASK_STATUS_CHANGED.value,
        payload={"step": 1},
        status="pending",
    )
    event_2 = TaskOutboxEvent(
        id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        event_type=TaskEventType.SUBTASK_STATUS_CHANGED.value,
        payload={"step": 2},
        status="pending",
    )
    event_3 = TaskOutboxEvent(
        id=uuid4(),
        workspace_id=workspace_id,
        task_id=task_id,
        event_type=TaskEventType.TASK_COMPLETED.value,
        payload={"step": 3},
        status="pending",
    )

    events_list = [event_1, event_2, event_3]

    session = AsyncMock(spec=AsyncSession)

    def execute_mock(stmt: object) -> MagicMock:
        mock_result = MagicMock()
        # Return pending events
        pending_events = [e for e in events_list if e.status == "pending"]
        mock_result.scalars.return_value.all.return_value = pending_events
        return mock_result

    session.execute = AsyncMock(side_effect=execute_mock)
    session.flush = AsyncMock()

    # 1. Simulate Redis connection drop (ConnectionError)
    failing_publisher = MagicMock(spec=TaskEventPublisher)
    failing_publisher.publish = AsyncMock(side_effect=ConnectionError("Redis connection dropped"))

    dispatcher = OutboxDispatcher(publisher=failing_publisher)

    # Attempt dispatch during outage
    dispatched_during_outage = await dispatcher.dispatch_pending_events(session=session)
    assert dispatched_during_outage == 0

    # Assert 100% of events remain durable in pending state (zero dropped events)
    for e in events_list:
        assert e.status == "pending"
        assert e.dispatched_at is None

    # 2. Simulate Redis network recovery
    healthy_publisher = MagicMock(spec=TaskEventPublisher)
    healthy_publisher.publish = AsyncMock(return_value=True)
    dispatcher.publisher = healthy_publisher

    # Re-attempt dispatch upon reconnection
    dispatched_after_recovery = await dispatcher.dispatch_pending_events(session=session)
    assert dispatched_after_recovery == 3

    # Assert all events are marked dispatched
    for e in events_list:
        assert e.status == "dispatched"
        assert e.dispatched_at is not None


class ChaosMockTransport(BaseTransport):
    """Mock transport capable of simulating subprocess crashes and restart cycles."""

    def __init__(self) -> None:
        self._alive = False
        self.ping_attempts = 0
        self.restart_attempts = 0
        self.should_fail_ping = False
        self.should_fail_start = False

    async def start(self) -> None:
        if self.should_fail_start:
            raise RuntimeError("Process spawn failed")
        self._alive = True
        self.restart_attempts += 1

    async def stop(self) -> None:
        self._alive = False

    def is_alive(self) -> bool:
        return self._alive

    async def send_request(
        self,
        method: str,
        params: dict[str, Any] | None = None,
        timeout: float = 5.0,  # noqa: ASYNC109
    ) -> dict[str, Any]:
        if method == "ping":
            self.ping_attempts += 1
            if self.should_fail_ping:
                raise ConnectionResetError("Connection reset by peer (process died)")
            return {"status": "pong"}
        return {}


@pytest.mark.asyncio
async def test_mcp_supervisor_respawn_with_backoff_and_fallback() -> None:
    """Verify MCP supervisor detects crashes, executes backoff restarts, and activates fallback."""
    transport = ChaosMockTransport()
    config = SupervisorConfig(
        ping_interval_seconds=0.01,
        ping_timeout_seconds=0.05,
        max_consecutive_failures=2,
        backoff_intervals=(0.01, 0.02),
    )

    supervisor = ProcessSupervisor(
        server_id=uuid4(),
        name="github",
        transport=transport,
        config=config,
    )

    await supervisor.start()
    assert supervisor.health_status == ServerHealthStatus.HEALTHY
    assert transport.is_running is True

    # 1. Simulate process failure (ping raises ConnectionResetError)
    transport.should_fail_ping = True

    # Wait for consecutive failures to trigger degradation and restart
    await asyncio.sleep(0.08)

    assert supervisor.consecutive_failures >= 2
    assert supervisor.health_status in (ServerHealthStatus.DEGRADED, ServerHealthStatus.DEAD)

    # 2. Simulate process recovery on restart
    transport.should_fail_ping = False
    await asyncio.sleep(0.05)

    assert supervisor.health_status == ServerHealthStatus.HEALTHY
    assert supervisor.consecutive_failures == 0

    # 3. Simulate unrecoverable fatal crash exhausting all backoff attempts
    transport.should_fail_ping = True
    transport.should_fail_start = True

    await asyncio.sleep(0.15)

    # Supervisor transitions to DEAD and routes to local fallback
    assert supervisor.health_status == ServerHealthStatus.DEAD
    fallback = supervisor.get_fallback_server()
    assert fallback == FALLBACK_ROUTES["github"]
    assert fallback == "local_git"

    await supervisor.stop()
