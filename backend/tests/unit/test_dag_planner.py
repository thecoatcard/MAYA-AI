"""Unit tests for DAG planner: Kahn's topological sort, cycle detection, and plan validation."""

import json
from typing import Any, cast
from unittest.mock import AsyncMock

import pytest

from app.agents.planner import (
    MAX_SUBTASKS_PER_PLAN,
    DAGPlanner,
    MaxSubtasksExceededError,
    PlanCycleError,
    PlanDecompositionResponse,
    PlanValidationError,
    SubTaskPlanItem,
    build_dependency_graph,
    topological_sort,
    validate_dag,
)
from app.agents.state import SubTask


def test_topological_sort_linear_chain() -> None:
    tasks = [
        SubTask(id="c", assigned_agent="email", description="Send", dependencies=["b"]),
        SubTask(id="a", assigned_agent="research", description="Find", dependencies=[]),
        SubTask(id="b", assigned_agent="document", description="Draft", dependencies=["a"]),
    ]
    sorted_tasks = topological_sort(tasks)
    ids = [t.id for t in sorted_tasks]
    assert ids == ["a", "b", "c"]


def test_topological_sort_diamond_dependency() -> None:
    # A -> B, A -> C; B -> D, C -> D
    tasks = [
        SubTask(id="d", assigned_agent="file", description="Save", dependencies=["b", "c"]),
        SubTask(id="b", assigned_agent="coding", description="Code", dependencies=["a"]),
        SubTask(id="c", assigned_agent="document", description="Doc", dependencies=["a"]),
        SubTask(id="a", assigned_agent="pm", description="Plan", dependencies=[]),
    ]
    sorted_tasks = topological_sort(tasks)
    ids = [t.id for t in sorted_tasks]

    assert ids[0] == "a"
    assert ids[-1] == "d"
    assert set(ids[1:3]) == {"b", "c"}


def test_topological_sort_disconnected_subgraphs() -> None:
    # Two disconnected branches: (A -> B) and (C -> D)
    tasks = [
        SubTask(id="b", assigned_agent="coding", description="B", dependencies=["a"]),
        SubTask(id="d", assigned_agent="file", description="D", dependencies=["c"]),
        SubTask(id="a", assigned_agent="research", description="A", dependencies=[]),
        SubTask(id="c", assigned_agent="browser", description="C", dependencies=[]),
    ]
    sorted_tasks = topological_sort(tasks)
    ids = [t.id for t in sorted_tasks]

    assert len(ids) == 4
    assert ids.index("a") < ids.index("b")
    assert ids.index("c") < ids.index("d")


def test_topological_sort_single_task() -> None:
    tasks = [SubTask(id="solo", assigned_agent="research", description="Sole task")]
    sorted_tasks = topological_sort(tasks)
    assert len(sorted_tasks) == 1
    assert sorted_tasks[0].id == "solo"


def test_topological_sort_rejects_duplicate_ids_directly() -> None:
    tasks = [
        SubTask(id="dup", assigned_agent="research", description="First"),
        SubTask(id="dup", assigned_agent="coding", description="Second"),
    ]

    with pytest.raises(PlanValidationError, match="Duplicate"):
        topological_sort(tasks)


def test_topological_sort_rejects_malformed_node() -> None:
    with pytest.raises(PlanValidationError, match="SubTask"):
        topological_sort(cast(Any, [None]))


def test_topological_sort_rejects_malformed_id() -> None:
    task = SubTask(id="a", assigned_agent="research", description="A").model_copy(
        update={"id": ["not", "an", "id"]}
    )

    with pytest.raises(PlanValidationError, match="id"):
        topological_sort([task])


def test_topological_sort_rejects_malformed_dependencies() -> None:
    task = SubTask(id="a", assigned_agent="research", description="A").model_copy(
        update={"dependencies": [None]}
    )

    with pytest.raises(PlanValidationError, match="dependencies"):
        topological_sort([task])


def test_topological_sort_rejects_non_sequence() -> None:
    with pytest.raises(PlanValidationError, match="sequence"):
        topological_sort(cast(Any, None))


def test_topological_sort_rejects_missing_dependency() -> None:
    tasks = [SubTask(id="a", assigned_agent="research", description="A", dependencies=["missing"])]

    with pytest.raises(PlanValidationError, match="non-existent"):
        topological_sort(tasks)


def test_topological_sort_preserves_empty_and_large_valid_graphs() -> None:
    assert topological_sort([]) == []

    tasks = [
        SubTask(id=f"task_{i}", assigned_agent="research", description="Task")
        for i in range(MAX_SUBTASKS_PER_PLAN + 1)
    ]
    assert topological_sort(tasks) == tasks


def test_topological_sort_rejects_repeated_dependency_entries() -> None:
    tasks = [
        SubTask(id="b", assigned_agent="coding", description="B", dependencies=["a", "a"]),
        SubTask(id="a", assigned_agent="research", description="A"),
    ]

    with pytest.raises(PlanValidationError, match="repeated"):
        topological_sort(tasks)


