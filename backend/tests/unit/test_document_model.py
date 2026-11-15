"""The structured document and deck models: caps, unknown types, image rules, legacy mapping."""

from __future__ import annotations

from typing import Any

import pytest
from pydantic import ValidationError

from app.automation import model
from app.automation.model import (
    Deck,
    Document,
    blocks_from_legacy_sections,
    deck_from_dict,
    document_from_dict,
    document_from_legacy,
    format_validation_error,
)
from tests.support.documents import TINY_PNG


def _doc(blocks: list[dict[str, Any]], **extra: Any) -> dict[str, Any]:
    return {"title": "T", "blocks": blocks, **extra}


def test_minimal_document_validates_with_defaults() -> None:
    doc = Document.model_validate(_doc([{"type": "paragraph", "text": "hi"}]))
    assert doc.theme == "executive"
    assert doc.blocks[0].type == "paragraph"


def test_every_block_type_validates() -> None:
    blocks: list[dict[str, Any]] = [
        {"type": "heading", "level": 3, "text": "h"},
        {"type": "paragraph", "text": "p"},
        {"type": "bullets", "items": ["a"]},
        {"type": "numbered", "items": ["a"]},
        {"type": "table", "headers": ["a"], "rows": [["1"]], "caption": "c"},
        {"type": "callout", "kind": "success", "text": "t"},
        {"type": "metrics", "items": [{"value": "1", "label": "x"}]},
        {"type": "quote", "text": "q", "by": "me"},
        {"type": "image", "src": TINY_PNG, "alt": "a"},
        {"type": "code", "language": "python", "text": "x=1"},
        {"type": "page_break"},
    ]
    assert {b["type"] for b in blocks} == set(model.BLOCK_TYPES)
    assert len(Document.model_validate(_doc(blocks)).blocks) == len(blocks)


def test_unknown_block_type_is_rejected() -> None:
    with pytest.raises(ValidationError) as err:
        Document.model_validate(_doc([{"type": "html", "text": "<b>x</b>"}]))
    assert "html" in str(err.value)


def test_unknown_field_is_rejected() -> None:
    with pytest.raises(ValidationError):
        Document.model_validate(_doc([{"type": "paragraph", "text": "p", "style": "x"}]))


def test_unknown_theme_is_rejected_with_allowed_list() -> None:
    with pytest.raises(ValidationError) as err:
        Document.model_validate(_doc([{"type": "paragraph", "text": "p"}], theme="neon"))
    message = format_validation_error(err.value)
    assert "unknown theme 'neon'" in message
    assert "executive" in message and "midnight" in message


@pytest.mark.parametrize(
    "src",
    [
        "http://example.com/a.png",
        "https://example.com/a.png",
        "//example.com/a.png",
        "file:///etc/passwd.png",
        "javascript:alert(1)",
        "/etc/a.png",
        "C:/a.png",
        "..\\secret.png",
        "images/../../secret.png",
        "images/a.svg",
        "data:image/svg+xml;base64,PHN2Zz48L3N2Zz4=",
        "data:text/html;base64,PGI+eDwvYj4=",
        "",
    ],
)
def test_image_sources_other_than_data_uri_or_relative_path_are_rejected(src: str) -> None:
    with pytest.raises(ValidationError):
        Document.model_validate(_doc([{"type": "image", "src": src, "alt": "x"}]))


@pytest.mark.parametrize("src", [TINY_PNG, "images/chart.png", "a/b/c.JPG"])
def test_image_sources_data_uri_and_workspace_path_are_accepted(src: str) -> None:
    doc = Document.model_validate(_doc([{"type": "image", "src": src, "alt": "x"}]))
    assert doc.blocks[0].src == src  # type: ignore[union-attr]


def test_block_count_cap() -> None:
    blocks = [{"type": "paragraph", "text": "p"}] * (model.MAX_BLOCKS + 1)
    with pytest.raises(ValidationError) as err:
        Document.model_validate(_doc(blocks))
    assert "blocks" in format_validation_error(err.value)


def test_paragraph_length_cap() -> None:
    long_text = "x" * (model.MAX_PARAGRAPH_CHARS + 1)
    with pytest.raises(ValidationError):
        Document.model_validate(_doc([{"type": "paragraph", "text": long_text}]))


def test_table_caps_and_shape() -> None:
    too_many_rows = [["1"]] * (model.MAX_TABLE_ROWS + 1)
    with pytest.raises(ValidationError):
        Document.model_validate(_doc([{"type": "table", "headers": ["a"], "rows": too_many_rows}]))
    with pytest.raises(ValidationError):
        headers = [str(i) for i in range(model.MAX_TABLE_COLS + 1)]
        Document.model_validate(_doc([{"type": "table", "headers": headers, "rows": []}]))
    with pytest.raises(ValidationError) as err:
        Document.model_validate(
            _doc([{"type": "table", "headers": ["a", "b"], "rows": [["only one"]]}])
        )
    assert "row 1 has 1 cells but there are 2 headers" in format_validation_error(err.value)


