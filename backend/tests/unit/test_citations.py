"""Reply-wide source numbers and the clean Sources list."""

from app.services.citations import SOURCES_MARKER, SourceBook


def test_numbers_continue_across_searches_and_urls_are_deduplicated() -> None:
    book = SourceBook()
    first = [{"n": 1, "title": "A", "url": "https://www.a.test/x"}]
    second = [
        {"n": 1, "title": "B", "url": "https://b.test/y"},
        {"n": 2, "title": "A again", "url": "https://www.a.test/x"},
    ]
    book.renumber(first)
    book.renumber(second)
    assert [s["n"] for s in second] == [2, 1]


def test_block_lists_only_cited_sources_with_host_and_never_invents_one() -> None:
    book = SourceBook()
    book.renumber(
        [
            {"title": "Reuters [live]", "url": "https://www.reuters.com/a"},
            {"title": "MSN", "url": "https://www.msn.com/b"},
            {"title": "bad", "url": "javascript:alert(1)"},
        ]
    )
    text = book.block("Oil rose [1] and [7].")
    assert text.startswith("\n\n" + SOURCES_MARKER)
    assert "1. [Reuters (live)](https://www.reuters.com/a) · reuters.com" in text
    assert "msn" not in text.lower() and "javascript" not in text and "7." not in text
    assert "2." in book.block("no markers at all")  # none cited: all real sources are listed


def test_empty_book_adds_nothing() -> None:
    assert SourceBook().block("x [1]") == ""
