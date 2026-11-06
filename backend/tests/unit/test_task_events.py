"""Unit tests for Redis task events: schema validation, channel naming, and publisher."""

import json
from datetime import UTC, datetime
from unittest.mock import AsyncMock
from uuid import uuid4

import pytest
from pydantic import ValidationError

from app.tasks.events import (
    DEFAULT_EVENT_CHANNEL_PREFIX,
    TaskEvent,
    TaskEventPublisher,
    TaskEventType,
    get_event_channel,
    get_task_channel,
)


def test_task_channel_naming() -> None:
    assert DEFAULT_EVENT_CHANNEL_PREFIX == "maya:events"
    ws_id = "ws_test_123"
    assert get_task_channel(ws_id) == "maya:events:ws_test_123"
    assert get_event_channel(ws_id) == "maya:events:ws_test_123"

    ws_uuid = uuid4()
    assert get_task_channel(ws_uuid) == f"maya:events:{ws_uuid}"
    assert get_event_channel(ws_uuid) == f"maya:events:{ws_uuid}"

    # Custom prefix support (e.g. maya:tasks compatibility)
    assert get_task_channel(ws_id, prefix="maya:tasks") == "maya:tasks:ws_test_123"


def test_task_event_schema_defaults() -> None:
    before = datetime.now(UTC)
    event = TaskEvent(
        event_type=TaskEventType.PLANNING_STARTED,
        session_id="sess_1",
        workspace_id="ws_1",
    )
    after = datetime.now(UTC)

    assert event.event_type == "planning_started"
    assert event.session_id == "sess_1"
    assert event.workspace_id == "ws_1"
    assert event.user_id is None
    assert event.payload == {}
    assert before <= event.timestamp <= after


def test_task_event_with_user_id() -> None:
    user_id = str(uuid4())
    event = TaskEvent(
        event_type=TaskEventType.TASK_PLAN_CREATED,
        session_id="sess_1",
        workspace_id="ws_1",
        user_id=user_id,
        payload={"plan_items": 3},
    )
    assert event.event_type == "task_plan_created"
    assert event.user_id == user_id
    dumped = json.loads(event.model_dump_json())
    assert dumped["user_id"] == user_id


def test_task_event_json_serialization() -> None:
    event = TaskEvent(
        event_type=TaskEventType.SUBTASK_DISPATCHED,
        session_id="sess_abc",
        workspace_id="ws_xyz",
        payload={"subtask_id": "t1", "agent": "research"},
    )
    dumped = event.model_dump_json()
    data = json.loads(dumped)

    assert data["event_type"] == "subtask_dispatched"
    assert data["session_id"] == "sess_abc"
    assert data["workspace_id"] == "ws_xyz"
    assert data["payload"]["subtask_id"] == "t1"
    assert "timestamp" in data


@pytest.mark.parametrize("event_type", ["unknown", "", "TASK_COMPLETED"])
def test_task_event_rejects_unknown_event_types(event_type: str) -> None:
    with pytest.raises(ValidationError):
        TaskEvent(event_type=event_type, session_id="s", workspace_id="w")


def test_task_event_rejects_naive_timestamps() -> None:
    with pytest.raises(ValidationError, match="timezone-aware"):
        TaskEvent(
            event_type=TaskEventType.TASK_COMPLETED,
            session_id="s",
            workspace_id="w",
            timestamp=datetime(2024, 1, 2),
        )


def test_task_event_normalizes_aware_timestamps_to_utc() -> None:
    event = TaskEvent(
        event_type=TaskEventType.TASK_COMPLETED,
        session_id="s",
        workspace_id="w",
        timestamp=datetime.fromisoformat("2024-01-02T08:00:00+05:30"),
    )

    assert event.timestamp == datetime.fromisoformat("2024-01-02T02:30:00+00:00")


@pytest.mark.asyncio
async def test_task_event_publisher_publishes_to_correct_channel() -> None:
    mock_redis = AsyncMock()
    mock_redis.publish.return_value = 1

    publisher = TaskEventPublisher(mock_redis)
    event = TaskEvent(
        event_type=TaskEventType.TASK_COMPLETED,
        session_id="s1",
        workspace_id="ws_99",
        payload={"result": "ok"},
    )

    count = await publisher.publish(event)
    assert count == 1

    mock_redis.publish.assert_called_once()
    channel, message = mock_redis.publish.call_args[0]
    assert channel == "maya:events:ws_99"
    payload = json.loads(message)
    assert payload["event_type"] == "task_completed"
    assert payload["session_id"] == "s1"
    assert payload["payload"]["result"] == "ok"


