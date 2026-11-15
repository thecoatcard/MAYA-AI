"""Planner prompt catalogue and deterministic plan repair (unknown role/tool, redundant save...)."""

from __future__ import annotations

import json
from typing import Any
from unittest.mock import AsyncMock

import pytest

from app.agents.plan_repair import PlanNode, repair_plan
from app.agents.planner import (
    PLANNER_TOOL_NAMES,
    SYSTEM_PLANNING_PROMPT,
    DAGPlanner,
    PlanCycleError,
    PlanDecompositionResponse,
    SubTaskPlanItem,
    build_subtasks,
)
from app.mcp.gateway import BUILTIN_MANIFEST
from app.mcp.permissions import DEFAULT_TOOL_PERMISSIONS


def _item(
    id_: str, role: str, desc: str, deps: list[str] | None = None, **kw: Any
) -> SubTaskPlanItem:
    return SubTaskPlanItem(
        id=id_, assigned_agent=role, description=desc, dependencies=deps or [], **kw
    )


def test_prompt_lists_the_real_roles_and_only_registered_runnable_tools() -> None:
    # ADR-0023: the planner sees three agents plus the text step; old names are only aliases.
    for role in ("research", "builder", "operator", "executor"):
        assert f'"{role}"' in SYSTEM_PLANNING_PROMPT
    for kind in ("document", "file_edit", "project", "email", "pm"):
        assert f'"{kind}"' in SYSTEM_PLANNING_PROMPT
    assert "ONLY reads the user's Google Calendar and Notion" in SYSTEM_PLANNING_PROMPT
    assert "never a separate step" in SYSTEM_PLANNING_PROMPT
    assert "2 to 6 subtasks" in SYSTEM_PLANNING_PROMPT
    assert "Never invent a tool name" in SYSTEM_PLANNING_PROMPT
    manifest = {str(t["name"]) for t in BUILTIN_MANIFEST}
    assert set(PLANNER_TOOL_NAMES) == manifest
    assert manifest <= set(DEFAULT_TOOL_PERMISSIONS)
    for name in manifest:
        assert f'   - "{name}"' in SYSTEM_PLANNING_PROMPT
    for unlisted in ("save_file", "run_sandbox_command", "gmail_send", "git_push"):
        assert f'   - "{unlisted}"' not in SYSTEM_PLANNING_PROMPT
    assert json.dumps(PlanDecompositionResponse.model_json_schema()) in SYSTEM_PLANNING_PROMPT


def test_unknown_agent_role_is_remapped_to_the_executor() -> None:
    nodes = build_subtasks([_item("a", "wizard", "Draft talking points for the launch")])
    assert nodes[0].assigned_agent == "file" and nodes[0].input_data["executor"] == "text"


def test_legacy_browser_role_becomes_research() -> None:
    assert build_subtasks([_item("a", "browser", "Find prices")])[0].assigned_agent == "research"


def test_invented_tool_is_dropped_and_a_real_one_is_kept() -> None:
    plan = build_subtasks(
        [
            _item("a", "research", "Find prices", tool="scrape_everything", tool_args={"u": "x"}),
            _item("b", "research", "Search", tool="web_search", tool_args={"query": "q"}),
        ]
    )
    assert plan[0].tool is None and plan[0].tool_args == {}
    assert plan[1].tool == "web_search" and plan[1].tool_args == {"query": "q"}


def test_redundant_save_step_is_removed_and_dependents_are_rewired() -> None:
    plan = {
        st.id: st
        for st in build_subtasks(
            [
                _item("r", "research", "Research scooters"),
                _item("d", "document", "Create the PDF report", ["r"]),
                _item(
                    "s",
                    "file",
                    "Save the generated 2-page PDF report to the designated workspace",
                    ["d"],
                    tool="save_file",
                    tool_args={"path": "workspace/a.pdf"},
                ),
                _item("m", "executor", "Summarise the outcome for the user", ["s"]),
            ]
        )
    }
    assert set(plan) == {"r", "d", "m"}
    assert plan["m"].dependencies == ["d"]  # was ["s"]


