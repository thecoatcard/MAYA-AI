"""Stitching of a continued reply: overlap trimming and the continuation turn."""

from app.services.stream_continuation import (
    CONTINUE_PROMPT,
    OverlapTrimmer,
    continuation_contents,
    trim_overlap,
)


def test_overlapping_prefix_is_dropped() -> None:
    assert trim_overlap("The capital of France is Pa", "is Paris.") == "ris."
    assert trim_overlap("one two three", "two three four") == " four"


def test_no_overlap_is_kept_whole_and_a_repeated_first_word_is_removed() -> None:
    assert trim_overlap("Hello there", "friend") == "friend"
    assert trim_overlap("it was the", "the end") == "end"
    assert trim_overlap("", "abc") == "abc"


def test_a_continuation_fully_inside_the_tail_adds_nothing() -> None:
    assert trim_overlap("Part one. ", "Part one. ") == ""


def test_trimmer_holds_the_start_then_passes_everything_through() -> None:
    trimmer = OverlapTrimmer("abc def ghi")
    assert trimmer.feed("def ") == ""
    assert trimmer.feed("ghi and more") == ""
    assert trimmer.flush() == " and more"
    assert trimmer.feed("later") == "later"


def test_continuation_contents_appends_the_model_turn_and_the_prompt() -> None:
    base = [{"role": "user", "parts": [{"text": "q"}]}]
    out = continuation_contents(base, "partial")
    assert out[:1] == base
    assert out[1] == {"role": "model", "parts": [{"text": "partial"}]}
    assert out[2]["parts"][0]["text"] == CONTINUE_PROMPT