def test_topological_sort_rejects_dependency_edges_over_plan_bound() -> None:
    tasks = [
        SubTask(
            id=f"task_{index}",
            assigned_agent="research",
            description="Task",
            dependencies=[
                f"task_{dependency}"
                for dependency in range(MAX_SUBTASKS_PER_PLAN)
                if dependency != index
            ],
        )
        for index in range(MAX_SUBTASKS_PER_PLAN)
    ]

    with pytest.raises(PlanValidationError, match="dependency edge limit"):
        topological_sort(tasks)


def test_topological_sort_accepts_maximum_dependency_edges_for_a_dag() -> None:
    tasks = [
        SubTask(
            id=f"task_{index}",
            assigned_agent="research",
            description="Task",
            dependencies=[f"task_{dependency}" for dependency in range(index)],
        )
        for index in range(MAX_SUBTASKS_PER_PLAN)
    ]

    sorted_tasks = topological_sort(tasks)

    assert len(sorted_tasks) == MAX_SUBTASKS_PER_PLAN
    assert sum(len(task.dependencies) for task in sorted_tasks) == 190


def test_cycle_detection_self_loop() -> None:
    tasks = [SubTask(id="a", assigned_agent="coding", description="Self loop", dependencies=["a"])]
    with pytest.raises(PlanCycleError):
        validate_dag(tasks)


def test_cycle_detection_simple_circular() -> None:
    # A -> B -> A
    tasks = [
        SubTask(id="a", assigned_agent="research", description="A", dependencies=["b"]),
        SubTask(id="b", assigned_agent="document", description="B", dependencies=["a"]),
    ]
    with pytest.raises(PlanCycleError):
        topological_sort(tasks)


def test_cycle_detection_indirect_circular() -> None:
    # A -> B -> C -> A
    tasks = [
        SubTask(id="a", assigned_agent="research", description="A", dependencies=["c"]),
        SubTask(id="b", assigned_agent="coding", description="B", dependencies=["a"]),
        SubTask(id="c", assigned_agent="document", description="C", dependencies=["b"]),
    ]
    with pytest.raises(PlanCycleError):
        topological_sort(tasks)


def test_max_subtasks_limit_exceeded() -> None:
    tasks = [
        SubTask(id=f"t_{i}", assigned_agent="research", description=f"Task {i}")
        for i in range(MAX_SUBTASKS_PER_PLAN + 1)
    ]
    with pytest.raises(MaxSubtasksExceededError):
        validate_dag(tasks)


def test_max_subtasks_boundary_allowed() -> None:
    tasks = [
        SubTask(id=f"t_{i}", assigned_agent="research", description=f"Task {i}")
        for i in range(MAX_SUBTASKS_PER_PLAN)
    ]
    sorted_tasks = validate_dag(tasks)
    assert len(sorted_tasks) == MAX_SUBTASKS_PER_PLAN


def test_plan_validation_empty_plan() -> None:
    with pytest.raises(PlanValidationError, match="at least one"):
        validate_dag([])


def test_plan_response_schema_rejects_empty_plan() -> None:
    with pytest.raises(ValueError):
        PlanDecompositionResponse(intent="Synthetic", subtasks=[])


def test_plan_response_schema_rejects_too_many_subtasks() -> None:
    with pytest.raises(ValueError):
        PlanDecompositionResponse(
            intent="Synthetic",
            subtasks=[
                SubTaskPlanItem(
                    id=f"task_{index}", assigned_agent="research", description="Synthetic"
                )
                for index in range(MAX_SUBTASKS_PER_PLAN + 1)
            ],
        )


def test_plan_item_schema_bounds_dependency_edges_per_subtask() -> None:
    with pytest.raises(ValueError):
        SubTaskPlanItem(
            id="task",
            assigned_agent="research",
            description="Synthetic",
            dependencies=[f"dep_{index}" for index in range(MAX_SUBTASKS_PER_PLAN)],
        )


def test_plan_schema_publishes_plan_and_dependency_bounds() -> None:
    schema = PlanDecompositionResponse.model_json_schema()
    subtask_schema = schema["properties"]["subtasks"]
    dependency_schema = schema["$defs"]["SubTaskPlanItem"]["properties"]["dependencies"]

    assert subtask_schema["minItems"] == 1
    assert subtask_schema["maxItems"] == MAX_SUBTASKS_PER_PLAN
    assert dependency_schema["maxItems"] == MAX_SUBTASKS_PER_PLAN - 1
    assert dependency_schema["uniqueItems"] is True


def test_plan_validation_duplicate_subtask_id() -> None:
    tasks = [
        SubTask(id="dup", assigned_agent="research", description="1"),
        SubTask(id="dup", assigned_agent="coding", description="2"),
    ]
    with pytest.raises(PlanValidationError, match="Duplicate"):
        validate_dag(tasks)


def test_plan_validation_missing_dependency() -> None:
    tasks = [
        SubTask(id="t1", assigned_agent="research", description="T1", dependencies=["ghost"]),
    ]
    with pytest.raises(PlanValidationError, match="non-existent"):
        validate_dag(tasks)


