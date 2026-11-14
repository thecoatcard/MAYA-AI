"""A page or slide count is a requirement only when the USER stated it.

The reported bug: "ek report PDF banao solar power par" (no count) was built with 1 page, the
model's own planned `pages: 3` was enforced as if the user had asked for it, the first attempt
failed ("built with 1 pages but 3 were asked for") and a second one was needed. The model's
`pages` is now a soft plan (`pages_planned`); the user's message (or the count the user stated
earlier for the document being changed) is the only requirement. Offline: a stand-in renderer
whose page count is fixed, never a real browser.
"""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from app.automation.acceptance import extract_acceptance
from app.services.document_tools import (
    CREATE_DOCUMENT_TOOL,
    TOOL_LOOP_ADDENDUM,
    build_document,
    carried_count,
    long_route,
    planned_count,
    requested_count,
)
from app.tasks.dispatch import DocumentSpec, FleetSupport
from tests.unit.test_page_fit import StepPdf, use_renderer

SOLAR_BLOCKS = [
    {"type": "heading", "level": 1, "text": "Solar power"},
    {"type": "paragraph", "text": "Solar panels turn sunlight into electricity. " * 30},
]
SOLAR_REQUEST = "ek report PDF banao solar power par"
SLIDES = [
    {"layout": "title", "title": "Solar", "subtitle": "An overview"},
    {"layout": "bullets", "title": "Why", "bullets": ["Clean", "Cheap"]},
    {"layout": "bullets", "title": "How", "bullets": ["Panels", "Inverters"]},
    {"layout": "bullets", "title": "Next", "bullets": ["Install", "Maintain"]},
]


def pdf(tmp_path: Path, request: str, pages: Any, **extra: Any) -> Any:
    return build_document(
        file_format="pdf",
        title="Solar",
        spec={"theme": "notes", "blocks": SOLAR_BLOCKS},
        request=request,
        workdir=tmp_path,
        pages=pages,
        **extra,
    )


def deck(tmp_path: Path, request: str, pages: Any, **extra: Any) -> Any:
    return build_document(
        file_format="pptx",
        title="Solar",
        spec={"theme": "midnight", "slides": SLIDES},
        request=request,
        workdir=tmp_path,
        pages=pages,
        **extra,
    )


def test_a_model_planned_count_is_not_enforced_when_the_user_stated_none(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_renderer(monkeypatch, StepPdf(lambda step: 1))
    # length_retry=True is the first-attempt mode that used to raise "built with 1 pages".
    built = pdf(tmp_path, SOLAR_REQUEST, 3, length_retry=True)
    assert built.length is None  # nothing to measure against: no pages_requested
    assert built.pages_planned == 3 and built.pages == 1
    assert not any("asked for" in w for w in built.warnings)


def test_a_user_stated_count_is_still_enforced(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_renderer(monkeypatch, StepPdf(lambda step: 1))
    # Count policy (deliberate update): the user's count is still the measured requirement, but a
    # miss no longer raises "built with 1 pages but 3 were asked for" (which discarded the file):
    # the file is delivered and `length` carries the true numbers, on the first attempt too.
    first = pdf(tmp_path, "3 page ka report PDF banao solar power par", 3, length_retry=True)
    assert first.length is not None and not first.length.exact
    assert "You asked for 3 pages; the file has 1" in first.length.note
    built = pdf(tmp_path, "3 page ka report PDF banao solar power par", 3)
    assert built.length is not None and (built.length.requested, built.length.measured) == (3, 1)
    assert built.pages_planned is None


def test_the_users_number_wins_over_the_models(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_renderer(monkeypatch, StepPdf(lambda step: 3))
    built = pdf(tmp_path, "5 page pdf on solar power", 3)
    assert built.length is not None and built.length.requested == 5
    assert requested_count("pdf", 3, "5 page pdf on solar power") == 5
    assert requested_count("pdf", 12, "a report") is None
    assert planned_count("pdf", 12) == 12 and planned_count("xlsx", 12) is None


def test_a_follow_up_keeps_the_count_the_user_stated_for_that_document(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    use_renderer(monkeypatch, StepPdf(lambda step: 3))
    previous = {"format": "pdf", "title": "Solar", "file_id": "f1", "pages_requested": 5}
    built = pdf(tmp_path, "continue", None, previous=previous, edits_file_id="f1")
    assert built.length is not None and built.length.requested == 5
    # Another document, or a new one with no edit, does not inherit the count.
    assert carried_count(previous, "pdf", "Other", "f2") is None
    # A count in the new message replaces it.
    assert requested_count("pdf", None, "make it 4 pages", 5) == 4


def test_a_deck_count_is_enforced_only_when_the_user_stated_it(tmp_path: Path) -> None:
    # Count policy (deliberate update): 4 slides for a stated 6 used to raise "4 slides but 6 were
    # asked for". The deck is now fitted in code to exactly 6 (labelled Appendix slides here).
    fitted = deck(tmp_path, "6 slide deck on solar", None, length_retry=True)
    assert fitted.pages == 6 and fitted.length is not None and fitted.length.exact
    built = deck(tmp_path, "solar deck banao", 12, length_retry=True)
    assert built.length is None and built.pages_planned == 12 and built.pages == 4


def test_a_model_chosen_count_never_starts_a_background_job() -> None:
    assert long_route("pdf", 12, "solar power par report banao") is None
    route = long_route("pdf", 12, "12 page report on solar power")
    assert route is not None and route.pages == 12


def test_the_prompt_tells_the_model_to_pass_pages_only_for_a_user_stated_count() -> None:
    description = CREATE_DOCUMENT_TOOL.parameters["properties"]["pages"]["description"]
    assert "USER stated" in description and "Leave it out" in description
    assert "Pass 'pages' ONLY when the user's own message states" in TOOL_LOOP_ADDENDUM
    assert "leave 'pages' out" in TOOL_LOOP_ADDENDUM


# ------------------------------------------------------------------------------- task path
def spec_of(file_type: str = "pptx") -> DocumentSpec:
    return DocumentSpec(
        file_type=file_type,
        filename="solar.pptx",
        title="Solar",
        theme="midnight",
        slides=SLIDES,
    )


def test_task_path_expected_slides_come_from_the_users_words_not_the_planner_rewrite() -> None:
    rewritten = "Create a 12 slide deck about solar power"  # the planner's own number
    fields = FleetSupport._validate_spec(spec_of(), rewritten, "solar deck banao")
    assert "expected_slides" not in fields
    fields = FleetSupport._validate_spec(spec_of(), rewritten, "6 slide solar deck")
    assert fields["expected_slides"] == 6
    # Without the user's text (a task submitted directly) the description is the user's text.
    assert FleetSupport._validate_spec(spec_of(), "a 7 slide deck")["expected_slides"] == 7


def test_task_path_acceptance_reads_only_the_users_instruction() -> None:
    assert extract_acceptance("ek report PDF banao solar power par") == {}
    assert extract_acceptance("3 page ka report PDF banao") == {"pages": 3}
