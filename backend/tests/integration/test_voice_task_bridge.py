"""Integration tests for VoiceEventBridge subscribing to Redis task events."""

import asyncio
from uuid import uuid4

import pytest

from app.tasks.events import TaskEvent, TaskEventType
from app.voice.event_bridge import VoiceEventBridge, format_spoken_task_update


def test_format_spoken_task_update() -> None:
    # 1. Plan created
    plan_text = format_spoken_task_update(
        TaskEventType.TASK_PLAN_CREATED,
        {"intent": "Research AI", "subtask_count": 3},
    )
    assert "plan with 3 steps" in plan_text
    assert "Research AI" in plan_text

    # 2. Subtask dispatched
    dispatched_text = format_spoken_task_update(
        TaskEventType.SUBTASK_DISPATCHED,
        {"description": "Fetching paper PDF"},
    )
    assert "Starting step: Fetching paper PDF" in dispatched_text

    # 3. Task paused for approval
    approval_text = format_spoken_task_update(
        TaskEventType.TASK_PAUSED_APPROVAL,
        {"action_description": "Send external email to client"},
    )
    assert "approval for Send external email to client in the web interface" in approval_text

    # 4. Task completed
    completed_text = format_spoken_task_update(
        TaskEventType.TASK_COMPLETED,
        {"summary": "All deliverables verified."},
    )
    assert "completed successfully" in completed_text

    # 5. Task failed
    failed_text = format_spoken_task_update(
        TaskEventType.TASK_FAILED,
        {"error": "Rate limit exceeded"},
    )
    assert "encountered an issue: Rate limit exceeded" in failed_text


@pytest.mark.asyncio
async def test_voice_event_bridge_queues_active_task_events() -> None:
    workspace_id = uuid4()
    bridge = VoiceEventBridge(workspace_id=workspace_id)
    task_id_1 = str(uuid4())
    task_id_2 = str(uuid4())

    bridge.track_task(task_id_1)
    assert bridge.is_tracking(task_id_1) is True
    assert bridge.is_tracking(task_id_2) is False

    # Simulate event for tracked task
    event_1 = TaskEvent(
        event_type=TaskEventType.TASK_PLAN_CREATED,
        session_id=str(uuid4()),
        workspace_id=str(workspace_id),
        payload={"task_id": task_id_1, "intent": "Sync calendar", "subtask_count": 2},
    )
    await bridge.handle_event(event_1)

    # Simulate event for untracked task (should be ignored)
    event_2 = TaskEvent(
        event_type=TaskEventType.TASK_PLAN_CREATED,
        session_id=str(uuid4()),
        workspace_id=str(workspace_id),
        payload={"task_id": task_id_2, "intent": "Other task", "subtask_count": 5},
    )
    await bridge.handle_event(event_2)

    # Pop message from queue
    announcement = await asyncio.wait_for(bridge.get_next_announcement(), timeout=1.0)
    assert announcement.task_id == task_id_1
    assert "Sync calendar" in announcement.spoken_text

    # Queue should now be empty
    assert bridge.announcement_count == 0


@pytest.mark.asyncio
async def test_voice_event_bridge_untrack_and_shutdown() -> None:
    workspace_id = uuid4()
    bridge = VoiceEventBridge(workspace_id=workspace_id)
    task_id = str(uuid4())

    bridge.track_task(task_id)
    bridge.untrack_task(task_id)
    assert bridge.is_tracking(task_id) is False

    await bridge.close()
