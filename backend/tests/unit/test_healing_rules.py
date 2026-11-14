"""Pure self-healing decisions (see app/tasks/healing.py)."""

from __future__ import annotations

from typing import Any

from app.mcp.permissions import SAVE_TOOL_NAMES
from app.tasks import healing

ARTIFACT_OUTPUT = {"artifact": {"file_name": "Report.pdf", "file_type": "pdf"}}


def _decide(**kw: Any) -> healing.HealingDecision | None:
    base: dict[str, Any] = {
        "code": healing.CODE_UNCLASSIFIED_TOOL,
        "role": "file",
        "description": "Save the generated PDF report to the designated workspace",
        "tool": "save_file",
        "dep_outputs": {"d1": ARTIFACT_OUTPUT},
        "save_tools": SAVE_TOOL_NAMES,
        "hops": 0,
    }
    base.update(kw)
    return healing.decide(**base)


def test_a_save_step_with_a_predecessor_artifact_is_skipped() -> None:
    decision = _decide()
    assert decision is not None and decision.action == "skip"
    assert decision.predecessor == "d1" and "already saved by d1" in decision.reason


def test_a_save_step_without_an_artifact_reruns_without_the_tool() -> None:
    decision = _decide(dep_outputs={"d1": {"summary": "text only"}})
    assert decision is not None and decision.action == "rerun_without_tool"


def test_an_invented_tool_on_a_content_step_reruns_without_the_tool() -> None:
    decision = _decide(role="research", description="Find prices", tool="scrape", dep_outputs={})
    assert decision is not None and decision.action == "rerun_without_tool"


def test_a_side_effect_role_with_a_tool_is_never_rewritten() -> None:
    assert _decide(role="email") is None
    assert _decide(role="coding") is None


def test_unsuitable_pm_content_step_is_rerouted_to_the_executor() -> None:
    decision = _decide(
        code=healing.CODE_ALL_SOURCES_FAILED,
        role="pm",
        description="Outline the 8-slide presentation for the scooter comparison",
        tool=None,
    )
    assert decision is not None and decision.action == "reroute_executor"


def test_steps_that_really_need_an_integration_are_not_rerouted() -> None:
    for role, description in (
        ("email", "Write and send the report to boss@example.test"),
        ("pm", "Summarise my calendar meetings for today"),
        ("pm", "Read my Notion tasks and draft a plan"),
        ("document", "Create the PDF report"),
    ):
        decision = _decide(
            code=healing.CODE_AGENT_UNAVAILABLE, role=role, description=description, tool=None
        )
        assert decision is None, (role, description)


def test_healing_is_bounded_to_two_hops() -> None:
    assert _decide(hops=1) is not None
    assert _decide(hops=healing.MAX_HEALING_HOPS) is None


def test_other_failure_codes_are_not_healed() -> None:
    for code in (healing.CODE_RESTRICTED_TOOL, "budget_exceeded", "cancelled", "timeout"):
        assert _decide(code=code) is None


def test_criticality_rules() -> None:
    assert healing.is_critical({}, "research", False) is True  # old plans: critical
    assert healing.is_critical({"critical": False}, "file", False) is False
    assert healing.is_critical({"critical": False}, "email", False) is True
    assert healing.is_critical({"critical": False}, "file", True) is True
    assert healing.is_critical({"critical": False, "requires_approval": True}, "file", False)


def test_recovered_text_is_honest_about_each_route() -> None:
    trail = [
        healing.entry(1, "pm", "executor", "the pm agent could not run this step"),
        healing.entry(2, "save_file", "skipped", "artifact already saved by d1 (Report.pdf)"),
        healing.entry(3, "mystery", "no tool", "tool 'mystery' is not classified"),
    ]
    text = healing.recovered_text(trail)
    assert text.startswith("Recovered: ran as executor instead of pm")
    assert "skipped the step (artifact already saved by d1" in text
    assert "ran without the tool 'mystery'" in text
    assert healing.recovered_text([]) == ""
