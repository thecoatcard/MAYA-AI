"""Deck design and layouts: every layout renders, the AI's design reaches the XML, and every
repair is reported instead of silent."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest
from pptx import Presentation
from pptx.enum.dml import MSO_FILL_TYPE

from app.automation.deck_checks import LAYOUT_ITEM_LIMITS, check_deck, halve
from app.automation.deck_html import render_deck_html, write_deck_html
from app.automation.design import DECORATIONS, DeckStyle, resolve_deck
from app.automation.model import SLIDE_LAYOUTS, Deck, Slide, deck_from_dict
from app.automation.pptx_engine import create_deck
from app.automation.verifiers.pptx_verifier import verify_pptx
from tests.support.documents import TINY_PNG

KIDS: dict[str, Any] = {
    "palette": {
        "background": "#FFF7E0",
        "background_end": "#FFE4F1",
        "text": "#3B1F57",
        "muted": "#6B4E8A",
        "accent": "#FF7A00",
        "accent2": "#00B4B4",
        "primary": "#7B2CBF",
        "link": "#0077B6",
        "surface": "#FFFFFF",
    },
    "heading_font": "Comic Sans MS",
    "body_font": "Trebuchet MS",
    "shape_style": "soft-shadow",
    "corner_radius": 20,
    "decorations": ["corner_circle", "grid_dots", "top_bar"],
    "title_scale": 1.15,
    "footer_text": "Kids Club",
}
NAVY: dict[str, Any] = {
    "palette": {
        "background": "#0B1F3A",
        "text": "#E6EEF8",
        "muted": "#9FB3C8",
        "accent": "#38BDF8",
        "accent2": "#6366F1",
        "primary": "#1E3A8A",
        "link": "#7DD3FC",
        "surface": "#13294B",
    },
    "heading_font": "Calibri",
    "body_font": "Segoe UI",
    "shape_style": "glass",
    "corner_radius": 4,
    "decorations": ["side_bar", "divider_line"],
    "slide_number": False,
}

SAMPLES: dict[str, dict[str, Any]] = {
    "cover": {"layout": "cover", "title": "Cover", "subtitle": "Sub"},
    "section": {"layout": "section", "title": "Section", "number": "02"},
    "bullets": {"layout": "bullets", "title": "Bullets", "bullets": ["one", "two", "three"]},
    "two_column": {
        "layout": "two_column",
        "title": "Two",
        "left": {"heading": "L", "bullets": ["a"]},
        "right": {"heading": "R", "bullets": ["b"]},
    },
    "cards": {
        "layout": "cards",
        "title": "Cards",
        "cards": [
            {"title": "A", "body": "x", "glyph": "01"},
            {"title": "B", "body": "y", "glyph": "02"},
            {"title": "C", "body": "z", "glyph": "03"},
        ],
    },
    "stat_callout": {
        "layout": "stat_callout",
        "title": "Stats",
        "metrics": [{"value": "98%", "label": "Ok"}, {"value": "2", "label": "Two"}],
    },
    "timeline": {
        "layout": "timeline",
        "title": "Road",
        "steps": [{"title": "Q4", "detail": "GA"}, {"title": "Q1", "detail": "B"}],
    },
    "comparison": {
        "layout": "comparison",
        "title": "Vs",
        "left": {"heading": "Before", "bullets": ["a"]},
        "right": {"heading": "After", "bullets": ["b"]},
    },
    "table": {
        "layout": "table",
        "title": "Table",
        "table": {"headers": ["A", "B"], "rows": [["1", "2"]]},
    },
    "code": {
        "layout": "code",
        "title": "Code",
        "code": {"language": "python", "text": "x = 1\nprint(x)"},
    },
    "quote": {"layout": "quote", "title": "Q", "quote": "Hi.", "by": "me"},
    "image": {"layout": "image", "title": "Pic", "image": {"src": TINY_PNG, "alt": "dot"}},
    "closing": {"layout": "closing", "title": "Bye", "bullets": ["a"]},
    "hero_cover": {"layout": "hero_cover", "title": "Hero", "subtitle": "Sub"},
    "section_break": {"layout": "section_break", "title": "Break", "number": "03"},
    "stat_grid": {
        "layout": "stat_grid",
        "title": "Grid",
        "metrics": [{"value": "9", "label": "l", "progress": 50}, {"value": "8", "label": "m"}],
    },
    "icon_cards": {
        "layout": "icon_cards",
        "title": "Icons",
        "cards": [{"title": "A", "body": "x", "icon": "rocket"}, {"title": "B", "body": "y"}],
    },
    "process": {
        "layout": "process",
        "title": "Proc",
        "steps": [{"title": "a", "detail": "x"}, {"title": "b"}],
    },
    "image_text": {
        "layout": "image_text",
        "title": "IT",
        "image": {"src": TINY_PNG, "alt": "dot"},
        "bullets": ["one"],
    },
    "chart": {
        "layout": "chart",
        "title": "Ch",
        "chart": {
            "type": "line",
            "categories": ["a", "b"],
            "series": [{"name": "s", "values": [1, 2]}],
        },
    },
}

# Shapes per slide for each layout with the default design (pinned from a run; a change here
# means a layout changed its geometry and should be reviewed).
EXPECTED_SHAPES = {
    "cover": 4,
    "section": 4,
    "bullets": 6,
    "two_column": 9,
    "cards": 14,
    "stat_callout": 13,
    "timeline": 10,
    "comparison": 13,
    "table": 6,
    "code": 8,
    "quote": 6,
    "image": 6,
    "closing": 4,
    "hero_cover": 4,
    "section_break": 4,
    "stat_grid": 15,
    "icon_cards": 13,
    "process": 11,
    "image_text": 7,
    "chart": 7,
}


def _deck(
    design: dict[str, Any] | None, slides: list[dict[str, Any]], theme: str = "executive"
) -> Deck:
    return Deck.model_validate(
        {"title": "Deck", "theme": theme, "design": design, "slides": slides}
    )


def _prs(path: Path) -> Any:
    return Presentation(str(path))


def _named(slide: Any, prefix: str) -> list[Any]:
    return [s for s in slide.shapes if (s.name or "").startswith(prefix)]


def _texts(slide: Any) -> list[str]:
    return [s.text_frame.text for s in slide.shapes if s.has_text_frame and s.text_frame.text]


# ------------------------------------------------------------------ every layout
def test_the_layout_list_is_the_original_thirteen_plus_the_library_layouts() -> None:
    # The library layouts were added after the original thirteen, which never change.
    assert SLIDE_LAYOUTS[:13] == (
        "cover",
        "section",
        "bullets",
        "two_column",
        "cards",
        "stat_callout",
        "timeline",
        "comparison",
        "table",
        "code",
        "quote",
        "closing",
        "image",
    )
    assert SLIDE_LAYOUTS[13:] == (
        "hero_cover",
        "section_break",
        "stat_grid",
        "icon_cards",
        "process",
        "image_text",
        "chart",
    )
    assert set(SAMPLES) == set(SLIDE_LAYOUTS) == set(EXPECTED_SHAPES)


@pytest.mark.parametrize("layout", SLIDE_LAYOUTS)
def test_each_layout_renders_a_verified_pptx_with_its_shape_count(
    tmp_path: Path, layout: str
) -> None:
    result = create_deck(_deck(None, [SAMPLES[layout]]), tmp_path / f"{layout}.pptx")
    assert result.slide_count == 1
    assert result.warnings == [] and result.repairs == []
    slide = _prs(result.path).slides[0]
    assert len(slide.shapes) == EXPECTED_SHAPES[layout], layout
    assert verify_pptx(result.path, expected_slides=1).status == "verified"


# ------------------------------------------------------------------ the design reaches the XML
def _two_decks(tmp_path: Path) -> tuple[Any, Any]:
    slides = [SAMPLES["bullets"], SAMPLES["stat_callout"]]
    kids = _prs(create_deck(_deck(KIDS, slides), tmp_path / "kids.pptx").path)
    navy = _prs(create_deck(_deck(NAVY, slides, theme="midnight"), tmp_path / "navy.pptx").path)
    return kids, navy


def test_a_bright_kids_deck_and_a_dark_navy_deck_look_different_in_the_xml(tmp_path: Path) -> None:
    kids, navy = _two_decks(tmp_path)
    kids_xml, navy_xml = kids.slides[0]._element.xml, navy.slides[0]._element.xml
    assert "FFF7E0" in kids_xml and "0B1F3A" in navy_xml
    assert "0B1F3A" not in kids_xml and "FFF7E0" not in navy_xml
    assert 'typeface="Comic Sans MS"' in kids_xml and 'typeface="Comic Sans MS"' not in navy_xml
    assert 'typeface="Calibri"' in navy_xml and 'typeface="Trebuchet MS"' in kids_xml
    assert 'typeface="Segoe UI"' in navy_xml


def test_the_gradient_background_and_decorations_are_drawn_on_every_slide(tmp_path: Path) -> None:
    kids, navy = _two_decks(tmp_path)
    for slide in kids.slides:
        assert slide.background.fill.type == MSO_FILL_TYPE.GRADIENT
        names = [s.name for s in slide.shapes]
        assert "deco:top_bar" in names and "deco:grid_dots" in names
        assert "deco:corner_circle" in names
    assert all(slide.background.fill.type == MSO_FILL_TYPE.GRADIENT for slide in kids.slides)
    assert all(slide.background.fill.type == MSO_FILL_TYPE.SOLID for slide in navy.slides)
    navy_names = [s.name for s in navy.slides[0].shapes]
    assert "deco:side_bar" in navy_names and "deco:divider_line" in navy_names
    assert "deco:top_bar" not in navy_names


def test_shape_style_and_corner_radius_reach_the_panels(tmp_path: Path) -> None:
    kids, navy = _two_decks(tmp_path)
    kids_card = _named(kids.slides[1], "visual:stat_panel")[0]._element.xml
    assert "outerShdw" in kids_card and "roundRect" in kids_card
    navy_panel = _named(navy.slides[1], "visual:stat_panel")[0]._element.xml
    assert "<a:alpha" in navy_panel  # glass: translucent fill
    assert 'prst="roundRect"' in navy_panel

    square = {"shape_style": "outlined", "corner_radius": 0, "decorations": ["none"]}
    flat = _prs(create_deck(_deck(square, [SAMPLES["stat_callout"]]), tmp_path / "sq.pptx").path)
    panel = _named(flat.slides[0], "visual:stat_panel")[0]._element.xml
    assert 'prst="rect"' in panel and "<a:noFill" in panel


def test_the_slide_number_and_footer_follow_the_design(tmp_path: Path) -> None:
    kids, navy = _two_decks(tmp_path)
    assert 'type="slidenum"' in kids.slides[0]._element.xml
    assert "Kids Club" in " ".join(_texts(kids.slides[0]))
    assert 'type="slidenum"' not in navy.slides[0]._element.xml
    assert "Board" not in " ".join(_texts(navy.slides[0]))  # no custom footer here


def test_text_labels_for_the_section_number_and_the_image_placeholder(tmp_path: Path) -> None:
    deck = _deck(
        None,
        [
            SAMPLES["section"],
            {
                "layout": "two_column",
                "title": "Chart",
                "left": {"heading": "Notes", "bullets": ["a"]},
                "image": {"alt": "revenue chart"},
            },
        ],
    )
    result = create_deck(deck, tmp_path / "ph.pptx")
    prs = _prs(result.path)
    assert "02" in " ".join(_texts(prs.slides[0]))
    assert any("Image placeholder: revenue chart" in t for t in _texts(prs.slides[1]))
    assert result.warnings == []


# ------------------------------------------------------------------ repairs are reported
def test_out_of_range_and_unknown_design_values_are_repaired_and_reported() -> None:
    _theme, style, notes = resolve_deck(
        {
            "palette": {"background": "#FFFFFF", "accent2": "not-a-colour"},
            "corner_radius": "soft",
            "title_scale": 9,
            "shape_style": "shadow",
            "decorations": ["bar", "zzz", "none", "grid_dots"],
            "slide_number": "maybe",
            "footer_text": "x" * 70,
        },
        "executive",
    )
    assert isinstance(style, DeckStyle)
    assert style.corner_radius == 12.0
    assert style.title_scale == 1.4
    assert style.shape_style == "soft-shadow"
    assert style.decorations == ("top_bar", "grid_dots")  # bar -> top_bar; none ignored with others
    assert style.slide_number is True
    assert len(style.footer_text) == 60
    joined = " | ".join(notes)
    for fragment in (
        "corner_radius 'soft' is not a number",
        "title_scale 9 is outside",
        "shape_style 'shadow' is not allowed",
        "decoration 'bar' is not allowed; used 'top_bar'",
        "decoration 'zzz' is not allowed; dropped",
        "slide_number 'maybe' is not on or off",
        "footer_text was cut",
        "accent2 'not-a-colour'",
    ):
        assert fragment in joined, fragment


def test_helvetica_and_comfortable_still_repair_in_a_deck(tmp_path: Path) -> None:
    design = {"heading_font": "Helvetica", "body_font": "Helvetica", "density": "comfortable"}
    result = create_deck(_deck(design, [SAMPLES["bullets"]]), tmp_path / "rep.pptx")
    assert any("Arial" in r for r in result.repairs)
    assert any("comfortable" in r and "normal" in r for r in result.repairs)
    assert 'typeface="Arial"' in _prs(result.path).slides[0]._element.xml


def test_a_flat_text_only_slide_gets_a_warning_and_a_decorated_one_does_not(tmp_path: Path) -> None:
    flat = create_deck(
        _deck({"decorations": ["none"]}, [SAMPLES["bullets"]]), tmp_path / "flat.pptx"
    )
    assert any("no visual element beyond text" in w for w in flat.warnings)
    decorated = create_deck(_deck(None, [SAMPLES["bullets"]]), tmp_path / "dec.pptx")
    assert decorated.warnings == []


def test_every_decoration_name_is_known() -> None:
    assert set(DECORATIONS) == {
        "top_bar",
        "side_bar",
        "corner_circle",
        "grid_dots",
        "divider_line",
        "none",
    }


# ------------------------------------------------------------------ item limits and splitting
def test_a_bullets_slide_over_six_items_splits_and_says_so(tmp_path: Path) -> None:
    slide = {"layout": "bullets", "title": "Long", "bullets": [f"Point {i}" for i in range(8)]}
    result = create_deck(_deck(None, [slide]), tmp_path / "split.pptx")
    assert result.slide_count == 2
    assert any(
        "slide 1 (bullets) held more than 6 items; split into 2 slides" in r for r in result.repairs
    )
    prs = _prs(result.path)
    assert "Long (cont.)" in _texts(prs.slides[1])[0]


def test_each_split_rule_matches_its_layout_limit() -> None:
    assert LAYOUT_ITEM_LIMITS == {
        "bullets": 6,
        "closing": 6,
        "cards": 4,
        "stat_callout": 3,
        "timeline": 6,
        "code": 16,
        "table": 10,
        "stat_grid": 4,
        "icon_cards": 4,
        "process": 5,
        "image_text": 6,
    }
    stats = Deck.model_validate(
        {
            "title": "D",
            "slides": [
                {
                    "layout": "stat_callout",
                    "title": "S",
                    "metrics": [{"value": str(i), "label": "l"} for i in range(4)],
                }
            ],
        }
    )
    checked, notes = check_deck(stats)
    assert [s.layout for s in checked.slides] == ["stat_callout", "stat_callout"]
    assert len(checked.slides[0].metrics) == 3 and len(checked.slides[1].metrics) == 1
    assert notes and "held more than 3 items" in notes[0]

    code = Slide.model_validate(
        {"layout": "code", "title": "C", "code": {"text": "\n".join(f"l{i}" for i in range(20))}}
    )
    parts = halve(code)
    assert parts is not None and len(parts) == 2
    assert parts[0].code is not None and parts[0].code.text.count("\n") == 9


def test_a_slide_within_its_limits_is_left_alone() -> None:
    deck = _deck(None, [SAMPLES["bullets"], SAMPLES["cards"]])
    checked, notes = check_deck(deck)
    assert checked is deck and notes == []


def test_halve_refuses_a_single_item_and_keeps_notes_on_the_first_part() -> None:
    assert (
        halve(Slide.model_validate({"layout": "bullets", "title": "T", "bullets": ["a"]})) is None
    )
    slide = Slide.model_validate(
        {"layout": "bullets", "title": "T", "bullets": ["a", "b", "c"], "notes": "say it"}
    )
    parts = halve(slide)
    assert parts is not None
    first, second = parts
    assert first is not None and first.notes == "say it"
    assert second is not None and second.notes is None and second.title == "T (cont.)"


# ------------------------------------------------------------------ normalizing the AI's slides
def test_layout_aliases_and_new_fields_survive_the_normalizer() -> None:
    deck = deck_from_dict(
        {
            "title": "T",
            "slides": [
                {"layout": "section", "number": "02", "title": "S"},
                {
                    "layout": "cards",
                    "title": "C",
                    "cards": [{"title": "a", "body": "b", "glyph": "01"}],
                },
                {"layout": "code", "title": "K", "code": {"language": "python", "text": "x=1"}},
                {"layout": "title", "title": "Cover"},
                {"layout": "metrics", "title": "M", "metrics": [{"value": "1", "label": "l"}]},
                {
                    "layout": "comparison",
                    "title": "V",
                    "left": {"bullets": ["a"]},
                    "right": {"bullets": ["b"]},
                },
            ],
        }
    )
    assert [s.layout for s in deck.slides] == [
        "section",
        "cards",
        "code",
        "cover",
        "stat_callout",
        "comparison",
    ]
    assert deck.slides[0].number == "02"
    assert deck.slides[1].cards[0].glyph == "01"
    assert deck.slides[2].code is not None and deck.slides[2].code.language == "python"
    assert not any("is not a slide layout" in n for n in deck.normalize_notes)


def test_an_unknown_layout_becomes_bullets_and_is_reported() -> None:
    deck = deck_from_dict(
        {
            "title": "T",
            "slides": [
                {"title": "A", "bullets": ["x"]},
                {"layout": "carousel", "title": "B", "bullets": ["y"]},
            ],
        }
    )
    assert deck.slides[1].layout == "bullets"
    assert any("carousel" in note for note in deck.normalize_notes)


# ------------------------------------------------------------------ design resolution details
def test_the_default_deck_style_is_the_old_look() -> None:
    _theme, style, notes = resolve_deck(None, "executive")
    assert style.decorations == ("divider_line",)
    assert style.background_end is None and style.shape_style == "flat"
    assert style.slide_number is True and style.footer_text == ""
    assert notes == []


def test_a_gradient_end_that_hurts_contrast_is_moved_and_reported() -> None:
    _theme, style, notes = resolve_deck(
        {"palette": {"background": "#FFFFFF", "background_end": "#333333"}}, "executive"
    )
    assert style.background_end is not None and style.background_end != "#333333"
    assert any("background_end #333333 lowered text contrast" in n for n in notes)


# ------------------------------------------------------------------ HTML mirror
def test_the_html_mirror_is_deterministic_one_page_per_slide_and_escapes_text() -> None:
    deck = _deck(
        KIDS, [SAMPLES["bullets"], {"layout": "quote", "title": "<b>bold</b>", "quote": "q"}]
    )
    html_one = render_deck_html(deck)
    assert html_one == render_deck_html(deck)
    assert html_one.count('class="page"') == 2
    assert "<script" not in html_one
    assert "&lt;b&gt;bold&lt;/b&gt;" in html_one and "<b>bold</b>" not in html_one
    assert "linear-gradient(180deg,#FFF7E0" in html_one


def test_the_html_mirror_is_written_to_disk(tmp_path: Path) -> None:
    out = write_deck_html(_deck(None, [SAMPLES["cards"]]), tmp_path / "preview" / "deck.html")
    assert out.is_file() and out.read_text(encoding="utf-8").startswith("<!doctype html>")
