"""A task that finished with a failed step carries a plain ``partial`` report, never plain done."""

from __future__ import annotations

from types import SimpleNamespace

from app.db.models import SubtaskStatus
from app.tasks.worker import failed_step_report


def _step(
    description: str, status: SubtaskStatus, error: str | None, agent: str
) -> SimpleNamespace:
    return SimpleNamespace(
        description=description,
        status=status,
        error_message=error,
        assigned_agent=SimpleNamespace(value=agent),
    )


def test_only_failed_steps_are_reported_with_a_plain_reason() -> None:
    steps = [
        _step("Outline", SubtaskStatus.COMPLETED, None, "executor"),
        _step("Chapter 3: Loops", SubtaskStatus.FAILED, "Chapter model\ncall   failed", "executor"),
        _step("Web research", SubtaskStatus.FAILED, None, "research"),
    ]
    report = failed_step_report(steps)

    assert report == [
        {
            "step": "Chapter 3: Loops",
            "error": "Chapter model call failed",
            "deliverable_incomplete": True,
        },
        {"step": "Web research", "error": "failed", "deliverable_incomplete": False},
    ]
    assert failed_step_report([steps[0]]) == []
