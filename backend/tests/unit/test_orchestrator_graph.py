"""Unit tests for LangGraph Orchestrator: flow, interrupts, and checkpoints."""

from typing import Any, cast
from unittest.mock import AsyncMock

import pytest
from langchain_core.runnables import RunnableConfig

from app.agents.checkpointer import create_memory_checkpointer
from app.agents.orchestrator import (
    create_orchestrator_graph,
    dispatch_subtask,
    request_human_approval,
    synthesize_final_report,
)
from app.agents.planner import DAGPlanner, PlannerError
from app.agents.state import AgentExecutionState, SubTask


def _initial_state() -> AgentExecutionState:
    return AgentExecutionState(
        session_id="session_test",
        user_id="user_test",
        workspace_id="00000000-0000-0000-0000-000000000001",
        user_instruction="Synthetic instruction",
        interaction_modality="text",
    )


def test_orchestrator_tools_dispatch_subtask() -> None:
    st = SubTask(id="t1", assigned_agent="research", description="test", status="pending")
    dispatched = dispatch_subtask(st)
    assert dispatched.status == "in_progress"
    assert st.status == "pending"  # original unchanged


def test_orchestrator_tools_request_human_approval() -> None:
    st = SubTask(id="sub_email", assigned_agent="email", description="Send blast")
    req = request_human_approval(st, action_description="Send campaign")
    assert req["subtask_id"] == "sub_email"
    assert req["action_description"] == "Send campaign"
    assert req["status"] == "pending"
    assert req["permission_level"] == "APPROVAL_REQUIRED"


def test_orchestrator_tools_synthesize_final_report() -> None:
    st1 = SubTask(
        id="t1",
        assigned_agent="research",
        description="R1",
        status="completed",
        output_result={"found": 5},
    )
    st2 = SubTask(
        id="t2",
        assigned_agent="document",
        description="D2",
        status="completed",
        output_result={"doc_id": "123"},
    )
    state = AgentExecutionState(
        session_id="s1",
        user_id="u1",
        workspace_id="w1",
        user_instruction="Generate brief",
        interaction_modality="text",
        active_plan=[st1, st2],
    )
    report = synthesize_final_report(state)
    assert report["all_successful"] is True
    assert report["completed_count"] == 2
    assert report["failed_count"] == 0
    assert len(report["results"]) == 2


def test_synthesize_empty_plan_is_not_successful() -> None:
    report = synthesize_final_report(_initial_state())

    assert report["all_successful"] is False
    assert "successfully" not in report["summary"]


@pytest.mark.asyncio
async def test_planning_exception_remains_failed() -> None:
    planner = AsyncMock(spec=DAGPlanner)
    planner.plan.side_effect = PlannerError("synthetic planning failure")
    graph = create_orchestrator_graph(planner=cast(DAGPlanner, planner))

    result = await graph.ainvoke(_initial_state())

    assert result["plan_status"] == "failed"
    assert result["shared_context"]["planning_error"] == "synthetic planning failure"
    assert result["shared_context"]["synthesis"]["all_successful"] is False


@pytest.mark.asyncio
async def test_empty_planner_result_is_not_successful() -> None:
    planner = AsyncMock(spec=DAGPlanner)
    planner.plan.return_value = ("synthetic intent", [])
    graph = create_orchestrator_graph(planner=cast(DAGPlanner, planner))

    result = await graph.ainvoke(_initial_state())

    assert result["plan_status"] == "failed"
    assert result["shared_context"]["planning_error"] == "Plan must contain at least one subtask"
    assert result["shared_context"]["synthesis"]["all_successful"] is False
    assert not any(
        step.get("step") == "plan" and step.get("status") == "executing"
        for step in result["execution_trace"]
    )


@pytest.mark.asyncio
async def test_invalid_preexisting_plan_remains_failed_even_if_items_are_completed() -> None:
    plan = [
        SubTask(id="dup", assigned_agent="research", description="First", status="completed"),
        SubTask(id="dup", assigned_agent="coding", description="Second", status="completed"),
    ]
    state = _initial_state().model_copy(update={"active_plan": plan})
    result = await create_orchestrator_graph().ainvoke(state)

    assert result["plan_status"] == "failed"
    assert "Duplicate" in result["shared_context"]["planning_error"]
    assert result["shared_context"]["synthesis"]["all_successful"] is False


