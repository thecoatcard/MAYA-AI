"""Unit tests for Phase 5 tasks and subtasks SQLAlchemy schema models."""

from uuid import uuid4

from sqlalchemy import inspect

from app.db.base import Base
from app.db.models import (
    PlanStatus,
    Subtask,
    SubtaskAgent,
    SubtaskStatus,
    Task,
)


def test_base_metadata_contains_required_tables() -> None:
    """Verify Base.metadata has tasks and subtasks registered."""
    table_names = set(Base.metadata.tables.keys())
    assert "tasks" in table_names
    assert "subtasks" in table_names
    assert "workspaces" in table_names
    assert "users" in table_names


def test_tasks_model_has_all_required_fields() -> None:
    """Verify tasks table has every field specified for Phase 5."""
    mapper = inspect(Task)
    columns = {col.key: col for col in mapper.columns}

    required_fields = [
        "id",
        "workspace_id",
        "user_id",
        "session_id",
        "user_instruction",
        "plan_status",
        "active_plan",
        "shared_context",
        "execution_trace",
        "created_at",
        "updated_at",
    ]

    for field in required_fields:
        assert field in columns, f"Field '{field}' missing from Task model"

    # Verify primary key
    assert columns["id"].primary_key is True

    # Verify foreign keys
    fk_targets = {fk.target_fullname for fk in mapper.columns["workspace_id"].foreign_keys}
    assert "workspaces.id" in fk_targets

    user_fk_targets = {fk.target_fullname for fk in mapper.columns["user_id"].foreign_keys}
    assert "users.id" in user_fk_targets

    # Verify types and nullability for required fields
    assert columns["user_instruction"].nullable is False
    assert columns["plan_status"].nullable is False
    assert columns["active_plan"].nullable is False
    assert columns["shared_context"].nullable is False
    assert columns["execution_trace"].nullable is False
    assert columns["created_at"].nullable is False
    assert columns["updated_at"].nullable is False


def test_subtasks_model_has_all_required_fields() -> None:
    """Verify subtasks table has every field specified for Phase 5."""
    mapper = inspect(Subtask)
    columns = {col.key: col for col in mapper.columns}

    required_fields = [
        "id",
        "task_id",
        "workspace_id",
        "assigned_agent",
        "description",
        "dependencies",
        "status",
        "input_data",
        "output_result",
        "verification_proof",
        "error_message",
        "retry_count",
        "created_at",
        "updated_at",
    ]

    for field in required_fields:
        assert field in columns, f"Field '{field}' missing from Subtask model"

    # Verify primary key
    assert columns["id"].primary_key is True

    # Verify foreign keys
    task_fk_targets = {fk.target_fullname for fk in mapper.columns["task_id"].foreign_keys}
    assert "tasks.id" in task_fk_targets

    ws_fk_targets = {fk.target_fullname for fk in mapper.columns["workspace_id"].foreign_keys}
    assert "workspaces.id" in ws_fk_targets

    # Nullable vs non-nullable checks
    assert columns["assigned_agent"].nullable is False
    assert columns["description"].nullable is False
    assert columns["dependencies"].nullable is False
    assert columns["status"].nullable is False
    assert columns["input_data"].nullable is False
    assert columns["output_result"].nullable is True
    assert columns["verification_proof"].nullable is True
    assert columns["error_message"].nullable is True
    assert columns["retry_count"].nullable is False
    assert columns["created_at"].nullable is False
    assert columns["updated_at"].nullable is False


def test_task_subtask_relationship() -> None:
    """Verify bidirectional relationship between Task and Subtask models."""
    ws_id = uuid4()
    u_id = uuid4()
    s_id = str(uuid4())

    task = Task(
        id=uuid4(),
        workspace_id=ws_id,
        user_id=u_id,
        session_id=s_id,
        user_instruction="Generate market research report and send summary",
        plan_status=PlanStatus.PLANNING,
        active_plan=[],
        shared_context={"topic": "market_analysis"},
        execution_trace=[],
    )

    subtask_1 = Subtask(
        id=uuid4(),
        task_id=task.id,
        workspace_id=ws_id,
        assigned_agent=SubtaskAgent.RESEARCH,
        description="Search recent trends",
        dependencies=[],
        status=SubtaskStatus.PENDING,
        input_data={"keywords": ["AI", "Agents"]},
    )

    subtask_2 = Subtask(
        id=uuid4(),
        task_id=task.id,
        workspace_id=ws_id,
        assigned_agent=SubtaskAgent.DOCUMENT,
        description="Compile summary document",
        dependencies=[str(subtask_1.id)],
        status=SubtaskStatus.PENDING,
        input_data={"template": "executive_brief"},
    )

    task.subtasks.append(subtask_1)
    task.subtasks.append(subtask_2)

    assert len(task.subtasks) == 2
    assert subtask_1.task is task
    assert subtask_2.task is task
    assert subtask_1 in task.subtasks
    assert subtask_2 in task.subtasks


def test_enums_values() -> None:
    """Verify allowed enum values match Phase 5 specification."""
    expected_plan_statuses = {"planning", "executing", "paused", "completed", "failed"}
    actual_plan_statuses = {e.value for e in PlanStatus}
    assert actual_plan_statuses == expected_plan_statuses

    expected_agents = {
        "research",
        "coding",
        "document",
        "email",
        "pm",
        "browser",
        "file",
    }
    actual_agents = {e.value for e in SubtaskAgent}
    assert actual_agents == expected_agents

    expected_subtask_statuses = {
        "pending",
        "in_progress",
        "awaiting_approval",
        "completed",
        "failed",
    }
    actual_subtask_statuses = {e.value for e in SubtaskStatus}
    assert actual_subtask_statuses == expected_subtask_statuses