def test_total_text_cap() -> None:
    # Enough 5000-character paragraphs to pass the document text budget (and stay under the
    # block cap, so it is the text budget that rejects it).
    count = model.MAX_DOCUMENT_CHARS // model.MAX_PARAGRAPH_CHARS + 1
    assert count <= model.MAX_BLOCKS
    blocks = [{"type": "paragraph", "text": "x" * model.MAX_PARAGRAPH_CHARS}] * count
    with pytest.raises(ValidationError) as err:
        Document.model_validate(_doc(blocks))
    assert "limit is" in format_validation_error(err.value)


def test_heading_level_bounds_and_strict_types() -> None:
    with pytest.raises(ValidationError):
        Document.model_validate(_doc([{"type": "heading", "level": 4, "text": "h"}]))
    with pytest.raises(ValidationError):
        Document.model_validate(_doc([{"type": "heading", "level": "2", "text": "h"}]))


def test_numbers_are_accepted_where_text_is_expected_for_metrics_and_cells() -> None:
    doc = Document.model_validate(
        _doc(
            [
                {"type": "metrics", "items": [{"value": 42, "label": "answers"}]},
                {"type": "table", "headers": ["n"], "rows": [[1.5]]},
            ]
        )
    )
    assert doc.blocks[0].items[0].value == "42"  # type: ignore[union-attr]


def test_document_from_dict_drops_null_optionals() -> None:
    doc = document_from_dict(
        _doc([{"type": "quote", "text": "q", "by": None}], subtitle=None, author=None)
    )
    assert doc.subtitle is None


def test_format_validation_error_is_short_and_precise() -> None:
    blocks = [{"type": "paragraph", "text": ""}] * 8
    with pytest.raises(ValidationError) as err:
        Document.model_validate(_doc(blocks))
    message = format_validation_error(err.value, limit=3)
    assert message.count(";") == 3
    assert "more problem(s)" in message
    assert message.startswith("blocks.0.paragraph.text")


# ----------------------------------------------------------------------------- deck
def _slide(**fields: Any) -> dict[str, Any]:
    return {"theme": "minimal", "title": "Deck", "slides": [fields]}


def test_every_slide_layout_is_covered_by_a_valid_example() -> None:
    examples: dict[str, dict[str, Any]] = {
        "cover": {"layout": "cover", "title": "T"},
        "section": {"layout": "section", "title": "S", "number": "02"},
        "comparison": {
            "layout": "comparison",
            "title": "V",
            "left": {"heading": "Before", "bullets": ["a"]},
            "right": {"heading": "After", "bullets": ["b"]},
        },
        "code": {
            "layout": "code",
            "title": "C",
            "code": {"language": "python", "text": "print(1)"},
        },
        "bullets": {"layout": "bullets", "title": "B", "bullets": ["a"]},
        "two_column": {
            "layout": "two_column",
            "title": "C",
            "left": {"bullets": ["a"]},
            "right": {"bullets": ["b"]},
        },
        "cards": {"layout": "cards", "title": "C", "cards": [{"title": "a", "body": "b"}]},
        "stat_callout": {
            "layout": "stat_callout",
            "title": "M",
            "metrics": [{"value": "1", "label": "l"}],
        },
        "quote": {"layout": "quote", "quote": "q"},
        "table": {"layout": "table", "title": "T", "table": {"headers": ["a"], "rows": [["1"]]}},
        "image": {"layout": "image", "image": {"src": TINY_PNG, "alt": "x"}},
        "timeline": {
            "layout": "timeline",
            "title": "T",
            "steps": [{"title": "a"}, {"title": "b"}],
        },
        "closing": {"layout": "closing", "title": "Bye"},
        "hero_cover": {"layout": "hero_cover", "title": "H"},
        "section_break": {"layout": "section_break", "title": "S"},
        "stat_grid": {
            "layout": "stat_grid",
            "title": "G",
            "metrics": [{"value": "1", "label": "l", "progress": 40}],
        },
        "icon_cards": {
            "layout": "icon_cards",
            "title": "I",
            "cards": [{"title": "a", "body": "b", "icon": "rocket"}],
        },
        "process": {"layout": "process", "title": "P", "steps": [{"title": "a"}, {"title": "b"}]},
        "image_text": {
            "layout": "image_text",
            "title": "IT",
            "image": {"src": TINY_PNG, "alt": "x"},
        },
        "chart": {
            "layout": "chart",
            "title": "C",
            "chart": {
                "type": "doughnut",
                "categories": ["a", "b"],
                "series": [{"name": "s", "values": [1, 2]}],
            },
        },
    }
    assert set(examples) == set(model.SLIDE_LAYOUTS)
    for layout, slide in examples.items():
        assert Deck.model_validate(_slide(**slide)).slides[0].layout == layout