@pytest.mark.asyncio
async def test_failed_prerequisite_does_not_unblock_dependent() -> None:
    plan = [
        SubTask(id="a", assigned_agent="research", description="A", status="failed"),
        SubTask(id="b", assigned_agent="document", description="B", dependencies=["a"]),
    ]
    state = _initial_state().model_copy(update={"active_plan": plan, "completed_task_ids": ["a"]})
    result = await create_orchestrator_graph().ainvoke(state)
    by_id = {task.id: task for task in result["active_plan"]}

    assert by_id["b"].status == "pending"
    assert result["plan_status"] == "failed"
    assert not any(
        step.get("step") == "dispatch" and step.get("subtask_id") == "b"
        for step in result["execution_trace"]
    )


@pytest.mark.asyncio
async def test_stale_completion_id_does_not_unblock_uncompleted_prerequisite() -> None:
    plan = [
        SubTask(id="a", assigned_agent="research", description="A", status="in_progress"),
        SubTask(id="b", assigned_agent="document", description="B", dependencies=["a"]),
    ]
    state = _initial_state().model_copy(update={"active_plan": plan, "completed_task_ids": ["a"]})
    result = await create_orchestrator_graph().ainvoke(state)
    by_id = {task.id: task for task in result["active_plan"]}

    assert by_id["a"].status == "in_progress"
    assert by_id["b"].status == "pending"


@pytest.mark.asyncio
async def test_failed_prerequisite_does_not_block_independent_work() -> None:
    plan = [
        SubTask(id="a", assigned_agent="research", description="A", status="failed"),
        SubTask(id="b", assigned_agent="document", description="B", dependencies=["a"]),
        SubTask(id="c", assigned_agent="coding", description="Independent"),
    ]
    state = _initial_state().model_copy(update={"active_plan": plan, "completed_task_ids": ["a"]})
    result = await create_orchestrator_graph().ainvoke(state)
    by_id = {task.id: task for task in result["active_plan"]}

    assert by_id["b"].status == "pending"
    assert by_id["c"].status == "in_progress"


@pytest.mark.asyncio
async def test_completed_prerequisite_still_unblocks_dependent() -> None:
    plan = [
        SubTask(id="a", assigned_agent="research", description="A", status="completed"),
        SubTask(id="b", assigned_agent="document", description="B", dependencies=["a"]),
    ]
    state = _initial_state().model_copy(update={"active_plan": plan, "completed_task_ids": ["a"]})
    result = await create_orchestrator_graph().ainvoke(state)
    by_id = {task.id: task for task in result["active_plan"]}

    assert by_id["b"].status == "in_progress"


@pytest.mark.asyncio
async def test_completed_status_without_completion_record_does_not_unblock() -> None:
    plan = [
        SubTask(id="a", assigned_agent="research", description="A", status="completed"),
        SubTask(id="b", assigned_agent="document", description="B", dependencies=["a"]),
    ]
    state = _initial_state().model_copy(update={"active_plan": plan})
    result = await create_orchestrator_graph().ainvoke(state)

    assert result["active_plan"][1].status == "pending"


@pytest.mark.asyncio
async def test_orchestrator_graph_compiles_and_runs_plan() -> None:
    checkpointer = create_memory_checkpointer()
    graph = create_orchestrator_graph(checkpointer=checkpointer)

    plan = [
        SubTask(id="t1", assigned_agent="research", description="Research"),
    ]
    initial_state = AgentExecutionState(
        session_id="sess_test",
        user_id="user_test",
        workspace_id="ws_test",
        user_instruction="Perform research",
        interaction_modality="text",
        active_plan=plan,
    )

    config = cast(RunnableConfig, {"configurable": {"thread_id": "thread_1"}})
    result = await graph.ainvoke(initial_state, config=config)

    # Dispatches the ready subtask
    assert result["plan_status"] == "executing"
    assert result["active_plan"][0].status == "in_progress"
    assert any(step.get("step") == "dispatch" for step in result["execution_trace"])

    # Verify state was checkpointed
    checkpoint = checkpointer.get(config)
    assert checkpoint is not None