def test_chained_save_steps_collapse_onto_the_document_step() -> None:
    plan = build_subtasks(
        [
            _item("d", "document", "Create the deck"),
            _item("s1", "file", "Export the deck to storage", ["d"]),
            _item("s2", "file", "Upload the deck to the workspace folder", ["s1"]),
            _item("e", "email", "Send the summary to boss@example.test", ["s2"]),
        ]
    )
    assert [st.id for st in plan] == ["d", "e"]
    assert plan[1].dependencies == ["d"]


def test_a_save_step_without_a_document_step_before_it_becomes_a_document_step() -> None:
    plan = build_subtasks(
        [
            _item("r", "research", "Research scooters"),
            _item("s", "file", "Save the findings as a PDF file", ["r"], tool="save_file"),
        ]
    )
    assert plan[1].assigned_agent == "document" and plan[1].tool is None


def test_pm_used_for_content_work_is_remapped_but_a_real_briefing_stays_pm() -> None:
    plan = {
        st.id: st
        for st in build_subtasks(
            [
                _item("o", "pm", "Outline the 8-slide presentation structure for the scooters"),
                _item("b", "pm", "Build my daily briefing from calendar events and Notion tasks"),
            ]
        )
    }
    assert plan["o"].assigned_agent == "file" and plan["o"].input_data["executor"] == "text"
    assert plan["b"].assigned_agent == "pm"


def test_missing_and_self_dependencies_are_repaired_deterministically() -> None:
    nodes, repairs = repair_plan(
        [
            PlanNode("a", "research", "Find facts", ["ghost", "a"]),
            PlanNode("b", "document", "Create the PDF", ["a", "nope"]),
        ],
        PLANNER_TOOL_NAMES,
    )
    assert [n.dependencies for n in nodes] == [[], ["a"]]
    assert len(repairs) == 2


def test_critical_defaults_and_overrides() -> None:
    plan = {
        st.id: st
        for st in build_subtasks(
            [
                _item("r", "research", "Research"),
                _item("o", "executor", "Outline the structure", ["r"]),
                _item("opt", "executor", "Optional notes", ["r"], critical=False),
                _item("d", "document", "Create the PDF", ["o", "opt"]),
                _item("e", "email", "Send it to boss@example.test", ["d"], critical=False),
            ]
        )
    }
    # Web research feeds a later step that can be written from general knowledge: it never
    # sinks the task (search quota), even when the planner said critical.
    assert plan["r"].input_data["critical"] is False
    assert plan["o"].input_data["critical"] is False  # an inner text step may be skipped
    assert plan["opt"].input_data["critical"] is False
    assert plan["d"].input_data["critical"] is True
    assert plan["e"].input_data["critical"] is True  # a side effect is never non-critical
    assert plan["e"].requires_approval is True


def test_a_deliverable_research_step_keeps_its_flag_and_a_planner_true_is_overridden() -> None:
    leaf = build_subtasks([_item("r", "research", "Research solar")])
    assert leaf[0].input_data["critical"] is True  # nothing depends on it: it IS the result
    feeding = build_subtasks(
        [
            _item("r", "research", "Research solar", critical=True),
            _item("d", "document", "Write the PDF", ["r"]),
        ]
    )
    assert {st.id: st.input_data["critical"] for st in feeding} == {"r": False, "d": True}


@pytest.mark.asyncio
async def test_a_cycle_is_rejected_after_one_repair_attempt() -> None:
    cyclic = PlanDecompositionResponse(
        intent="x",
        subtasks=[
            # A text step, not research: "do it" needs no web facts, so a research step would be
            # removed by the general-request repair before the cycle check.
            _item("a", "executor", "A", ["b"]),
            _item("b", "document", "Create B", ["a"]),
        ],
    )
    gateway = AsyncMock()
    gateway.generate_structured.side_effect = [cyclic, cyclic]
    with pytest.raises(PlanCycleError):
        # The request names a PDF: without a format word the file step would be stripped (owner
        # rule: no file unless asked) and the cycle with it, so this case must ask for one.
        await DAGPlanner(gateway=gateway).plan("write a report on solar energy as a pdf")
    assert gateway.generate_structured.call_count == 2


