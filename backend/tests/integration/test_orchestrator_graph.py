"""Integration tests for orchestrator graph execution and checkpointer lifecycle."""

from typing import Any, cast

import pytest
from langchain_core.runnables import RunnableConfig

from app.agents.checkpointer import create_memory_checkpointer
from app.agents.orchestrator import create_orchestrator_graph
from app.agents.state import AgentExecutionState, SubTask


@pytest.mark.asyncio
async def test_full_orchestrator_execution_lifecycle() -> None:
    """Executes a 3-step DAG through planning, multiple dispatches, and final synthesis."""
    checkpointer = create_memory_checkpointer()
    graph = create_orchestrator_graph(checkpointer=checkpointer)

    plan = [
        SubTask(id="sub_1", assigned_agent="research", description="Search docs"),
        SubTask(
            id="sub_2",
            assigned_agent="coding",
            description="Write code",
            dependencies=["sub_1"],
        ),
        SubTask(
            id="sub_3",
            assigned_agent="document",
            description="Write summary",
            dependencies=["sub_2"],
        ),
    ]

    initial_state = AgentExecutionState(
        session_id="sess_lifecycle",
        user_id="user_lifecycle",
        workspace_id="ws_lifecycle",
        user_instruction="Research, code, and document",
        interaction_modality="text",
        active_plan=plan,
    )

    config = cast(RunnableConfig, {"configurable": {"thread_id": "thread_lifecycle_1"}})

    # Turn 1: Starts and dispatches sub_1
    state1 = await graph.ainvoke(initial_state, config=config)
    assert state1["active_plan"][0].status == "in_progress"
    assert state1["active_plan"][1].status == "pending"

    # Turn 2: Simulate completion of sub_1
    completed_1 = state1["active_plan"][0].model_copy(
        update={"status": "completed", "output_result": {"docs": ["spec.md"]}}
    )
    state2 = await graph.ainvoke(
        cast(
            Any,
            {
                "active_plan": [completed_1, state1["active_plan"][1], state1["active_plan"][2]],
                "completed_task_ids": ["sub_1"],
            },
        ),
        config=config,
    )
    # sub_2 is dispatched
    assert state2["active_plan"][1].status == "in_progress"

    # Turn 3: Simulate completion of sub_2
    completed_2 = state2["active_plan"][1].model_copy(
        update={"status": "completed", "output_result": {"code": "main.py"}}
    )
    state3 = await graph.ainvoke(
        cast(
            Any,
            {
                "active_plan": [completed_1, completed_2, state2["active_plan"][2]],
                "completed_task_ids": ["sub_2"],
            },
        ),
        config=config,
    )
    # sub_3 is dispatched
    assert state3["active_plan"][2].status == "in_progress"

    # Turn 4: Simulate completion of sub_3
    completed_3 = state3["active_plan"][2].model_copy(
        update={"status": "completed", "output_result": {"report": "All tests pass"}}
    )
    final_state = await graph.ainvoke(
        cast(
            Any,
            {
                "active_plan": [completed_1, completed_2, completed_3],
                "completed_task_ids": ["sub_3"],
            },
        ),
        config=config,
    )

    # Workflow reaches synthesize and completes!
    assert final_state["plan_status"] == "completed"
    assert "synthesis" in final_state["shared_context"]
    assert final_state["shared_context"]["synthesis"]["all_successful"] is True
    assert final_state["shared_context"]["synthesis"]["completed_count"] == 3