@pytest.mark.asyncio
async def test_orchestrator_graph_interrupt_on_approval_requirement() -> None:
    checkpointer = create_memory_checkpointer()
    graph = create_orchestrator_graph(
        checkpointer=checkpointer,
        interrupt_before_approval=True,
    )

    # Email agent requires human approval
    plan = [
        SubTask(id="t_email", assigned_agent="email", description="Send confirmation"),
    ]
    initial_state = AgentExecutionState(
        session_id="sess_approval",
        user_id="user_test",
        workspace_id="ws_test",
        user_instruction="Send confirmation email",
        interaction_modality="text",
        active_plan=plan,
    )

    config = cast(RunnableConfig, {"configurable": {"thread_id": "thread_approval"}})
    result = await graph.ainvoke(initial_state, config=config)

    # Graph pauses and requests approval
    assert result["pending_approval"] is not None
    assert result["pending_approval"]["subtask_id"] == "t_email"
    assert result["pending_approval"]["status"] == "pending"
    assert result["plan_status"] == "paused"
    assert result["active_plan"][0].status == "awaiting_approval"


@pytest.mark.asyncio
async def test_orchestrator_crash_recovery_from_checkpoint() -> None:
    """Demonstrates 100% state restoration from checkpoint across invocations."""
    checkpointer = create_memory_checkpointer()
    graph = create_orchestrator_graph(checkpointer=checkpointer)

    plan = [
        SubTask(id="step_a", assigned_agent="research", description="Step A"),
        SubTask(
            id="step_b",
            assigned_agent="document",
            description="Step B",
            dependencies=["step_a"],
        ),
    ]
    initial_state = AgentExecutionState(
        session_id="sess_recovery",
        user_id="user_recovery",
        workspace_id="ws_recovery",
        user_instruction="Two step task",
        interaction_modality="text",
        active_plan=plan,
    )

    config = cast(RunnableConfig, {"configurable": {"thread_id": "recovery_thread_42"}})

    # First invocation: dispatches step_a
    intermediate = await graph.ainvoke(initial_state, config=config)
    assert intermediate["active_plan"][0].status == "in_progress"

    # Simulate worker completing step_a
    completed_step_a = intermediate["active_plan"][0].model_copy(
        update={"status": "completed", "output_result": {"notes": "done"}}
    )
    simulated_worker_state = {
        "active_plan": [completed_step_a, intermediate["active_plan"][1]],
        "completed_task_ids": ["step_a"],
    }

    # Simulate process termination & resume from checkpoint with updated state
    resumed_result = await graph.ainvoke(cast(Any, simulated_worker_state), config=config)

    # Step B should now be runnable and dispatched
    assert "step_a" in resumed_result["completed_task_ids"]
    assert resumed_result["active_plan"][1].status == "in_progress"


@pytest.mark.asyncio
async def test_runnable_subtask_with_requires_approval_routes_to_approval() -> None:
    """When a runnable subtask explicitly has requires_approval=True, router halts at approval."""
    checkpointer = create_memory_checkpointer()
    graph = create_orchestrator_graph(checkpointer=checkpointer, interrupt_before_approval=True)

    plan = [
        SubTask(
            id="t_custom",
            assigned_agent="coding",
            description="Run dangerous operation",
            requires_approval=True,
        ),
    ]
    initial_state = _initial_state().model_copy(update={"active_plan": plan})
    config = cast(RunnableConfig, {"configurable": {"thread_id": "thread_flag_approval"}})

    result = await graph.ainvoke(initial_state, config=config)

    assert result["plan_status"] == "paused"
    assert result["pending_approval"] is not None
    assert result["pending_approval"]["subtask_id"] == "t_custom"
    assert result["active_plan"][0].status == "awaiting_approval"
    # Never dispatched to in_progress
    assert not any(step.get("step") == "dispatch" for step in result["execution_trace"])