def test_build_dependency_graph() -> None:
    tasks = [
        SubTask(id="t1", assigned_agent="research", description="T1"),
        SubTask(id="t2", assigned_agent="coding", description="T2", dependencies=["t1"]),
        SubTask(id="t3", assigned_agent="document", description="T3", dependencies=["t1", "t2"]),
    ]
    graph = build_dependency_graph(tasks)
    assert graph["t1"] == set()
    assert graph["t2"] == {"t1"}
    assert graph["t3"] == {"t1", "t2"}


@pytest.mark.asyncio
async def test_dag_planner_with_mock_gateway() -> None:
    mock_gateway = AsyncMock()
    mock_gateway.generate_structured.return_value = PlanDecompositionResponse(
        intent="Research and implement algorithm",
        subtasks=[
            SubTaskPlanItem(id="step_1", assigned_agent="research", description="Read specs"),
            SubTaskPlanItem(
                id="step_2",
                assigned_agent="coding",
                description="Write code",
                dependencies=["step_1"],
            ),
        ],
    )

    planner = DAGPlanner(gateway=mock_gateway)
    intent, plan = await planner.plan("Research and code this feature")

    assert intent == "Research and implement algorithm"
    assert len(plan) == 2
    assert plan[0].id == "step_1"
    assert plan[1].id == "step_2"
    mock_gateway.generate_structured.assert_called_once()


@pytest.mark.asyncio
async def test_dag_planner_includes_response_schema_in_initial_prompt() -> None:
    mock_gateway = AsyncMock()
    mock_gateway.generate_structured.return_value = PlanDecompositionResponse(
        intent="Synthetic intent",
        subtasks=[SubTaskPlanItem(id="a", assigned_agent="research", description="A")],
    )

    await DAGPlanner(gateway=mock_gateway).plan("Synthetic instruction")

    kwargs = mock_gateway.generate_structured.call_args.kwargs
    prompt = "\n".join(str(part) for part in kwargs["contents"])
    prompt += "\n" + str(kwargs["system_instruction"])
    assert json.dumps(PlanDecompositionResponse.model_json_schema()) in prompt


@pytest.mark.asyncio
async def test_dag_planner_cycle_repair_includes_previous_plan() -> None:
    cyclic = PlanDecompositionResponse(
        intent="Synthetic intent",
        subtasks=[
            SubTaskPlanItem(id="a", assigned_agent="research", description="A", dependencies=["b"]),
            SubTaskPlanItem(id="b", assigned_agent="coding", description="B", dependencies=["a"]),
        ],
    )
    repaired = PlanDecompositionResponse(
        intent="Synthetic intent",
        subtasks=[
            SubTaskPlanItem(id="a", assigned_agent="research", description="A"),
            SubTaskPlanItem(id="b", assigned_agent="coding", description="B", dependencies=["a"]),
        ],
    )
    mock_gateway = AsyncMock()
    mock_gateway.generate_structured.side_effect = [cyclic, repaired]

    await DAGPlanner(gateway=mock_gateway).plan("Synthetic instruction")

    assert mock_gateway.generate_structured.call_count == 2
    repair_contents = mock_gateway.generate_structured.call_args_list[1].kwargs["contents"]
    assert cyclic.model_dump_json() in "\n".join(str(part) for part in repair_contents)
    repair_system = mock_gateway.generate_structured.call_args_list[1].kwargs["system_instruction"]
    assert json.dumps(PlanDecompositionResponse.model_json_schema()) in repair_system
    repair_prompt = "\n".join(str(part) for part in repair_contents)
    assert "no more than 190 dependency edges" in repair_prompt
    assert json.dumps(PlanDecompositionResponse.model_json_schema()) in repair_prompt


@pytest.mark.asyncio
async def test_dag_planner_self_repair_on_cycle() -> None:
    mock_gateway = AsyncMock()

    # First turn returns cyclic plan: A -> B -> A
    cyclic_plan = PlanDecompositionResponse(
        intent="Circular intent",
        subtasks=[
            SubTaskPlanItem(id="a", assigned_agent="research", description="A", dependencies=["b"]),
            SubTaskPlanItem(id="b", assigned_agent="coding", description="B", dependencies=["a"]),
        ],
    )
    # Self-repair turn returns valid plan: A -> B
    repaired_plan = PlanDecompositionResponse(
        intent="Repaired intent",
        subtasks=[
            SubTaskPlanItem(id="a", assigned_agent="research", description="A", dependencies=[]),
            SubTaskPlanItem(id="b", assigned_agent="coding", description="B", dependencies=["a"]),
        ],
    )

    mock_gateway.generate_structured.side_effect = [cyclic_plan, repaired_plan]

    planner = DAGPlanner(gateway=mock_gateway)
    intent, plan = await planner.plan("Do tasks")

    assert intent == "Repaired intent"
    assert len(plan) == 2
    assert plan[0].id == "a"
    assert plan[1].id == "b"
    assert mock_gateway.generate_structured.call_count == 2
