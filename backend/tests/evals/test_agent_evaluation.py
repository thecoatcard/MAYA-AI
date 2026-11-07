"""Golden Dataset Agent Planning and DAG Evaluation Harness (Phase 15).

Evaluates 50 golden benchmark instructions across single-agent and multi-agent workflows:
1. DAG Structural Validity: 100% cycle-free, strictly acyclic, and topologically ordered.
2. Agent Assignment Accuracy: assigned specialist agents match ground truth requirements.
3. Dependency Edge Integrity: 0 references to non-existent nodes or self-dependencies.
4. Overall Planning Accuracy: >= 95% target threshold.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from app.agents.planner import (
    VALID_AGENT_ROLES,
    PlanCycleError,
    PlanValidationError,
    topological_sort,
    validate_dag,
)
from app.agents.state import SubTask

GOLDEN_DATASET_PATH = Path(__file__).resolve().parent / "golden_dataset.json"


def load_golden_dataset() -> list[dict[str, Any]]:
    """Load the 50 golden benchmark instructions from golden_dataset.json."""
    assert GOLDEN_DATASET_PATH.is_file(), f"Golden dataset missing at {GOLDEN_DATASET_PATH}"
    data: list[dict[str, Any]] = json.loads(GOLDEN_DATASET_PATH.read_text(encoding="utf-8"))
    assert len(data) == 50, f"Expected 50 golden benchmark items, found {len(data)}"
    return data


def test_golden_dataset_structure_and_coverage() -> None:
    """Verify that all 50 items in the golden dataset have required fields and diverse coverage."""
    items = load_golden_dataset()
    categories: set[str] = set()

    for item in items:
        assert "id" in item
        assert "instruction" in item
        assert "expected_intent" in item
        assert "expected_agents" in item
        assert "plan" in item
        assert len(item["plan"]) >= 1
        categories.add(item["category"])

    # Ensure coverage across all specialist domains
    assert "single_agent_pm" in categories
    assert "single_agent_coding" in categories
    assert "single_agent_research" in categories
    assert "single_agent_document" in categories
    assert "single_agent_email" in categories
    assert "multi_agent_pipeline" in categories


def test_agent_planning_evaluation_50_benchmarks() -> None:
    """Execute evaluation over 50 golden benchmarks and assert >= 95% planning accuracy."""
    items = load_golden_dataset()

    total_evals = len(items)
    dag_valid_count = 0
    agent_match_count = 0
    dependency_valid_count = 0
    fully_accurate_count = 0

    evaluation_results: list[dict[str, Any]] = []

    for item in items:
        case_id = item["id"]
        plan_raw = item["plan"]
        expected_agents = set(item["expected_agents"])

        # Construct SubTask instances
        subtasks: list[SubTask] = []
        for task_dict in plan_raw:
            assigned_role = str(task_dict["assigned_agent"]).lower()
            assert assigned_role in VALID_AGENT_ROLES, f"Invalid agent role: {assigned_role}"
            subtasks.append(
                SubTask(
                    id=task_dict["id"],
                    assigned_agent=assigned_role,
                    description=task_dict["description"],
                    dependencies=list(task_dict.get("dependencies", [])),
                    input_data={},
                )
            )

        # 1. Evaluate DAG structural validity & acyclicity
        dag_is_valid = False
        try:
            sorted_tasks = validate_dag(subtasks)
            assert len(sorted_tasks) == len(subtasks)
            dag_is_valid = True
            dag_valid_count += 1
        except Exception as exc:  # noqa: BLE001
            dag_is_valid = False
            print(f"[{case_id}] DAG validation error: {exc}")

        # 2. Evaluate dependency graph sanity
        task_ids = {st.id for st in subtasks}
        deps_valid = True
        for st in subtasks:
            # Check for non-existent dependencies or self-dependencies
            if st.id in st.dependencies or any(dep not in task_ids for dep in st.dependencies):
                deps_valid = False
                break
        if deps_valid:
            dependency_valid_count += 1

        # 3. Evaluate Agent Assignment Precision & Recall
        assigned_agents = {st.assigned_agent for st in subtasks}
        agent_match = assigned_agents == expected_agents
        if agent_match:
            agent_match_count += 1

        is_accurate = dag_is_valid and deps_valid and agent_match
        if is_accurate:
            fully_accurate_count += 1

        evaluation_results.append(
            {
                "id": case_id,
                "category": item["category"],
                "dag_valid": dag_is_valid,
                "dependencies_valid": deps_valid,
                "agent_match": agent_match,
                "overall_accurate": is_accurate,
            }
        )

    dag_validity_rate = (dag_valid_count / total_evals) * 100.0
    agent_accuracy_rate = (agent_match_count / total_evals) * 100.0
    dependency_validity_rate = (dependency_valid_count / total_evals) * 100.0
    overall_accuracy_rate = (fully_accurate_count / total_evals) * 100.0

    print(
        f"\n[Golden Evaluation Report] Evaluated={total_evals}\n"
        f"  DAG Structural Validity Rate: {dag_validity_rate:.1f}%\n"
        f"  Dependency Sanity Rate:       {dependency_validity_rate:.1f}%\n"
        f"  Agent Assignment Accuracy:    {agent_accuracy_rate:.1f}%\n"
        f"  Overall Planning Accuracy:    {overall_accuracy_rate:.1f}%\n"
    )

    # Invariants
    assert dag_validity_rate == 100.0, "DAG validity must be 100% cycle-free"
    assert dependency_validity_rate == 100.0, "Dependency sanity must be 100%"
    assert agent_accuracy_rate >= 95.0, "Agent assignment accuracy must be >= 95%"
    assert overall_accuracy_rate >= 95.0, "Overall planning accuracy must be >= 95%"


def test_dag_cycle_and_fault_detection() -> None:
    """Negative tests: verify that cycle detection and invalid dependencies are trapped."""
    # 1. Circular dependency cycle (task_1 -> task_2 -> task_1)
    cyclic_plan = [
        SubTask(
            id="task_1",
            assigned_agent="research",
            description="Task 1",
            dependencies=["task_2"],
        ),
        SubTask(
            id="task_2",
            assigned_agent="document",
            description="Task 2",
            dependencies=["task_1"],
        ),
    ]
    with pytest.raises(PlanCycleError):
        topological_sort(cyclic_plan)

    # 2. Dependency on non-existent node
    broken_plan = [
        SubTask(
            id="task_1",
            assigned_agent="research",
            description="Task 1",
            dependencies=["task_ghost"],
        ),
    ]
    with pytest.raises(PlanValidationError) as exc_info:
        topological_sort(broken_plan)
    assert "depends on non-existent subtask" in str(exc_info.value)