@pytest.mark.asyncio
async def test_task_event_publisher_configurable_channel_prefix() -> None:
    mock_redis = AsyncMock()
    mock_redis.publish.return_value = 1

    publisher = TaskEventPublisher(mock_redis, channel_prefix="maya:tasks")
    event = TaskEvent(
        event_type=TaskEventType.TASK_COMPLETED,
        session_id="s1",
        workspace_id="ws_99",
    )
    await publisher.publish(event)
    channel = mock_redis.publish.call_args[0][0]
    assert channel == "maya:tasks:ws_99"


@pytest.mark.asyncio
async def test_task_event_publisher_helpers() -> None:
    mock_redis = AsyncMock()
    mock_redis.publish.return_value = 1
    publisher = TaskEventPublisher(mock_redis)

    # 1. task_plan_created
    await publisher.publish_task_plan_created("s", "w", {"instruction": "test"})
    # 2. subtask_dispatched
    await publisher.publish_subtask_dispatched("s", "w", "sub_1", "coding")
    # 3. subtask_status_changed
    await publisher.publish_subtask_status_changed("s", "w", "sub_1", "completed")
    # 4. task_paused_approval
    await publisher.publish_task_paused_approval("s", "w", "sub_2", {"action": "email"})
    # 5. task_completed
    await publisher.publish_task_completed("s", "w", {"summary": "done"})
    # 6. task_failed
    await publisher.publish_task_failed("s", "w", "Rate limit exceeded")
    # 7. planning_started (alias)
    await publisher.publish_planning_started("s", "w", {"instruction": "test_alias"})
    # 8. awaiting_approval (alias)
    await publisher.publish_awaiting_approval("s", "w", "sub_3", {"action": "file"})

    assert mock_redis.publish.call_count == 8

    events = [json.loads(call[0][1]) for call in mock_redis.publish.call_args_list]
    assert events[0]["event_type"] == TaskEventType.TASK_PLAN_CREATED
    assert events[1]["event_type"] == TaskEventType.SUBTASK_DISPATCHED
    assert events[1]["payload"]["subtask_id"] == "sub_1"
    assert events[2]["event_type"] == TaskEventType.SUBTASK_STATUS_CHANGED
    assert events[2]["payload"]["subtask_id"] == "sub_1"
    assert events[2]["payload"]["status"] == "completed"
    assert events[3]["event_type"] == TaskEventType.TASK_PAUSED_APPROVAL
    assert events[3]["payload"]["subtask_id"] == "sub_2"
    assert events[4]["event_type"] == TaskEventType.TASK_COMPLETED
    assert events[5]["event_type"] == TaskEventType.TASK_FAILED
    assert events[5]["payload"]["error"] == "Rate limit exceeded"
    assert events[6]["event_type"] == TaskEventType.PLANNING_STARTED
    assert events[7]["event_type"] == TaskEventType.AWAITING_APPROVAL


@pytest.mark.asyncio
async def test_event_helpers_do_not_allow_payload_to_override_trusted_fields() -> None:
    mock_redis = AsyncMock()
    publisher = TaskEventPublisher(mock_redis)

    await publisher.publish_subtask_dispatched(
        "session-trusted",
        "workspace-trusted",
        "subtask-trusted",
        "coding",
        {"subtask_id": "subtask-untrusted", "assigned_agent": "email"},
    )
    await publisher.publish_subtask_status_changed(
        "session-trusted",
        "workspace-trusted",
        "subtask-trusted",
        "in_progress",
        {"subtask_id": "subtask-untrusted", "status": "completed"},
    )
    await publisher.publish_task_failed(
        "session-trusted",
        "workspace-trusted",
        "trusted failure",
        {"error": "overridden failure"},
    )
    await publisher.publish_awaiting_approval(
        "session-trusted",
        "workspace-trusted",
        "approval-subtask-trusted",
        {"subtask_id": "approval-subtask-untrusted"},
    )

    dispatched = json.loads(mock_redis.publish.call_args_list[0].args[1])
    status_changed = json.loads(mock_redis.publish.call_args_list[1].args[1])
    failed = json.loads(mock_redis.publish.call_args_list[2].args[1])
    approval = json.loads(mock_redis.publish.call_args_list[3].args[1])

    assert dispatched["session_id"] == "session-trusted"
    assert dispatched["workspace_id"] == "workspace-trusted"
    assert dispatched["payload"]["subtask_id"] == "subtask-trusted"
    assert dispatched["payload"]["assigned_agent"] == "coding"

    assert status_changed["session_id"] == "session-trusted"
    assert status_changed["workspace_id"] == "workspace-trusted"
    assert status_changed["payload"]["subtask_id"] == "subtask-trusted"
    assert status_changed["payload"]["status"] == "in_progress"

    assert failed["session_id"] == "session-trusted"
    assert failed["workspace_id"] == "workspace-trusted"
    assert failed["payload"]["error"] == "trusted failure"

    assert approval["payload"]["subtask_id"] == "approval-subtask-trusted"
