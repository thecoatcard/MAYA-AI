"""Acceptance criteria: extraction from English, Hinglish and number words; tolerances; planning."""

from __future__ import annotations

import pytest

from app.automation.acceptance import (
    MAX_CHAPTERS,
    MAX_PIPELINE_PAGES,
    check_acceptance,
    extract_acceptance,
    is_long_document,
    normalize_acceptance,
    pages_bounds,
    plan_chapters,
    requested_pages,
    scaled_document_timeout,
)

OWNER_REQUEST = (
    "make a notes on java from basic to advance make sure pdf is of 50 pages and well "
    "formatted and well documented"
)


@pytest.mark.parametrize(
    ("text", "expected"),
    [
        (OWNER_REQUEST, {"pages": 50}),
        ("Create a 50-page PDF about Rust", {"pages": 50}),
        ("50 page ka pdf banao java par", {"pages": 50}),
        ("mujhe 12 pages ki notes chahiye", {"pages": 12}),
        ("fifty pages of notes", {"pages": 50}),
        ("a twenty five page guide", {"pages": 25}),
        ("one hundred pages", {"pages": 100}),
        ("make an 8-slide deck on AI", {"slides": 8}),
        ("10 slide ki ppt banao", {"slides": 10}),
        ("a deck with twelve slides", {"slides": 12}),
        ("write a 2,000 word essay", {"words": 2000}),
        ("a 3k-word article", {"words": 3000}),
        ("5000 words on history", {"words": 5000}),
        ("an excel with 3 sheets", {"sheets": 3}),
        ("a 40-50 pages report", {"pages": 45}),
        ("pdf of 20 pages and 5 slides", {"pages": 20, "slides": 5}),
        ("२० पेज का notes", {"pages": 20}),  # 20 पेज (Hindi digits)
    ],
)
def test_extracts_only_what_the_user_stated(text: str, expected: dict[str, int]) -> None:
    assert extract_acceptance(text) == expected


@pytest.mark.parametrize(
    "text",
    [
        "see page 5 of chapter 3",
        "turn to page fifty",
        "explain java in detail",
        "",
        "a few pages please",
        "0 pages",
        "5000000 pages",
    ],
)
def test_no_quantity_when_none_is_stated(text: str) -> None:
    assert extract_acceptance(text) == {}


def test_none_text_is_empty() -> None:
    assert extract_acceptance(None) == {}


def test_normalize_drops_unknown_and_bad_values() -> None:
    assert normalize_acceptance({"pages": 50, "slides": True, "x": 3, "words": -1}) == {"pages": 50}
    assert normalize_acceptance(None) == {}
    assert normalize_acceptance({"pages": 12.0}) == {"pages": 12}


def test_long_document_threshold_is_pages_above_five_or_words_above_3500() -> None:
    # Owner decision (Markdown notes): up to 5 pages stay ONE call, 6 and above are a job (was 8).
    assert not is_long_document({"pages": 5})
    assert is_long_document({"pages": 6})
    assert not is_long_document({"pages": 4})
    assert is_long_document({"pages": 9})
    assert not is_long_document({"words": 3500})
    assert is_long_document({"words": 3501})
    assert not is_long_document({"slides": 40})
    assert requested_pages({"words": 4500}) == 10


def test_chapters_follow_what_a_model_call_really_writes() -> None:
    # chapters = ceil(pages x 310 words x 0.85 / 900), from 3 to 16.
    for pages, chapters in [(9, 3), (12, 4), (20, 6), (50, 15), (100, 16)]:
        assert plan_chapters({"pages": pages}).chapters == chapters, pages
    plan = plan_chapters({"pages": 50})
    assert 2.5 <= plan.pages_per_chapter <= 4
    assert plan.target_pages == 50 and not plan.capped


def test_words_per_page_is_calibrated_per_theme() -> None:
    from app.automation.acceptance import THEME_WORDS_PER_PAGE, words_per_page

    # Calibrated for the four document presets; the slide-theme library (deck_themes.py) is for
    # decks and falls back to the notes density in words_per_page.
    from app.automation.themes import DOCUMENT_THEME_NAMES as THEME_NAMES

    assert set(THEME_WORDS_PER_PAGE) == set(THEME_NAMES)
    assert words_per_page("notes") == THEME_WORDS_PER_PAGE["notes"] == 310
    assert words_per_page("unknown-theme") == 310


def test_fan_out_is_capped_and_oversize_requests_are_marked() -> None:
    big = plan_chapters({"pages": 100})
    assert big.chapters == MAX_CHAPTERS
    huge = plan_chapters({"pages": 400})
    assert huge.chapters == MAX_CHAPTERS and huge.capped and huge.target_pages == MAX_PIPELINE_PAGES
    assert plan_chapters({"pages": 9}).chapters >= 2


def test_pages_tolerance_is_minus_ten_plus_twenty_five_percent() -> None:
    assert pages_bounds(50) == (45, 63)
    assert pages_bounds(10) == (9, 13)
    low, high = pages_bounds(3)
    assert low <= 3 <= high and high == 4


def test_pages_check_and_the_honest_message() -> None:
    assert check_acceptance({"pages": 50}, {"pages": 52}).met
    assert check_acceptance({"pages": 50}, {"pages": 45}).met
    assert check_acceptance({"pages": 50}, {"pages": 63}).met
    short = check_acceptance({"pages": 50}, {"pages": 38})
    assert not short.met and not short.severe
    assert short.message == "Requested 50 pages, produced 38"
    assert short.as_proof() == {
        "requested": {"pages": 50},
        "actual": {"pages": 38},
        "met": False,
        # Count policy (deliberate update): a miss is recorded as a WARNING with a plain note,
        # never as a failure; the machine message stays for the heal prompts.
        "status": "warning",
        "message": "Requested 50 pages, produced 38",
        "note": "You asked for 50 pages; the file has 38 pages.",
    }
    owner_case = check_acceptance({"pages": 50}, {"pages": 3})
    assert owner_case.severe and owner_case.message == "Requested 50 pages, produced 3"
    assert not check_acceptance({"pages": 50}, {"pages": 64}).met


def test_slides_exact_and_words_at_85_percent_and_unmeasured_is_not_claimed() -> None:
    assert check_acceptance({"slides": 8}, {"slides": 8}).met
    assert (
        check_acceptance({"slides": 8}, {"slides": 7}).message == "Requested 8 slides, produced 7"
    )
    assert check_acceptance({"words": 1000}, {"words": 850}).met
    assert not check_acceptance({"words": 1000}, {"words": 849}).met
    assert check_acceptance({"sheets": 3}, {"sheets": 2}).shortfalls == [
        "Requested 3 sheets, produced 2"
    ]
    # A DOCX has no measurable page count: nothing is checked and nothing is claimed.
    unmeasured = check_acceptance({"pages": 50}, {"pages": None, "words": 9000})
    assert unmeasured.met and unmeasured.requested == {}


def test_document_timeout_scales_with_pages_and_is_capped() -> None:
    assert scaled_document_timeout(None) == 240.0
    assert scaled_document_timeout(3) == 240.0
    assert scaled_document_timeout(50) == 360.0
    assert scaled_document_timeout(100) == 600.0
    assert scaled_document_timeout(5000) == 600.0
