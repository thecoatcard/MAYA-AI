"""Unit tests for relational projection service logic and parameter construction."""

import json
from datetime import UTC, datetime
from decimal import Decimal
from typing import Any
from unittest.mock import AsyncMock
from uuid import UUID, uuid4

import pytest

from app.agents.state import AgentExecutionState, SubTask
from app.services.projection_service import (
    StateProjectionService,
    map_plan_status_to_task_status,
    resolve_subtask_uuid,
)


def test_map_plan_status_to_task_status() -> None:
    assert map_plan_status_to_task_status("planning", False) == "pending"
    assert map_plan_status_to_task_status("executing", False) == "executing"
    assert map_plan_status_to_task_status("paused", False) == "awaiting_approval"
    assert map_plan_status_to_task_status("executing", True) == "awaiting_approval"
    assert map_plan_status_to_task_status("completed", False) == "completed"
    assert map_plan_status_to_task_status("failed", False) == "failed"


def test_resolve_subtask_uuid_with_valid_uuid() -> None:
    task_id = uuid4()
    sub_uuid = uuid4()
    resolved = resolve_subtask_uuid(task_id, str(sub_uuid))
    assert resolved == sub_uuid


def test_resolve_subtask_uuid_deterministic_fallback() -> None:
    task_id = uuid4()
    string_id = "step_alpha"
    resolved_1 = resolve_subtask_uuid(task_id, string_id)
    resolved_2 = resolve_subtask_uuid(task_id, string_id)
    assert isinstance(resolved_1, UUID)
    assert resolved_1 == resolved_2


@pytest.mark.asyncio
async def test_state_projection_executes_upserts_with_parameters() -> None:
    mock_engine = AsyncMock()
    mock_conn = AsyncMock()

    service = StateProjectionService(mock_engine)

    ws_id = str(uuid4())
    sess_id = str(uuid4())
    usr_id = str(uuid4())

    sub1 = SubTask(
        id="sub_1",
        assigned_agent="research",
        description="Search data",
        dependencies=[],
        status="completed",
        output_result={"found": 1},
    )
    sub2 = SubTask(
        id="sub_2",
        assigned_agent="document",
        description="Format report",
        dependencies=["sub_1"],
        status="in_progress",
    )

    state = AgentExecutionState(
        session_id=sess_id,
        user_id=usr_id,
        workspace_id=ws_id,
        user_instruction="Analyze and report",
        interaction_modality="text",
        active_plan=[sub1, sub2],
        plan_status="executing",
        completed_task_ids=["sub_1"],
        shared_context={"classified_intent": "Analyze"},
        execution_trace=[{"step": "plan"}, {"step": "dispatch"}],
    )

    task_id = await service.project_state(state, connection=mock_conn)
    assert isinstance(task_id, UUID)

    # Verify calls: 1 task upsert + 2 subtask upserts = 3 calls
    assert mock_conn.execute.call_count == 3

    # Check task upsert call parameters
    task_call_args = mock_conn.execute.call_args_list[0][0]
    task_params = task_call_args[1]
    assert task_params["id"] == task_id
    assert task_params["workspace_id"] == UUID(ws_id)
    assert task_params["session_id"] == UUID(sess_id)
    assert task_params["user_id"] == UUID(usr_id)
    assert task_params["status"] == "executing"
    assert task_params["tool_turns"] == 2
    dag_plan = json.loads(task_params["dag_plan"])
    assert len(dag_plan["subtasks"]) == 2

    # Check subtask 1 parameters
    sub1_params = mock_conn.execute.call_args_list[1][0][1]
    assert sub1_params["task_id"] == task_id
    assert sub1_params["assigned_agent"] == "research"
    assert sub1_params["status"] == "completed"
    assert json.loads(sub1_params["output_result"]) == {"found": 1}

    # Check subtask 2 parameters
    sub2_params = mock_conn.execute.call_args_list[2][0][1]
    assert sub2_params["task_id"] == task_id
    assert sub2_params["assigned_agent"] == "document"
    assert sub2_params["status"] == "in_progress"