@pytest.mark.parametrize(
    ("fields", "needle"),
    [
        ({"layout": "bullets", "title": "x"}, "needs at least one bullet"),
        ({"layout": "bullets", "bullets": ["a"]}, "needs a title"),
        ({"layout": "two_column", "title": "x", "left": {"bullets": ["a"]}}, "'right' column"),
        ({"layout": "cards", "title": "x"}, "at least one card"),
        ({"layout": "stat_callout", "title": "x"}, "at least one metric"),
        ({"layout": "comparison", "title": "x", "left": {"bullets": ["a"]}}, "both 'left'"),
        ({"layout": "code", "title": "x"}, "needs a 'code' block"),
        ({"layout": "quote"}, "needs 'quote' text"),
        ({"layout": "table", "title": "x"}, "needs a 'table'"),
        ({"layout": "image"}, "needs an 'image'"),
        ({"layout": "timeline", "title": "x", "steps": [{"title": "a"}]}, "at least 2 steps"),
        ({"layout": "carousel", "title": "x"}, "carousel"),
    ],
)
def test_slide_layout_requirements_give_precise_errors(fields: dict[str, Any], needle: str) -> None:
    with pytest.raises(ValidationError) as err:
        Deck.model_validate(_slide(**fields))
    assert needle in str(err.value)


def test_deck_caps() -> None:
    slides = [{"layout": "section", "title": "s"}] * (model.MAX_SLIDES + 1)
    with pytest.raises(ValidationError):
        Deck.model_validate({"title": "D", "slides": slides})
    with pytest.raises(ValidationError):
        Deck.model_validate(
            _slide(layout="bullets", title="t", bullets=["a"] * (model.MAX_SLIDE_BULLETS + 1))
        )
    with pytest.raises(ValidationError):
        Deck.model_validate(
            _slide(layout="bullets", title="t", bullets=["a" * (model.MAX_SLIDE_BULLET_CHARS + 1)])
        )
    with pytest.raises(ValidationError):
        Deck.model_validate(_slide(layout="section", title="t" * (model.MAX_SLIDE_TITLE_CHARS + 1)))
    with pytest.raises(ValidationError):
        cards = [{"title": "a"}] * (model.MAX_SLIDE_CARDS + 1)
        Deck.model_validate(_slide(layout="cards", title="t", cards=cards))


def test_deck_image_rules_match_document_rules() -> None:
    with pytest.raises(ValidationError):
        Deck.model_validate(
            _slide(layout="image", title="t", image={"src": "http://x/a.png", "alt": "a"})
        )


def test_deck_from_dict_prunes_nulls() -> None:
    deck = deck_from_dict(
        {"title": "D", "slides": [{"layout": "quote", "quote": "q", "by": None, "notes": None}]}
    )
    assert deck.slides[0].by is None


# ----------------------------------------------------------------------------- legacy
def test_legacy_sections_map_to_blocks() -> None:
    blocks = blocks_from_legacy_sections(
        [
            {"type": "heading1", "text": "A"},
            {"type": "heading2", "text": "B"},
            {"type": "heading3", "text": "C"},
            {"type": "paragraph", "text": "p"},
            {"type": "bullet", "items": ["x", "y"]},
            {"type": "callout", "text": "c"},
            {"type": "page_break"},
            {"type": "table", "headers": ["h1", "h2"], "rows": [["1"], ["2", "3"]]},
            {"type": "paragraph", "text": "   "},
        ]
    )
    assert [b["type"] for b in blocks] == [
        "heading",
        "heading",
        "heading",
        "paragraph",
        "bullets",
        "callout",
        "page_break",
        "table",
    ]
    assert [b.get("level") for b in blocks[:3]] == [1, 2, 3]
    assert blocks[-1]["rows"] == [["1", ""], ["2", "3"]]  # short rows are padded


def test_legacy_unknown_section_type_is_an_error() -> None:
    with pytest.raises(ValueError, match="unsupported legacy section type 'video'"):
        blocks_from_legacy_sections([{"type": "video", "text": "x"}])


def test_document_from_legacy_handles_empty_sections() -> None:
    doc = document_from_legacy("Title", [], subtitle="Sub", theme="midnight")
    assert doc.theme == "midnight" and doc.subtitle == "Sub" and doc.blocks