@pytest.mark.asyncio
async def test_awaiting_approval_subtask_routes_to_approval_and_interrupts() -> None:
    """If state has a subtask already in awaiting_approval, it routes to wait_for_approval."""
    checkpointer = create_memory_checkpointer()
    graph = create_orchestrator_graph(checkpointer=checkpointer, interrupt_before_approval=True)

    plan = [
        SubTask(
            id="t_await",
            assigned_agent="email",
            description="Send newsletter",
            status="awaiting_approval",
        ),
    ]
    initial_state = _initial_state().model_copy(update={"active_plan": plan})
    config = cast(RunnableConfig, {"configurable": {"thread_id": "thread_already_awaiting"}})

    result = await graph.ainvoke(initial_state, config=config)

    assert result["plan_status"] == "paused"
    assert result["pending_approval"] is not None
    assert result["pending_approval"]["subtask_id"] == "t_await"
    assert not any(step.get("step") == "dispatch" for step in result["execution_trace"])


@pytest.mark.asyncio
async def test_security_finding_8_already_in_progress_approval_required_is_trapped() -> None:
    """Security Finding #8: in_progress subtask requiring approval must NEVER bypass decision."""
    checkpointer = create_memory_checkpointer()
    graph = create_orchestrator_graph(checkpointer=checkpointer, interrupt_before_approval=True)

    # Subtask requiring approval (email agent) marked in_progress with no pending subtasks
    plan = [
        SubTask(
            id="t_inprog_email",
            assigned_agent="email",
            description="Exfiltrate or blast emails",
            status="in_progress",
        ),
    ]
    initial_state = _initial_state().model_copy(update={"active_plan": plan})
    config = cast(RunnableConfig, {"configurable": {"thread_id": "thread_sec_8"}})

    result = await graph.ainvoke(initial_state, config=config)

    # Must be trapped: paused and demoted to awaiting_approval, requesting human approval
    assert result["plan_status"] == "paused"
    assert result["pending_approval"] is not None
    assert result["pending_approval"]["subtask_id"] == "t_inprog_email"
    assert result["active_plan"][0].status == "awaiting_approval"


@pytest.mark.asyncio
async def test_multi_step_dependent_task_requiring_approval_pauses_when_deps_complete() -> None:
    """Task 1 (research) runs; when done, Task 2 (email, requires approval) halts at approval."""
    checkpointer = create_memory_checkpointer()
    graph = create_orchestrator_graph(checkpointer=checkpointer, interrupt_before_approval=True)

    plan = [
        SubTask(id="step_1", assigned_agent="research", description="Find contacts"),
        SubTask(
            id="step_2",
            assigned_agent="email",
            description="Send outreach",
            dependencies=["step_1"],
        ),
    ]
    initial_state = _initial_state().model_copy(update={"active_plan": plan})
    config = cast(RunnableConfig, {"configurable": {"thread_id": "thread_chained_approval"}})

    # Turn 1: step_1 is dispatched to in_progress; step_2 is pending (dependency not met)
    first_run = await graph.ainvoke(initial_state, config=config)
    assert first_run["active_plan"][0].status == "in_progress"
    assert first_run["active_plan"][1].status == "pending"
    assert first_run["plan_status"] == "executing"

    # Turn 2: worker completes step_1
    completed_step_1 = first_run["active_plan"][0].model_copy(
        update={"status": "completed", "output_result": {"contacts": ["test@example.com"]}}
    )
    resumed_state = {
        "active_plan": [completed_step_1, first_run["active_plan"][1]],
        "completed_task_ids": ["step_1"],
    }
    second_run = await graph.ainvoke(cast(Any, resumed_state), config=config)

    # step_2 is now runnable, but requires approval: must pause and NOT dispatch
    assert second_run["plan_status"] == "paused"
    assert second_run["pending_approval"] is not None
    assert second_run["pending_approval"]["subtask_id"] == "step_2"
    assert second_run["active_plan"][1].status == "awaiting_approval"
    # Never dispatched to in_progress
    assert second_run["active_plan"][1].status != "in_progress"