@pytest.mark.asyncio
async def test_state_projection_honors_explicit_idempotency_key_and_uses_stable_identity() -> None:
    service = StateProjectionService(AsyncMock())
    connection = AsyncMock()
    common = {
        "user_id": "00000000-0000-0000-0000-000000000002",
        "workspace_id": "00000000-0000-0000-0000-000000000003",
        "user_instruction": "Synthetic",
        "interaction_modality": "text",
    }
    plan = [SubTask(id="step_1", assigned_agent="research", description="Synthetic")]
    first_state = AgentExecutionState(
        session_id="00000000-0000-0000-0000-000000000001",
        active_plan=plan,
        **common,
    )
    second_state = AgentExecutionState(
        session_id="00000000-0000-0000-0000-000000000004",
        active_plan=plan,
        **common,
    )
    uppercase_workspace_state = first_state.model_copy(
        update={
            "session_id": "00000000-0000-0000-0000-000000000005",
            "workspace_id": common["workspace_id"].upper(),
        }
    )

    first_id = await service.project_state(
        first_state, idempotency_key="request-123", connection=connection
    )
    first_params = connection.execute.call_args_list[0].args[1]
    first_subtask_id = connection.execute.call_args_list[1].args[1]["id"]
    connection.reset_mock()
    second_id = await service.project_state(
        second_state, idempotency_key="request-123", connection=connection
    )
    second_params = connection.execute.call_args_list[0].args[1]
    second_subtask_id = connection.execute.call_args_list[1].args[1]["id"]
    connection.reset_mock()
    uppercase_id = await service.project_state(
        uppercase_workspace_state,
        idempotency_key="request-123",
        connection=connection,
    )

    assert first_id == second_id
    assert first_id == uppercase_id
    assert first_subtask_id == second_subtask_id
    assert first_params["idempotency_key"] == "request-123"
    assert second_params["idempotency_key"] == "request-123"


@pytest.mark.asyncio
async def test_explicit_idempotency_key_overrides_state_context_key() -> None:
    state = AgentExecutionState(
        session_id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
        workspace_id="00000000-0000-0000-0000-000000000003",
        user_instruction="Synthetic",
        interaction_modality="text",
        shared_context={"idempotency_key": "context-key"},
    )
    connection = AsyncMock()

    await StateProjectionService(AsyncMock()).project_state(
        state, idempotency_key="explicit-key", connection=connection
    )

    task_params = connection.execute.call_args.args[1]
    assert task_params["idempotency_key"] == "explicit-key"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["input_data", "output_result", "verification_proof"])
async def test_state_projection_serializes_nested_json_values(field: str) -> None:
    value = {
        "uuid": UUID("00000000-0000-0000-0000-000000000123"),
        "time": datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
        "amount": Decimal("1.25"),
        "nested": [None, True, 3, {"label": "synthetic"}],
    }
    expected = {
        "uuid": "00000000-0000-0000-0000-000000000123",
        "time": "2024-01-02T03:04:05Z",
        "amount": "1.25",
        "nested": [None, True, 3, {"label": "synthetic"}],
    }
    subtask = SubTask(id="step_1", assigned_agent="research", description="Synthetic").model_copy(
        update={field: value}
    )
    state = AgentExecutionState(
        session_id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
        workspace_id="00000000-0000-0000-0000-000000000003",
        user_instruction="Synthetic",
        interaction_modality="text",
        active_plan=[subtask],
    )
    connection = AsyncMock()
    service = StateProjectionService(AsyncMock())

    await service.project_state(state, connection=connection)

    task_params = connection.execute.call_args_list[0].args[1]
    subtask_params = connection.execute.call_args_list[1].args[1]
    assert json.loads(task_params["dag_plan"])["subtasks"][0][field] == expected
    assert json.loads(subtask_params[field]) == expected


@pytest.mark.asyncio
async def test_state_projection_serializes_json_values_in_classified_intent() -> None:
    state = AgentExecutionState(
        session_id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
        workspace_id="00000000-0000-0000-0000-000000000003",
        user_instruction="Synthetic",
        interaction_modality="text",
        shared_context={"classified_intent": {"at": datetime(2024, 1, 2, tzinfo=UTC)}},
    )
    connection = AsyncMock()
    service = StateProjectionService(AsyncMock())

    await service.project_state(state, connection=connection)

    task_params = connection.execute.call_args.args[1]
    assert json.loads(task_params["dag_plan"])["intent"] == {"at": "2024-01-02T00:00:00Z"}