@pytest.mark.asyncio
async def test_planner_applies_the_repairs_before_returning_the_plan() -> None:
    response = PlanDecompositionResponse(
        intent="compare scooters",
        subtasks=[
            # A text step: the research-free repair would drop a research step for this request.
            _item("r", "executor", "Collect scooter facts"),
            _item("o", "pm", "Outline the slides", ["r"]),
            _item("d", "document", "Create the deck", ["r", "o"]),
            _item("s", "file", "Save the deck to the workspace", ["d"], tool="save_file"),
        ],
    )
    gateway = AsyncMock()
    gateway.generate_structured.return_value = response
    _, plan = await DAGPlanner(gateway=gateway).plan("compare scooters in a pptx deck")
    assert [st.id for st in plan] == ["r", "o", "d"]
    assert next(st for st in plan if st.id == "o").assigned_agent == "file"


# ----------------------------------------------------------- research only when web facts are asked
def _planned_research_then_document(instruction_word: str) -> PlanDecompositionResponse:
    return PlanDecompositionResponse(
        intent=instruction_word,
        subtasks=[
            _item("r", "research", "Research the topic"),
            _item("d", "document", "Create the notes", ["r"]),
        ],
    )


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "request_text",
    [
        # A notes request is now planned as a learning book in code (ADR-0031, covered in
        # test_learning_book.py), so the research repair is shown on other general requests.
        # Each names a PDF on purpose: without a format word the document step is stripped (owner
        # rule: no file unless asked, see test_deliverable_intent.py).
        "make a beautiful guide on Angular from basic to advanced, detailed, as a pdf",
        "Java ke baare mein samjhao, pdf banao",
        "explain closures in JavaScript with examples, make a pdf",
    ],
)
async def test_a_general_request_gets_no_research_step(request_text: str) -> None:
    gateway = AsyncMock()
    gateway.generate_structured.return_value = _planned_research_then_document(request_text)
    _, plan = await DAGPlanner(gateway=gateway).plan(request_text)
    assert [st.id for st in plan] == ["d"]
    assert plan[0].assigned_agent == "document"
    assert plan[0].dependencies == []  # no edge to the removed research step


@pytest.mark.asyncio
async def test_a_request_that_asks_for_sources_keeps_its_research_step() -> None:
    request = "latest Java 25 features with sources as a pdf"
    gateway = AsyncMock()
    gateway.generate_structured.return_value = _planned_research_then_document(request)
    _, plan = await DAGPlanner(gateway=gateway).plan(request)
    assert [st.id for st in plan] == ["r", "d"]
    assert plan[1].dependencies == ["r"]


def test_a_general_plan_with_only_research_gets_a_document_step() -> None:
    from app.agents.plan_repair import general_request_repairs
    from app.agents.state import SubTask

    nodes = [
        SubTask(
            id="step_1",
            assigned_agent="research",
            description="Find Java basics",
            dependencies=[],
            input_data={"critical": True},
        ),
    ]
    kept = general_request_repairs(nodes, "Java ke notes banao")
    assert len(kept) == 1
    assert kept[0].assigned_agent == "document"
    assert kept[0].dependencies == []
    assert kept[0].description == "Java ke notes banao"


def test_general_repair_leaves_a_web_fact_plan_and_non_web_research_alone() -> None:
    from app.agents.plan_repair import general_request_repairs
    from app.agents.state import SubTask

    nodes = [
        SubTask(
            id="step_1",
            assigned_agent="research",
            description="latest EV prices",
            dependencies=[],
            input_data={"critical": True},
        ),
        SubTask(
            id="step_2",
            assigned_agent="document",
            description="make the notes",
            dependencies=["step_1"],
            input_data={"critical": True},
        ),
    ]
    assert general_request_repairs(nodes, "latest EV prices in notes") is nodes
    # A research step that calls a repository tool is not a web search: it is never removed here.
    tool_nodes = [
        SubTask(
            id="step_1",
            assigned_agent="research",
            description="collect repo",
            dependencies=[],
            input_data={"critical": True},
            tool="github_review_repo",
        ),
        SubTask(
            id="step_2",
            assigned_agent="document",
            description="write review",
            dependencies=["step_1"],
            input_data={"critical": True},
        ),
    ]
    assert [n.id for n in general_request_repairs(tool_nodes, "review the notes")] == [
        "step_1",
        "step_2",
    ]
