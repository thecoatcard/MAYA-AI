"""Unified agents (ADR-0023): Research, Builder, Operator resolve to the concrete members."""

from __future__ import annotations

import pytest

from app.agents.plan_repair import PlanNode, repair_plan
from app.agents.planner import SubTaskPlanItem, build_subtasks
from app.agents.unified import (
    ALIASES,
    PLANNER_FACING_ROLES,
    UNIFIED_AGENTS,
    catalogue,
    resolve_member,
    unified_for,
)

TOOLS: frozenset[str] = frozenset()


def test_three_agents_and_every_member_has_one_owner() -> None:
    assert set(UNIFIED_AGENTS) == {"research", "builder", "operator"}
    assert PLANNER_FACING_ROLES == ("research", "builder", "operator", "executor")
    for agent in UNIFIED_AGENTS.values():
        assert agent.capabilities
        for member in agent.members:
            assert unified_for(member) == agent.id
    for old in ("document", "project", "file_edit", "coding", "email", "pm", "browser"):
        assert old in ALIASES


@pytest.mark.parametrize(
    ("role", "description", "data", "expected"),
    [
        ("builder", "Create a 5 slide deck on solar", {}, "document"),
        ("builder", "Build a landing page for my bakery", {}, "project"),
        ("builder", "Fix the typos in the report", {"path": "a.docx"}, "file_edit"),
        ("builder", "anything", {"kind": "file_edit"}, "file_edit"),
        ("builder", "anything", {"kind": "bogus"}, "document"),
        ("operator", "Send the summary to Ravi", {}, "email"),
        ("operator", "Brief me on my calendar", {}, "pm"),
        ("operator", "x", {"kind": "briefing"}, "pm"),
        ("operator", "x", {"kind": "email"}, "email"),
        ("document", "x", {}, "document"),
        ("research", "x", {}, "research"),
    ],
)
def test_resolve_member(
    role: str, description: str, data: dict[str, object], expected: str
) -> None:
    assert resolve_member(role, description, data) == expected


def test_a_model_cannot_pick_a_member_outside_its_agent() -> None:
    # kind "email" on a builder is not a builder kind: it falls back to the default member.
    assert resolve_member("builder", "write a report", {"kind": "email"}) == "document"
    assert resolve_member("operator", "send a note", {"kind": "document"}) == "email"


def test_repair_plan_turns_unified_roles_into_members() -> None:
    nodes, _ = repair_plan(
        [
            PlanNode(id="a", role="research", description="find facts"),
            PlanNode(id="b", role="builder", description="make a pdf", dependencies=["a"]),
            PlanNode(id="c", role="operator", description="email it to Ravi", dependencies=["b"]),
        ],
        TOOLS,
    )
    assert [n.role for n in nodes] == ["research", "document", "email"]


def test_build_subtasks_stores_member_roles_so_stored_tasks_and_enum_still_work() -> None:
    tasks = build_subtasks(
        [
            SubTaskPlanItem(id="s1", assigned_agent="builder", description="build a website"),
            SubTaskPlanItem(id="s2", assigned_agent="document", description="legacy name"),
        ]
    )
    by_id = {t.id: t for t in tasks}
    assert by_id["s1"].assigned_agent == "file" and by_id["s1"].input_data["executor"] == "project"
    assert by_id["s2"].assigned_agent == "document"


def test_catalogue_reports_availability() -> None:
    entries = {e["id"]: e for e in catalogue({"email": True})}
    assert entries["operator"]["available"] is True
    assert entries["builder"]["available"] is False


# ------------------------------------------------------------- project routing (ADR-0023)


def test_a_multi_file_website_request_is_routed_to_the_project_step() -> None:
    for role in ("document", "builder"):
        nodes, repairs = repair_plan(
            [PlanNode(id="a", role=role, description="make a website with html, css and js")],
            TOOLS,
        )
        assert nodes[0].role == "project"
    nodes, _ = repair_plan(
        [PlanNode(id="a", role="document", description="build it")],
        TOOLS,
        user_text="Create a multi-page portfolio website for me",
    )
    assert nodes[0].role == "project"


def test_single_file_requests_are_not_turned_into_projects() -> None:
    for text in ("write a report on solar", "make a single html page", "a 5 slide deck"):
        nodes, _ = repair_plan([PlanNode(id="a", role="document", description=text)], TOOLS)
        assert nodes[0].role == "document"
    # an edit of an existing file stays an edit
    nodes, _ = repair_plan(
        [
            PlanNode(
                id="a",
                role="builder",
                description="update my website",
                input_data={"path": "a.html"},
            )
        ],
        TOOLS,
    )
    assert nodes[0].role == "file_edit"


def test_project_file_types_widen_to_code_but_never_to_programs() -> None:
    from app.agents.specialized.project_writer import ProjectPlan, ProjectProblem, validate_plan

    def plan(*paths: str) -> ProjectPlan:
        return ProjectPlan.model_validate(
            {"project_name": "t", "files": [{"path": p} for p in paths]}
        )

    assert validate_plan(plan("index.html", "src/app.ts", "api/main.py", "data/x.csv", "q.sql"))
    for bad in (
        "run.exe",
        "setup.bat",
        "go.ps1",
        "x.sh",
        "i.msi",
        "a.jar",
        "../evil.py",
        "a/../b.py",
    ):
        with pytest.raises(ProjectProblem):
            validate_plan(plan("index.html", bad))
