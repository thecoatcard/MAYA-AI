"""Edit preservation: the comparator, and the edit step refusing a version that changes facts."""

from __future__ import annotations

from typing import Any

from pydantic import BaseModel

from app.automation.file_edit import (
    compare_preservation,
    requests_preservation,
    requests_redesign,
)
from app.files.edit_step import TextEditPlan, run_file_edit_step
from app.tasks.worker import NonRetryableSubtaskError
from tests.support.files import make_job

ORIGINAL = (
    "Revenue grew 42 percent in 2026 across Europe and Asia. The team hired 17 engineers "
    "in Berlin, shipped the new billing portal in March, and reduced churn to 3 percent. "
    "Contact finance@example.com or see https://example.com/report for the full table."
)


class FakeGateway:
    def __init__(self, *answers: BaseModel) -> None:
        self.answers = list(answers)
        self.prompts: list[str] = []

    async def generate_structured(self, **kwargs: Any) -> Any:
        self.prompts.append(str(kwargs["contents"][0]))
        return self.answers.pop(0)


def test_preservation_requests_are_detected() -> None:
    assert requests_preservation("keep all the information, just tidy the layout")
    assert requests_preservation("don't change the content")
    assert requests_preservation("make it professional")
    assert requests_preservation("Keep the numbers the same")
    assert not requests_preservation("add a slide about sales")


def test_redesign_requests_are_detected() -> None:
    assert requests_redesign("redesign this PDF")
    assert requests_redesign("please rebuild it with a new look")
    assert not requests_redesign("summarise the report")


def test_identical_text_passes() -> None:
    report = compare_preservation(ORIGINAL, ORIGINAL)
    assert report.ok and report.reasons == []


def test_reflow_and_rewrapping_are_allowed() -> None:
    reflowed = ORIGINAL.replace(" ", "\n", 12)  # line breaks only, same words
    assert compare_preservation(ORIGINAL, reflowed).ok


def test_changed_number_is_refused() -> None:
    report = compare_preservation(ORIGINAL, ORIGINAL.replace("42 percent", "43 percent"))
    assert not report.ok
    assert any("numbers changed" in r and "'42'" in r for r in report.reasons)


def test_changed_date_is_refused() -> None:
    before = "The contract starts on 2026-01-05 and ends in 2027."
    after = before.replace("2026-01-05", "2026-01-06")
    assert not compare_preservation(before, after).ok


def test_changed_link_is_refused() -> None:
    after = ORIGINAL.replace("https://example.com/report", "https://example.org/report")
    report = compare_preservation(ORIGINAL, after)
    assert not report.ok and any("links changed" in r for r in report.reasons)


def test_added_name_is_refused() -> None:
    after = ORIGINAL.replace("in Berlin", "in Berlin and Madrid")
    report = compare_preservation(ORIGINAL, after)
    assert not report.ok and any("names changed" in r and "Madrid" in r for r in report.reasons)


def test_dropped_content_beyond_the_threshold_is_refused() -> None:
    kept = ORIGINAL.split(". ")[0]
    report = compare_preservation(ORIGINAL, kept)
    assert not report.ok and report.missing_word_ratio > 0.10


def test_a_light_rewording_with_the_facts_kept_is_allowed() -> None:
    reworded = ORIGINAL.replace("shipped", "delivered")
    report = compare_preservation(ORIGINAL, reworded)
    assert report.ok, report.reasons


def test_page_markers_do_not_count_as_facts() -> None:
    before = "== Page 1\nRevenue grew 42 percent in 2026."
    after = "Revenue grew 42 percent in 2026."
    assert compare_preservation(before, after).ok


async def test_edit_step_refuses_a_version_that_changes_a_number(file_env: Any) -> None:
    _services, folder = file_env
    (folder / "notes.md").write_text(ORIGINAL + "\n")
    changed = ORIGINAL.replace("42 percent", "43 percent")
    gateway = FakeGateway(
        TextEditPlan(new_content=changed + "\n"), TextEditPlan(new_content=changed + "\n")
    )
    try:
        await run_file_edit_step(
            gateway,
            make_job(),
            {
                "path": "notes.md",
                "description": "Make it professional",
                "requirements": "keep all the information and numbers",
            },
        )
        raise AssertionError("the changed number was accepted")
    except NonRetryableSubtaskError as err:
        assert "numbers changed" in str(err) and "'42'" in str(err)
    assert (folder / "notes.md").read_text() == ORIGINAL + "\n"  # original untouched
    assert not any(p.name.startswith("notes (edited") for p in folder.iterdir())
    assert "refused" in gateway.prompts[1].lower() or "rejected" in gateway.prompts[1].lower()


async def test_edit_step_accepts_the_retry_that_keeps_the_facts(file_env: Any) -> None:
    _services, folder = file_env
    (folder / "notes.md").write_text(ORIGINAL + "\n")
    wrong = ORIGINAL.replace("42 percent", "43 percent")
    right = ORIGINAL.replace("shipped", "delivered")
    gateway = FakeGateway(
        TextEditPlan(new_content=wrong + "\n"), TextEditPlan(new_content=right + "\n")
    )
    output = await run_file_edit_step(
        gateway,
        make_job(),
        {"path": "notes.md", "description": "Tidy the wording", "requirements": "keep the facts"},
    )
    assert (folder / "notes.md").read_text() == right + "\n"
    assert not (folder / "notes (edited).md").exists()
    assert output["proof"]["status"] == "verified"


async def test_edit_step_without_a_preservation_request_is_not_checked(file_env: Any) -> None:
    _services, folder = file_env
    (folder / "notes.md").write_text(ORIGINAL + "\n")
    gateway = FakeGateway(TextEditPlan(new_content="Short replacement.\n"))
    output = await run_file_edit_step(
        gateway, make_job(), {"path": "notes.md", "description": "Replace the whole text"}
    )
    assert output["result"]["changes"] == 1
    assert (folder / "notes.md").read_text() == "Short replacement.\n"
    assert not (folder / "notes (edited).md").exists()
