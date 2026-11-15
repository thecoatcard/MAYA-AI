"""Unit tests for Pydantic state models: SubTask and AgentExecutionState."""

from datetime import UTC, datetime
from decimal import Decimal
from typing import Any, cast
from uuid import UUID

import pytest
from pydantic import ValidationError

from app.agents.state import AgentExecutionState, SubTask


def test_subtask_field_defaults() -> None:
    st = SubTask(
        id="task_1",
        assigned_agent="research",
        description="Gather background information",
    )
    assert st.id == "task_1"
    assert st.assigned_agent == "research"
    assert st.description == "Gather background information"
    assert st.dependencies == []
    assert st.status == "pending"
    assert st.input_data == {}
    assert st.output_result is None
    assert st.verification_proof is None
    assert st.error_message is None
    assert st.retry_count == 0


@pytest.mark.parametrize(
    "role",
    ["research", "coding", "document", "email", "pm", "browser", "file"],
)
def test_subtask_valid_agent_roles(role: str) -> None:
    st = SubTask(id="task_role", assigned_agent=cast(Any, role), description="desc")
    assert st.assigned_agent == role


def test_subtask_invalid_agent_role() -> None:
    with pytest.raises(ValidationError):
        SubTask(id="task_bad", assigned_agent=cast(Any, "unsupported_agent"), description="desc")


@pytest.mark.parametrize(
    "status",
    ["pending", "in_progress", "awaiting_approval", "completed", "failed"],
)
def test_subtask_valid_status_literals(status: str) -> None:
    st = SubTask(
        id="t1",
        assigned_agent="coding",
        description="desc",
        status=cast(Any, status),
    )
    assert st.status == status


def test_subtask_invalid_status() -> None:
    with pytest.raises(ValidationError):
        SubTask(
            id="t1",
            assigned_agent="coding",
            description="desc",
            status=cast(Any, "running"),
        )


def test_subtask_serialization_round_trip() -> None:
    original = SubTask(
        id="sub_99",
        assigned_agent="file",
        description="Save file artifact",
        dependencies=["sub_98"],
        status="completed",
        input_data={"path": "/tmp/out.txt"},
        output_result={"bytes": 42},
        verification_proof={"sha256": "abcdef"},
        error_message=None,
        retry_count=1,
    )
    dumped_json = original.model_dump_json()
    loaded = SubTask.model_validate_json(dumped_json)
    assert loaded == original


def test_agent_execution_state_field_defaults() -> None:
    state = AgentExecutionState(
        session_id="sess_123",
        user_id="user_456",
        workspace_id="ws_789",
        user_instruction="Analyze report",
        interaction_modality="text",
    )
    assert state.session_id == "sess_123"
    assert state.user_id == "user_456"
    assert state.workspace_id == "ws_789"
    assert state.user_instruction == "Analyze report"
    assert state.interaction_modality == "text"
    assert state.active_plan == []
    assert state.plan_status == "planning"
    assert state.completed_task_ids == []
    assert state.pending_approval is None
    assert state.shared_context == {}
    assert state.execution_trace == []


@pytest.mark.parametrize("modality", ["voice", "text"])
def test_agent_execution_state_modalities(modality: str) -> None:
    state = AgentExecutionState(
        session_id="s",
        user_id="u",
        workspace_id="w",
        user_instruction="i",
        interaction_modality=cast(Any, modality),
    )
    assert state.interaction_modality == modality


def test_agent_execution_state_invalid_modality() -> None:
    with pytest.raises(ValidationError):
        AgentExecutionState(
            session_id="s",
            user_id="u",
            workspace_id="w",
            user_instruction="i",
            interaction_modality=cast(Any, "telepathy"),
        )


@pytest.mark.parametrize(
    "plan_status",
    ["planning", "executing", "paused", "completed", "failed"],
)
def test_agent_execution_state_valid_plan_status(plan_status: str) -> None:
    state = AgentExecutionState(
        session_id="s",
        user_id="u",
        workspace_id="w",
        user_instruction="i",
        interaction_modality="text",
        plan_status=cast(Any, plan_status),
    )
    assert state.plan_status == plan_status


def test_agent_execution_state_invalid_plan_status() -> None:
    with pytest.raises(ValidationError):
        AgentExecutionState(
            session_id="s",
            user_id="u",
            workspace_id="w",
            user_instruction="i",
            interaction_modality="text",
            plan_status=cast(Any, "cancelled"),
        )


def test_agent_execution_state_serialization_round_trip() -> None:
    sub1 = SubTask(id="t1", assigned_agent="research", description="R1")
    sub2 = SubTask(id="t2", assigned_agent="document", description="D2", dependencies=["t1"])

    state = AgentExecutionState(
        session_id="sess_1",
        user_id="usr_1",
        workspace_id="ws_1",
        user_instruction="Research and summarize",
        interaction_modality="voice",
        active_plan=[sub1, sub2],
        plan_status="executing",
        completed_task_ids=["t1"],
        pending_approval={"action": "save_doc"},
        shared_context={"topic": "AI"},
        execution_trace=[{"step": "plan", "count": 2}],
    )

    data = state.model_dump()
    assert len(data["active_plan"]) == 2
    assert data["active_plan"][1]["dependencies"] == ["t1"]

    json_str = state.model_dump_json()
    assert "Research and summarize" in json_str
    reconstructed = AgentExecutionState.model_validate_json(json_str)
    assert reconstructed == state
    assert reconstructed.active_plan[0].id == "t1"


def test_agent_execution_state_round_trips_json_compatible_nested_values() -> None:
    state = AgentExecutionState(
        session_id="s",
        user_id="u",
        workspace_id="w",
        user_instruction="Synthetic",
        interaction_modality="text",
        shared_context={
            "request_id": UUID("00000000-0000-0000-0000-000000000123"),
            "created_at": datetime(2024, 1, 2, 3, 4, 5, tzinfo=UTC),
            "amount": Decimal("1.25"),
            "metadata": {UUID("00000000-0000-0000-0000-000000000124"): Decimal("2.50")},
        },
        execution_trace=[{"observed_at": datetime(2024, 1, 2, tzinfo=UTC)}],
    )

    restored = AgentExecutionState.model_validate_json(state.model_dump_json())

    assert restored.shared_context == {
        "request_id": "00000000-0000-0000-0000-000000000123",
        "created_at": "2024-01-02T03:04:05Z",
        "amount": "1.25",
        "metadata": {"00000000-0000-0000-0000-000000000124": "2.50"},
    }
    assert restored.execution_trace == [{"observed_at": "2024-01-02T00:00:00Z"}]
