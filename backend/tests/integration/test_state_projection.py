"""Integration tests for StateProjectionService syncing into tasks and subtasks tables."""

import pytest
from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncEngine

from app.agents.state import AgentExecutionState, SubTask
from app.services.projection_service import StateProjectionService
from tests.support.postgres import scoped, seed_tenant


@pytest.mark.asyncio
async def test_state_projection_persists_to_postgresql(
    app_engine: AsyncEngine,
    admin_engine: AsyncEngine,
) -> None:
    async with admin_engine.connect() as conn, conn.begin():
        tenant = await seed_tenant(conn, "proj_test")

    service = StateProjectionService(app_engine)

    sub1 = SubTask(
        id="step_1",
        assigned_agent="research",
        description="Search info",
        dependencies=[],
        status="in_progress",
        input_data={"query": "test"},
    )
    sub2 = SubTask(
        id="step_2",
        assigned_agent="document",
        description="Write doc",
        dependencies=["step_1"],
        status="pending",
    )

    state = AgentExecutionState(
        session_id=str(tenant.session_id),
        user_id=str(tenant.user_id),
        workspace_id=str(tenant.workspace_id),
        user_instruction="Analyze and summarize",
        interaction_modality="text",
        active_plan=[sub1, sub2],
        plan_status="executing",
        completed_task_ids=[],
        shared_context={"task_id": str(tenant.task_id)},
        execution_trace=[{"step": "plan"}, {"step": "dispatch"}],
    )

    # 1. Project initial executing state
    async with scoped(app_engine, tenant.workspace_id) as conn:
        task_id = await service.project_state(state, connection=conn)
        assert task_id == tenant.task_id

        # Query tasks table to verify projection
        task_row = (
            await conn.execute(
                text("SELECT status, tool_turns, instruction FROM tasks WHERE id = :id"),
                {"id": task_id},
            )
        ).one()
        assert task_row.status == "executing"
        assert task_row.tool_turns == 2
        assert task_row.instruction == "Analyze and summarize"

        # Query subtasks
        subtasks_count = (
            await conn.execute(
                text("SELECT count(*) FROM subtasks WHERE task_id = :task_id"),
                {"task_id": task_id},
            )
        ).scalar_one()
        assert subtasks_count == 2

    # 2. Complete subtask 1 and test idempotent upsert update
    completed_sub1 = sub1.model_copy(
        update={"status": "completed", "output_result": {"summary": "Done"}}
    )
    updated_state = state.model_copy(
        update={
            "active_plan": [completed_sub1, sub2],
            "completed_task_ids": ["step_1"],
            "execution_trace": [*state.execution_trace, {"step": "complete"}],
        }
    )

    async with scoped(app_engine, tenant.workspace_id) as conn:
        await service.project_state(updated_state, connection=conn)

        # Check subtask 1 is updated to completed
        sub1_row = (
            await conn.execute(
                text(
                    "SELECT status, output_result FROM subtasks "
                    "WHERE task_id = :task_id AND assigned_agent = 'research'"
                ),
                {"task_id": task_id},
            )
        ).one()
        assert sub1_row.status == "completed"
        assert sub1_row.output_result == {"summary": "Done"}
