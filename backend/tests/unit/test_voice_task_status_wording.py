"""The voice status tool never lets a model read an unfinished or failed task as done."""

import pytest

from app.voice.task_wiring import task_status_statement


@pytest.mark.parametrize("status", ["failed", "FAILED"])
def test_a_failed_task_is_stated_as_failed(status: str) -> None:
    text = task_status_statement(status)
    assert text.startswith("failed:") and "never say it succeeded" in text


@pytest.mark.parametrize("status", ["pending", "planning", "running", "in_progress", "paused"])
def test_an_unfinished_task_is_never_described_as_done(status: str) -> None:
    text = task_status_statement(status)
    assert text.startswith(f"{status}:") and "NOT finished" in text


def test_cancelled_and_completed_are_stated_plainly() -> None:
    assert task_status_statement("cancelled").startswith("cancelled:")
    assert task_status_statement("completed") == "completed: the task finished."