@pytest.mark.asyncio
@pytest.mark.parametrize("include_uuid", [False, True])
async def test_state_projection_preserves_standard_json_keys(include_uuid: bool) -> None:
    value: dict[str, Any] = {
        "nested": {
            None: "none",
            False: "false",
            3: "int",
            UUID("00000000-0000-0000-0000-000000000124"): "uuid-key",
            Decimal("1.50"): "decimal-key",
            datetime(2024, 1, 2, tzinfo=UTC): "datetime-key",
        }
    }
    if include_uuid:
        value["uuid"] = UUID("00000000-0000-0000-0000-000000000123")
    subtask = SubTask(
        id="step_1", assigned_agent="research", description="Synthetic", input_data=value
    )
    state = AgentExecutionState(
        session_id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
        workspace_id="00000000-0000-0000-0000-000000000003",
        user_instruction="Synthetic",
        interaction_modality="text",
        active_plan=[subtask],
    )
    connection = AsyncMock()
    service = StateProjectionService(AsyncMock())

    await service.project_state(state, connection=connection)

    task_params = connection.execute.call_args_list[0].args[1]
    subtask_params = connection.execute.call_args_list[1].args[1]
    expected_keys = {
        "null": "none",
        "false": "false",
        "3": "int",
        "00000000-0000-0000-0000-000000000124": "uuid-key",
        "1.50": "decimal-key",
        "2024-01-02T00:00:00Z": "datetime-key",
    }
    assert (
        json.loads(task_params["dag_plan"])["subtasks"][0]["input_data"]["nested"] == expected_keys
    )
    assert json.loads(subtask_params["input_data"])["nested"] == expected_keys


@pytest.mark.asyncio
async def test_state_projection_rejects_unsupported_values_before_writes() -> None:
    subtask = SubTask(
        id="step_1",
        assigned_agent="research",
        description="Synthetic",
        input_data={"opaque": object()},
    )
    state = AgentExecutionState(
        session_id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
        workspace_id="00000000-0000-0000-0000-000000000003",
        user_instruction="Synthetic",
        interaction_modality="text",
        active_plan=[subtask],
    )
    connection = AsyncMock()
    service = StateProjectionService(AsyncMock())

    with pytest.raises(TypeError, match="not JSON serializable"):
        await service.project_state(state, connection=connection)

    connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_state_projection_rejects_non_finite_json_numbers_before_writes() -> None:
    subtask = SubTask(
        id="step_1",
        assigned_agent="research",
        description="Synthetic",
        input_data={"score": float("nan")},
    )
    state = AgentExecutionState(
        session_id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
        workspace_id="00000000-0000-0000-0000-000000000003",
        user_instruction="Synthetic",
        interaction_modality="text",
        active_plan=[subtask],
    )
    connection = AsyncMock()

    with pytest.raises(ValueError, match="Out of range float"):
        await StateProjectionService(AsyncMock()).project_state(state, connection=connection)

    connection.execute.assert_not_awaited()


@pytest.mark.asyncio
async def test_state_projection_rejects_circular_json_values_before_writes() -> None:
    circular: dict[str, Any] = {}
    circular["self"] = circular
    subtask = SubTask(id="step_1", assigned_agent="research", description="Synthetic").model_copy(
        update={"input_data": circular}
    )
    state = AgentExecutionState(
        session_id="00000000-0000-0000-0000-000000000001",
        user_id="00000000-0000-0000-0000-000000000002",
        workspace_id="00000000-0000-0000-0000-000000000003",
        user_instruction="Synthetic",
        interaction_modality="text",
        active_plan=[subtask],
    )
    connection = AsyncMock()

    with pytest.raises(ValueError, match="Circular reference"):
        await StateProjectionService(AsyncMock()).project_state(state, connection=connection)

    connection.execute.assert_not_awaited()
